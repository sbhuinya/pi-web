# USAGE
# python run.py
# python run.py --preview 1

# import the necessary packages
from __future__ import print_function

import sys
sys.path.append('/opt/ros/jade/lib/python2.7/dist-packages')

import serial
#import serial.threaded ? https://github.com/pyserial/pyserial/blob/master/examples/at_protocol.py
import threading

from imutils.video.pivideostream import PiVideoStream
import argparse
import imutils
import time
import cv2


from numpy import empty, nan
import os
import sys

import CMT
import numpy as np
import util


ser = serial.Serial('/dev/ttyAMA0', 19200, timeout=0.5)

with open("running-flag.txt", "a") as logfile:
	logfile.write("start")

# construct the argument parse and parse the arguments
ap = argparse.ArgumentParser()

ap.add_argument('inputpath', nargs='?', help='The input path.')
ap.add_argument('--challenge', dest='challenge', action='store_true', help='Enter challenge mode.')
ap.add_argument('--preview', dest='preview', action='store_const', const=True, default=None, help='Force preview')
ap.add_argument('--width', dest='width', type=int, default=240, help='Capture width')
ap.add_argument('--height', dest='height', type=int, default=180, help='Capture height')
ap.add_argument('--maxspeed', dest='maxspeed', type=int, default=20, help='Max robot speed')
ap.add_argument('--minspeed', dest='minspeed', type=int, default=11, help='Min robot speed')
ap.add_argument('--objectwidth', dest='objectwidth', type=int, default=10, help='Rough width of initial object in cm')
ap.add_argument('--focallength', dest='focallength', type=float, default=3.6, help='Camera focal length in mm')
ap.add_argument('--no-scale', dest='estimate_scale', action='store_false', help='Disable scale estimation')
ap.add_argument('--with-rotation', dest='estimate_rotation', action='store_true', help='Enable rotation estimation')
ap.add_argument('--bbox', dest='bbox', help='Specify initial bounding box.')
ap.add_argument('--frameimage', dest='frameimage', help='Specify start frame image.')
ap.add_argument('--pause', dest='pause', action='store_true', help='Pause after every frame and wait for any key.')
ap.add_argument('--output-dir', dest='output', help='Specify a directory for output data.')
ap.add_argument('--tracker', dest='tracker', default='CMT', help='Which tracker to use.')
ap.add_argument('--quiet', dest='quiet', action='store_true', help='Do not show graphical output (Useful in combination with --output-dir ).')
ap.add_argument('--skip', dest='skip', action='store', default=None, help='Skip the first n frames', type=int)

ap.add_argument("-n", "--num-frames", type=int, default=100, help="# of frames to loop over for FPS test")

args = ap.parse_args()

preview = args.preview
if preview is None:
	preview = False
quiet = args.quiet
if quiet is None:
	quiet = False


# motor1 is left, motor2 is right
def motor_speeds(motor1, motor2):
	motor1 = int(motor1)
	motor2 = int(motor2)
	# replace xd with xe in below serial bytes to get acceleration working
	if(motor1 == 0 and motor2 == 0):
		ser.write(b'\xe0') # both stop
		ser.write(chr(motor1))
		ser.write(chr(motor2))
	elif(motor1 >= 0 and motor2 >= 0):
		ser.write(b'\xe9')  # both forward
		ser.write(chr(motor1))
		ser.write(chr(motor2))
	elif(motor1 < 0 and motor2 < 0):
		ser.write(b'\xe6')  # both back
		ser.write(chr(abs(motor1)))
		ser.write(chr(abs(motor2)))
	elif(motor1 > 0 and motor2 <= 0):
		ser.write(b'\xe5')  # turn right
		ser.write(chr(abs(motor1)))
		ser.write(chr(abs(motor2)))
	elif(motor1 < 0 and motor2 >= 0):
		ser.write(b'\xeA')  # turn left
		ser.write(chr(abs(motor1)))
		ser.write(chr(abs(motor2)))
	else:
		if not quiet:
			print("Unknown motor command!")



#def PolyArea(x, y):
#	return 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))

def PolygonArea(corners):
	n = len(corners)  # of corners
	area = 0.0
	for i in range(n):
		j = (i + 1) % n
		area += corners[i][0] * corners[j][1]
		area -= corners[j][0] * corners[i][1]
	area = abs(area) / 2.0
	return area

previous_motor_speed = [0,0]
current_motor_speed = [0,0]
initial_object_center_position = [0,0]
initial_object_size = 0
current_object_center_position = [0,0]
current_object_size = 0

if(args.tracker == 'CMT'):
	CMT = CMT.CMT()
	CMT.estimate_scale = args.estimate_scale
	CMT.estimate_rotation = args.estimate_rotation
else:
	CMT = cv2.Tracker_create(args.tracker)

if args.pause:
	pause_time = 0
else:
	pause_time = 10

if args.output is not None:
	if not os.path.exists(args.output):
		os.mkdir(args.output)
	elif not os.path.isdir(args.output):
		raise Exception(args.output + ' exists, but is not a directory')

# Clean up
cv2.destroyAllWindows()
# write a stop signal to serial so our robot doesn't take off when we start
motor_speeds(0, 0)
time.sleep(0.5)
# motor_speeds(12, 12)  # forward
# time.sleep(1)
# motor_speeds(-12, -12)  # back
# time.sleep(1)
# motor_speeds(0, 12)  # turn left with one wheel
# time.sleep(1)
# motor_speeds(-12, 12)  # turn left with spin
# time.sleep(1)
# motor_speeds(12, 0)  # turn right with one wheel
# time.sleep(1)
# motor_speeds(12, -12)  # turn right with spin
# time.sleep(1)



# created a *threaded *video stream, allow the camera sensor to warmup,
# and start the FPS counter
if not quiet:
	print("Starting camera")
vs = PiVideoStream((args.width, args.height)).start()
time.sleep(2.0)


frame_counter = 0
frame_init_at = 20  # start processing once we've read this many frames, as it can take some time to get the cam warmed up
frame_limit = 10000

object_size_percentage_average = []
object_size_percentage_limit = 12
kfilterfactor = 0.5

# 240 x 180
# 370 x 240
# image_resize = 300

tl = 0
br = 0

# read a bunch of frames from the camera to start
while frame_counter < frame_limit and os.path.isfile("running-flag.txt"):

	# print(time.time())

	frame = vs.read()
	# frame = imutils.resize(frame, width=image_resize)

	frame_counter += 1
	# print("frame " + str(frame_counter))
	if (frame_counter == frame_init_at):
		# Read first frame
		if args.frameimage is not None:
			frame = cv2.imread(args.frameimage)
			frame = imutils.resize(frame, width=args.width)
			with open("log.txt", "a") as logfile:
				logfile.write("Using frame image: " + str(args.frameimage) + "\n")

		im_gray0 = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
		im_draw = np.copy(frame)

		if args.bbox is not None:
			# Try to disassemble user specified bounding box
			values = args.bbox.split(',')
			try:
				values = [int(v) for v in values]
			except:
				raise Exception('Unable to parse bounding box')
			if len(values) != 4:
				raise Exception('Bounding box must have exactly 4 elements')
			bbox = np.array(values)

			# Convert to point representation, adding singleton dimension
			bbox = util.bb2pts(bbox[None, :])

			# Squeeze
			bbox = bbox[0, :]

			tl = bbox[:2]
			br = bbox[2:4]
		else:
			# print("bbox arg is required")
			# exit()
			(tl, br) = util.get_rect(im_draw)

		if not quiet:
			print('using', tl, br, 'as init bb')

		initial_object_center_position = [(tl[0] + br[0]) / 2, (tl[1] + br[1]) / 2]
		current_object_center_position = [(tl[0] + br[0]) / 2, (tl[1] + br[1]) / 2]
		initial_object_size = PolygonArea([tl, (br[0], tl[1]), br, (tl[0], br[1])])
		with open("log.txt", "a") as logfile:
			s = "Initial Center: " + str(initial_object_center_position[0]) + " , " + str(initial_object_center_position[1])
			if not quiet:
				print(s)
			logfile.write(s + "\n")
			s = "Initial Size: " + str(initial_object_size)
			if not quiet:
				print(s)
			logfile.write(s + "\n")

		tic = time.time()
		if args.tracker == 'CMT':
			CMT.initialise(im_gray0, tl, br)
		else:
			CMT.init(frame, (tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]))
		toc = time.time()

		with open("log.txt", "a") as logfile:
			s = "Tracker Init Time: " + str(args.tracker) + ": " + str(toc - tic)
			if not quiet:
				print(s)
			logfile.write(s + "\n")


	elif (frame_counter > frame_init_at):
		im_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
		if preview or args.output is not None:
			im_draw = np.copy(frame)

		tic = time.time()
		has_result = False
		if args.tracker == 'CMT':
			CMT.process_frame(im_gray)
			if CMT.has_result:
				has_result = True

		else:
			has_result, newbox = CMT.update(frame)
			print(newbox)
		toc = time.time()

		# Draw updated estimate
		if has_result:

			current_object_center_position = [(CMT.tl[0] + CMT.tr[0]) / 2, (CMT.tl[1] + CMT.bl[1]) / 2]
			# current_object_center_position_new = [(CMT.tl[0] + CMT.tr[0]) / 2, (CMT.tl[1] + CMT.bl[1]) / 2]
			# current_object_center_position[0] = (current_object_center_position_new[0] * kfilterfactor) + (current_object_center_position[0] * (1.0 - kfilterfactor))
			# current_object_center_position[1] = (current_object_center_position_new[1] * kfilterfactor) + (current_object_center_position[1] * (1.0 - kfilterfactor))
			# current_object_size = PolygonArea([CMT.tl, CMT.tr, CMT.br, CMT.bl])
			current_object_size_new = PolygonArea([CMT.tl, CMT.tr, CMT.br, CMT.bl])
			current_object_size = ( current_object_size_new * kfilterfactor ) + ( current_object_size * (1.0 - kfilterfactor))
			# work out averages / kfilter

			if initial_object_size == current_object_size and set(current_object_center_position) == set(
					initial_object_center_position):
				# object hasn't moved
				if not quiet:
					print("object hasn't moved")
			else:
				# object has moved
				# work out how far it has moved ( left/right and forward/back ) then calculate what speed we have to move the motors
				# we do this by working out how much of a percentage the object has moved to the left/right

				#movement_multiplier = 0.01
				# current_object_center_position[0] - initial_object_center_position[0]
				left_right_diff = current_object_center_position[0] - (args.width / 2)
				speed = np.interp(abs(left_right_diff), [0, 100], [args.minspeed, args.maxspeed])

				object_size_percent1 = round( (current_object_size / initial_object_size) * 100 )
				foo1 = (int(CMT.tr[0]) - int(CMT.tl[0]))
				foo2 =(int(br[0]) - int(tl[0]))
				object_size_percent2 = round( ( (float(CMT.tr[0]) - float(CMT.tl[0])) / (float(br[0]) - float(tl[0])) ) * 100 )

				forward_back = int(np.interp(abs(object_size_percent1), [0, 100], [args.maxspeed, 0]))


				with open("log.txt", "a") as logfile:
					s = "Current Pos: " + str(current_object_center_position[0]) + " , " + str(current_object_center_position[1]) + " " + str(int(left_right_diff)) + "% from center, speed: " + str(speed)
					if not quiet:
						print(s)
					logfile.write(s + "\n")
					s = "Current Size: " + str(current_object_size) + ", " + str(object_size_percent1) + "%, " + str(object_size_percent2) + "% " + str(foo1) + "/" + str(foo2) + " " + str(CMT.tr[0]) + ":" + str(CMT.tl[0]) + ":" + str(br[0]) + ":" + str(tl[0])
					if not quiet:
						print(s)
					logfile.write(s + "\n")
					s = "Forward Back Speed: " + str(forward_back)
					if not quiet:
						print(s)
					logfile.write(s + "\n")

				if left_right_diff < -10:
					# object has moved left more than 10%, turn the robot left,
					# increase speed of right motors, decrease speed of left motors
					#current_motor_speed[0] -= abs(left_right_diff) * movement_multiplier
					#current_motor_speed[1] += abs(left_right_diff) * movement_multiplier
					current_motor_speed[0] = forward_back + (speed * -1)
					current_motor_speed[1] = forward_back + (speed * 1)

				elif left_right_diff > 10:
					# object has moved right, turn the robot right
					# increase speed of left motors, decrease speed of right motors
					#current_motor_speed[0] += abs(left_right_diff) * movement_multiplier
					#current_motor_speed[1] -= abs(left_right_diff) * movement_multiplier
					current_motor_speed[0] = forward_back + (speed * 1)
					current_motor_speed[1] = forward_back + (speed * -1)


				elif forward_back > 5:
					current_motor_speed[0] = forward_back
					current_motor_speed[1] = forward_back
				else:
					# object hasn't moved left/right yet, maybe forward/back
					# todo: move forward/back with object size and ultrasound sensor
					current_motor_speed[0] = 0
					current_motor_speed[1] = 0

				with open("log.txt", "a") as logfile:
					s = "Motor: " + str(current_motor_speed[0]) + " , " + str(current_motor_speed[1])
					if not quiet:
						print(s)
					logfile.write(s + "\n")

				# check speed limits.
				if current_motor_speed[0] > args.maxspeed:
					current_motor_speed[0] = args.maxspeed
				if current_motor_speed[1] > args.maxspeed:
					current_motor_speed[1] = args.maxspeed
				if current_motor_speed[0] < ( args.maxspeed * -1 ):
					current_motor_speed[0] = ( args.maxspeed * -1 )
				if current_motor_speed[1] < ( args.maxspeed * -1 ):
					current_motor_speed[1] = ( args.maxspeed * -1 )

				send_motor_speed = [int(current_motor_speed[0]), int(current_motor_speed[1])]
				if (set(previous_motor_speed) != set(send_motor_speed)):
					with open("log.txt", "a") as logfile:
						s = "Send Motor: " + str(send_motor_speed[0]) + " , " + str(send_motor_speed[1])
						if not quiet:
							print(s)
						logfile.write(s + "\n")

					motor_speeds(send_motor_speed[0], send_motor_speed[1])
					previous_motor_speed = send_motor_speed

			# print(np.array((CMT.tl, CMT.tr, CMT.br, CMT.bl, CMT.tl)))

			if preview or args.output is not None:
				cv2.line(im_draw, CMT.tl, CMT.tr, (255, 0, 0), 4)
				cv2.line(im_draw, CMT.tr, CMT.br, (255, 0, 0), 4)
				cv2.line(im_draw, CMT.br, CMT.bl, (255, 0, 0), 4)
				cv2.line(im_draw, CMT.bl, CMT.tl, (255, 0, 0), 4)

			if args.output is not None:
				# Original image
				cv2.imwrite('{0}/input_{1:08d}.png'.format(args.output, frame_counter), frame)
				# Output image
				cv2.imwrite('{0}/output_{1:08d}.png'.format(args.output, frame_counter), im_draw)

				# Keypoints
				with open('{0}/keypoints_{1:08d}.csv'.format(args.output, frame_counter), 'w') as f:
					f.write('x y\n')
					np.savetxt(f, CMT.tracked_keypoints[:, :2], fmt='%.2f')

				# Outlier
				with open('{0}/outliers_{1:08d}.csv'.format(args.output, frame_counter), 'w') as f:
					f.write('x y\n')
					np.savetxt(f, CMT.outliers, fmt='%.2f')

				# Votes
				with open('{0}/votes_{1:08d}.csv'.format(args.output, frame_counter), 'w') as f:
					f.write('x y\n')
					np.savetxt(f, CMT.votes, fmt='%.2f')

				# Bounding box
				with open('{0}/bbox_{1:08d}.csv'.format(args.output, frame_counter), 'w') as f:
					f.write('x y\n')
					# Duplicate entry tl is not a mistake, as it is used as a drawing instruction
					np.savetxt(f, np.array((CMT.tl, CMT.tr, CMT.br, CMT.bl, CMT.tl)), fmt='%.2f')

			if preview:
				util.draw_keypoints(CMT.tracked_keypoints, im_draw, (255, 255, 255))
				# this is from simplescale
				util.draw_keypoints(CMT.votes[:, :2], im_draw)  # blue
				util.draw_keypoints(CMT.outliers[:, :2], im_draw, (0, 0, 255))

				# check to see if the frame should be displayed to our screen
				cv2.imshow("Frame", im_draw)
				key = cv2.waitKey(2) & 0xFF

		else:
			motor_speeds(0, 0)


		# Remember image
		# im_prev = im_gray
	else:
		# check to see if the frame should be displayed to our screen
		if preview:
			cv2.imshow("Frame", frame)
			key = cv2.waitKey(1) & 0xFF

motor_speeds(0, 0)

# do a bit of cleanup
cv2.destroyAllWindows()
vs.stop()
time.sleep(2.0)
ser.close()
print("finished")



