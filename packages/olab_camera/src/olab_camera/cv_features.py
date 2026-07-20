"""Composed CV feature classes (ArUco, calibration, barcode, face detection,
timelapse, ROI tracking, Ultralytics YOLO) used by `Camera` and its subclasses.

Each class takes the parent `Camera` instance (`camObject`) as a constructor
argument and is composed onto it (e.g. `self.aruco[idName] = _Aruco(self, ...)`)
rather than inherited, so none of these need to import `Camera` itself.
"""

import os
import threading
import time
from collections import deque

import cv2
import numpy as np

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .streaming import _make_fps_dict


class _Aruco():
	"""
	Internal class for ArUco marker detection and tracking.

	This class runs in a separate thread to detect ArUco/AprilTag markers in camera frames.
	Detected markers are stored in a deque and can be drawn on the video stream.

	Attributes:
		camObject: Parent Camera object
		idName (str): ArUco dictionary name (e.g., 'DICT_APRILTAG_36h11')
		res_rows (int): Height for marker detection
		res_cols (int): Width for marker detection
		fps_target (float): Target detection framerate in Hz
		calcRotations (bool): Whether to calculate marker rotations
		postFunction (callable): Callback function after each detection
		postFunctionArgs (dict): Arguments for postFunction
		config (dict): Drawing configuration (colors, line thickness, etc.)
		ids_of_interest (list): List of specific marker IDs to track (None for all)
		deque (deque): Most recent detection results {'ids', 'corners', 'centers', 'rotations'}

	Methods:
		start(): Start the detection thread
		stop(): Stop the detection thread and cleanup
	"""
	def __init__(self, camObject, idName, res_rows, res_cols, fps_target, calcRotations, postFunction, postFunctionArgs, configDict, ids_of_interest):
		try:
			self.camObject = camObject  # This is the parent!
								
			self.idName   = idName
			self.decorationID = None
			
			self.res_rows = res_rows
			self.res_cols = res_cols		
			self.resolution = f'{res_cols}x{res_rows}'

			self.fps_target  = fps_target		# Hz
			self.threadSleep = 1/fps_target		# seconds
			
			self.calcRotations = calcRotations
				
			self.postFunctionArgs = postFunctionArgs
			self.postFunctionArgs['idName'] = idName
			if (postFunction is None):
				self.postFunction = olab_utils._passFunction
			else:
				self.postFunction = postFunction

			self.config = configDict
			# self.color = color
			
			self.ids_of_interest = ids_of_interest
			
			self.fps = _make_fps_dict(recheckInterval=5)

			self.deque = deque(maxlen=1)
			self.deque.append({'ids': None, 'corners': [], 'centers': [], 'rotations': []})

			(major, minor, sub) = cv2.__version__.split(".")[:3]
			if ((int(major) >= 4) and (int(minor) >= 7)):
				self.cv2dict   = cv2.aruco.getPredefinedDictionary(olab_utils.ARUCO_DICT[idName]['dict'])
				self.cv2params = cv2.aruco.DetectorParameters()
			else:
				# This is old:
				self.cv2dict   = cv2.aruco.Dictionary_get(olab_utils.ARUCO_DICT[idName]['dict'])
				self.cv2params = cv2.aruco.DetectorParameters_create()
					
			self.isThreadActive = False

		except Exception as e:
			self.camObject.logger.log(f'Error in aruco init: {e}.', severity=olab_utils.SEVERITY_ERROR)
		

	def _decorate(self, img, **kwargs):
		olab_utils.arucoDrawDetections(img, self.deque[0]['corners'],
									        self.deque[0]['ids'], 
									        self.deque[0]['centers'], 
									        self.deque[0]['rotations'], self.config)		
		
	def _thread_Aruco(self):
		'''
		THIS IS A THREAD
		rate is in [Hz] (frames/second)
		self.camObject is the parent (from Camera).
		We are in self.camObject.aruco[idName] 
		'''
		self.isThreadActive = True

		while self.camObject.camOn:
			try:	
				timeNow = time.time()
							
				# FIXME -- It would be nice to cut out the `if` statements...
				
				# Throttle things if we're going faster than capture speed
				if (self.fps.actual >= self.camObject.fps['capture'].actual):
					with self.camObject.condition:
						self.camObject.condition.wait(1)   # added a timeout, just to keep from getting permanently stuck here
				
				# FIXME -- Why are we calculating this each time (in loop)?
				# We should only set resOption when properties change.
				img_x_y  = (self.res_cols, self.res_rows)
				orig_x_y = (self.camObject.res_cols, self.camObject.res_rows)
				if (img_x_y == orig_x_y):
					resOption = None
				else:
					resOption = img_x_y
				
				img = self.camObject.getFrameCopy(colorOption='gray', resOption=resOption)
								
				# `corners` will be of same scale as original (captured) image
				(corners, ids, rejected, centers, rotations) = olab_utils.arucoDetectMarkers(img, 
																		 self.cv2dict, 
																		 self.cv2params,
																		 img_x_y  = img_x_y,
																		 orig_x_y = orig_x_y)
	
				'''
				centers = []
				rotations = []
				for i in range(0, len(corners)):
					# Find midpoint, using corner points 1 (NE) and 3 (SW)
					# NOTE:  These are not int coordinates.
					mp = ((corners[i][0][3][0] + corners[i][0][1][0])/2, 
						  (corners[i][0][3][1] + corners[i][0][1][1])/2) 
					centers.append(mp)
					if (self.calcRotations):					
						# point 0 is top left, 3 is bottom left.  x increases to right, y increases down
						x = corners[i][0][0][0] - corners[i][0][3][0]
						y = corners[i][0][0][1] - corners[i][0][3][1]
						theta = math.atan2(x, -y)  # NOTE:  This is in [radians]
					
						rotations.append(theta) 
						print(np.rad2deg(theta))
				'''		
				'''
				if (len(corners) > 0):
					print(corners, centers, rotations)
				'''
									
				# Add detection info to deque:
				# print(len(self.deque))
				if (self.ids_of_interest is None):
					self.deque.append({'ids': ids, 'corners': corners, 'centers': centers, 'rotations': rotations})
				else:
					indices = olab_utils.arucoFindTagIndicesList(ids, self.ids_of_interest)
					if (len(indices)):
						self.deque.append({'ids': ids[indices], 'corners': corners[indices], 'centers': centers[indices], 'rotations': rotations[indices]})
					else:
						self.deque.append({'ids': [], 'corners': [], 'centers': [], 'rotations': []})
						
					
					'''
					# self.camObject.logger.log(f'{ids=}, {corners=}, {type(ids)}, {type(corners)}', severity=olab_utils.SEVERITY_DEBUG)	
					if (ids is None):
						self.deque.append({'ids': [], 'corners': [], 'centers': [], 'rotations': []})
					else:
						indices = [i for i in range(len(ids)) if ids[i] in self.ids_of_interest]
						if (len(indices) > 0):
							self.deque.append({'ids': [ids[i] for i in indices], 'corners': [corners[i] for i in indices], 'centers': [centers[i] for i in indices], 'rotations': [rotations[i] for i in indices]})

						else:
							self.deque.append({'ids': [], 'corners': [], 'centers': [], 'rotations': []})
					'''
					
				# Do some post-processing:
				self.postFunction(self.postFunctionArgs)
								
				self.camObject.calcFramerate(self.fps, 'aruco')
				
				self.camObject.reachback_pubCamStatus()
			except Exception as e:
				self.stop()
				self.camObject.logger.log(f'Error in Aruco {self.idName} thread: {e}', severity=olab_utils.SEVERITY_ERROR)				
				break
	
			if (not self.isThreadActive):
				self.stop()
				self.camObject.logger.log(f'Stopping ArUco {self.idName} thread - no longer active.', severity=olab_utils.SEVERITY_INFO)
				break
	
			# Simplified version of rospy.sleep
			delta = max(0, timeNow + self.threadSleep - time.time())
			if (delta > 0):
				time.sleep(delta)
		
		# If while loop stops, shut down aruco:
		self.stop()	


	def start(self):
		try:			
			self.camObject.logger.log(f'Starting ArUco {self.idName} thread at {self.fps_target} fps', severity=olab_utils.SEVERITY_INFO)
			
			arucoThread = threading.Thread(target=self._thread_Aruco, args=())
			arucoThread.daemon = True    # Allows your main script to exit, shutting down this thread, too.
			arucoThread.start()

			# Add to decorations deque
			# FIXME -- Maybe we don't necessarily want to decorate?
			self.decorationID = int(time.time()*1000)
			self.camObject.dec['dequeAdd'].append({'function': self._decorate, 'idName': self.idName, 'decorationID': self.decorationID})

		except Exception as e:
			self.camObject.logger.log(f'Error in aruco start: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def stop(self):
		try:
			if (self.idName in self.camObject.aruco):
				'''
				# Remove idName from self.camObject.decorations['aruco']
				if (self.idName in self.camObject.decorations['aruco']):
					self.camObject.decorations['aruco'].remove(self.idName)
				'''	
				self.camObject.dec['dequeRemove'].append(self.decorationID)	

				self.camObject.logger.log(f'Stopping ArUco {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)
				
				self.isThreadActive = False
				self.deque.clear()					
			else:
				self.camObject.logger.log(f'In stop, aruco {self.idName} dictionary is not defined', severity=olab_utils.SEVERITY_ERROR)
		except Exception as e:
			self.camObject.logger.log(f'Error in aruco stop: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def edit(self, res_rows=None, res_cols=None, fps_target=5, postFunction=None, color=None):
		# Note:  `color=None` now implies "do not change color".
		try:
			# change fps_target, resolution, (function?)
			if ((res_rows is not None) and (res_cols is not None)):
				if ((res_cols, res_rows) != (self.res_cols, self.res_rows)):
					(self.res_cols, self.res_rows) = (int(res_cols), int(res_rows))
					self.resolution = f'{res_cols}x{res_rows}'
			
			if (fps_target != self.fps_target):
				self.fps_target = int(fps_target)
				self.threadSleep = 1/self.fps_target
				
			if (postFunction is not None):
				self.postFunction = postFunction
				
			if (color is not None):
				self.color = color
		except Exception as e:
			self.camObject.logger.log(f'Error in aruco edit: {e}.', severity=olab_utils.SEVERITY_ERROR)


class _Calibrate():
	"""Internal camera calibration feature class using checkerboard pattern detection.

	This class performs camera calibration by detecting checkerboard patterns in captured
	frames and computing the camera's intrinsic matrix and distortion coefficients.
	Calibration runs in a separate thread and collects images at specified intervals
	until the required number of valid detections is obtained or a timeout occurs.

	Attributes:
		camObject: Parent Camera instance managing this calibration feature.
		idName (str): Unique identifier for this calibration instance.
		res_rows (int): Target vertical resolution in pixels.
		res_cols (int): Target horizontal resolution in pixels.
		numImages (int): Number of checkerboard detections required for calibration.
		timeoutSec (float): Maximum time in seconds to wait for calibration completion.
		pattern_size (tuple): Checkerboard dimensions as (cols, rows) of internal corners.
		square_size (float): Physical size of checkerboard squares in world units.
		postFunction (callable): Callback function invoked after calibration completes.
		deque (collections.deque): Thread-safe storage for latest detection results.
		isThreadActive (bool): Flag indicating if calibration thread is running.

	Key Methods:
		start(): Initiates calibration thread and begins collecting checkerboard images.
		stop(): Terminates calibration thread and cleans up resources.
	"""
	def __init__(self, camObject, idName, res_rows, res_cols, secBetweenImages, numImages, timeoutSec, pattern_size, square_size, postFunction):
		"""Initialize camera calibration feature.

		Args:
			camObject: Parent Camera instance.
			idName (str): Unique identifier for this calibration.
			res_rows (int): Target vertical resolution in pixels.
			res_cols (int): Target horizontal resolution in pixels.
			secBetweenImages (float): Time in seconds between image captures.
			numImages (int): Number of valid checkerboard detections needed.
			timeoutSec (float): Maximum calibration duration in seconds.
			pattern_size (tuple): Checkerboard dimensions (cols, rows) of internal corners.
			square_size (float): Physical size of squares in world units.
			postFunction (callable): Callback executed after calibration with results.
		"""
		try:
			self.camObject = camObject  # This is the parent!
								
			self.idName   = idName
			self.decorationID = None
			
			self.res_rows = res_rows
			self.res_cols = res_cols		
			self.resolution = f'{res_cols}x{res_rows}'

			# self.fps_target  = fps_target		# Hz
			# self.threadSleep = 1/fps_target		# seconds
			# self.fps = _make_fps_dict(recheckInterval=5)
			self.threadSleep = secBetweenImages		# seconds

				
			self.numImages    = numImages
			self.timeoutSec   = timeoutSec
			self.pattern_size = pattern_size
			self.square_size  = square_size

			if (postFunction is None):
				self.postFunction = olab_utils._passFunction
			else:
				self.postFunction = postFunction
			
			self.deque = deque(maxlen=1)
			self.deque.append({'checkerboard': None, 'corners': None, 'count': 0, 'img_x_y': (), 'orig_x_y': ()})
								
			self.isThreadActive = False			
		except Exception as e:
			self.camObject.logger.log(f'Error in barcode init: {e}.', severity=olab_utils.SEVERITY_ERROR)
			
	def _decorate(self, img, **kwargs):
		olab_utils.decorateCalibrate(img, 
									 self.deque[0]['checkerboard'], 
									 self.deque[0]['corners'], 
									 self.deque[0]['count'], 
									 self.deque[0]['img_x_y'], 
									 self.deque[0]['orig_x_y'], addText=True)

	def _thread_Calibrate(self):
		# See https://github.com/opencv/opencv/blob/master/samples/python/calibrate.py
		# See https://learnopencv.com/camera-calibration-using-opencv/
		
		# Defining the dimensions of checkerboard
		CHECKERBOARD = self.pattern_size   # e.g., (6,9)
		
		# FIXME -- What is this???
		criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
		
		# Create vector to store vectors of 3D points for each checkerboard image
		objpoints = []
		# Create vector to store vectors of 2D points for each checkerboard image
		imgpoints = [] 
		
		# Define the world coordinates for 3D points
		objp = np.zeros((1, CHECKERBOARD[0] * CHECKERBOARD[1], 3), np.float32)
		objp[0,:,:2] = np.mgrid[0:CHECKERBOARD[0], 0:CHECKERBOARD[1]].T.reshape(-1, 2)
		objp *= self.square_size
		
		# Initialize return values
		success     = False
		mtx         = []
		dist        = []
		total_error = -1
		mean_error  = -1

		img_x_y  = (self.res_cols, self.res_rows)
		orig_x_y = (self.camObject.res_cols, self.camObject.res_rows)
		if (img_x_y == orig_x_y):
			resOption = None
		else:
			resOption = img_x_y

		timeStart = time.time()
			
		self.isThreadActive = True

		try:
			while self.isThreadActive:
				# We should be going very slowly...no need to wait for next frame.
				timeNow = time.time()
								
				gray = self.camObject.getFrameCopy(colorOption='gray', resOption=resOption)

				# Find the chess board corners
				# If desired number of corners are found in the image then ret = true
				ret, corners = cv2.findChessboardCorners(gray, CHECKERBOARD, cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_FAST_CHECK + cv2.CALIB_CB_NORMALIZE_IMAGE)
				 
				"""
				If desired number of corners are detected,
				refine the pixel coordinates and display 
				them on the images of checker board
				"""
				if ret == True:
					objpoints.append(objp)
					# refining pixel coordinates for given 2d points.
					corners2 = cv2.cornerSubPix(gray, corners, (11,11),(-1,-1), criteria)
					 
					imgpoints.append(corners2)
					
					# Draw and display the corners
					# FIXME -- Need to decorate
					# img = cv2.drawChessboardCorners(img, CHECKERBOARD, corners2, ret)
					# 

					# Add detection info to deque:
					self.deque.append({'checkerboard': CHECKERBOARD, 'corners': corners2, 'count': len(imgpoints), 'img_x_y': img_x_y, 'orig_x_y': orig_x_y})

					# Reset timer
					timeStart = time.time()
				else:
					self.deque.append({'checkerboard': None, 'corners': None, 'count': len(imgpoints), 'img_x_y': img_x_y, 'orig_x_y': orig_x_y})
										
								
				# Do some post-processing:
				# self.postFunction()
				
				# Simplified version of rospy.sleep
				delta = max(0, timeNow + self.threadSleep - time.time())
				if (delta > 0):
					time.sleep(delta)

				self.isThreadActive = self.camObject.camOn 
				if ((time.time() - timeStart >= self.timeoutSec) or (len(imgpoints) >= self.numImages)):
					self.isThreadActive = False
					
					
			if (len(imgpoints) >= self.numImages):
				"""
				Perform camera calibration by 
				passing the value of known 3D points (objpoints)
				and corresponding pixel coordinates of the 
				detected corners (imgpoints)
				"""
				ret, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(objpoints, imgpoints, gray.shape[::-1], None, None)
				
				# self.camObject.logger.log(f'Error in barcode init: {e}.', severity=olab_utils.SEVERITY_ERROR)
				print("Camera matrix : \n")
				print(mtx)
				print("dist : \n")
				print(dist)
				print("rvecs : \n")
				print(rvecs)
				print("tvecs : \n")
				print(tvecs)
				print(f"Resolution: {self.resolution}") 

				# Check Reprojection Error.
				# See bottom of https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html
				total_error = 0
				for i in range(len(objpoints)):
					imgpoints2, _ = cv2.projectPoints(objpoints[i], rvecs[i], tvecs[i], mtx, dist)
					error = cv2.norm(imgpoints[i], imgpoints2, cv2.NORM_L2)/len(imgpoints2)
					total_error += error
				print( "\ntotal error: {}".format(total_error) )
				mean_error = total_error/len(objpoints)
				print( "mean error: {}".format(mean_error) )

				success = True

			self.stop()
			
		except Exception as e:
			self.stop()
			self.camObject.logger.log(f'Error in calibration {self.idName} thread: {e}', severity=olab_utils.SEVERITY_ERROR)				

		finally:
			self.postFunction(success=success, res=f'{self.res_cols}x{self.res_rows}', mtx=mtx, dist=dist, total_error=total_error, mean_error=mean_error)

			
	def start(self):
		"""Start calibration thread to collect checkerboard images.

		Launches a daemon thread that captures images at regular intervals, detects
		checkerboard patterns, and computes calibration parameters. The thread
		terminates when sufficient valid detections are collected or timeout occurs.
		Automatically registers a decoration function to visualize detected corners.
		"""
		try:
			'''
			# Add idName to self.decorations['calibrate']
			if (self.idName not in self.camObject.decorations['calibrate']):
				self.camObject.decorations['calibrate'].append(self.idName)
			'''
			# Add to decorations deque
			# FIXME -- Maybe we don't necessarily want to decorate?
			self.decorationID = int(time.time()*1000)
			self.camObject.dec['dequeAdd'].append({'function': self._decorate, 'idName': self.idName, 'decorationID': self.decorationID})

			self.camObject.logger.log(f'Starting calibration {self.idName} thread at {self.threadSleep} sec betw images', severity=olab_utils.SEVERITY_INFO)

			calThread = threading.Thread(target=self._thread_Calibrate, args=())
			calThread.daemon = True    # Allows your main script to exit, shutting down this thread, too.
			calThread.start()
		except Exception as e:
			self.camObject.logger.log(f'Error in calibrate start: {e}.', severity=olab_utils.SEVERITY_ERROR)
		
	def stop(self):
		"""Stop calibration thread and clean up resources.

		Signals the calibration thread to terminate, removes associated decorations,
		and clears the detection deque. Safe to call even if calibration has already
		completed or was never started.
		"""
		try:
			if (self.idName in self.camObject.calibrate):
				'''
				# Remove idName from self.camObject.decorations['calibrate']
				if (self.idName in self.camObject.decorations['calibrate']):
					self.camObject.decorations['calibrate'].remove(self.idName)
				'''
				self.camObject.dec['dequeRemove'].append(self.decorationID)

				self.camObject.logger.log(f'Stopping calibrate {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)

				self.isThreadActive = False
				self.deque.clear()
			else:
				self.camObject.logger.log(f'In stop, calibrate {self.idName} name is not defined', severity=olab_utils.SEVERITY_ERROR)
		except Exception as e:
			self.camObject.logger.log(f'Error in calibrate stop: {e}.', severity=olab_utils.SEVERITY_ERROR)	
		
		
class _Barcode():
	"""Internal barcode detection feature class using pyzbar library.

	This class detects and decodes 1D and 2D barcodes (including QR codes) in camera
	frames using the pyzbar library. Detection runs continuously in a separate thread
	at a specified frame rate, with results stored in a thread-safe deque.

	Attributes:
		camObject: Parent Camera instance managing this barcode detector.
		idName (str): Unique identifier for this barcode detection instance.
		res_rows (int): Target vertical resolution in pixels.
		res_cols (int): Target horizontal resolution in pixels.
		fps_target (float): Target detection rate in frames per second.
		postFunction (callable): Callback function invoked after each detection cycle.
		postFunctionArgs (dict): Arguments passed to the post-processing callback.
		color (tuple): RGB color for visualization of detected barcodes.
		deque (collections.deque): Thread-safe storage for latest detection results.
		fps (dict): Frame rate tracking metrics for this detector.
		isThreadActive (bool): Flag indicating if detection thread is running.

	Key Methods:
		start(): Launches barcode detection thread.
		stop(): Terminates detection thread and cleans up resources.
	"""
	def __init__(self, camObject, idName, res_rows, res_cols, fps_target, postFunction, postFunctionArgs, color):
		"""Initialize barcode detection feature.

		Args:
			camObject: Parent Camera instance.
			idName (str): Unique identifier for this barcode detector.
			res_rows (int): Target vertical resolution in pixels.
			res_cols (int): Target horizontal resolution in pixels.
			fps_target (float): Target detection rate in Hz.
			postFunction (callable): Callback executed after each detection cycle.
			postFunctionArgs (dict): Arguments for post-processing callback.
			color (tuple): RGB color tuple for barcode visualization.
		"""
		try:
			# https://pypi.org/project/pyzbar/
			from pyzbar import pyzbar
			self.pyzbar = pyzbar
			
			self.camObject = camObject  # This is the parent!
								
			self.idName   = idName
			self.decorationID = None
			
			self.res_rows = res_rows
			self.res_cols = res_cols		
			self.resolution = f'{res_cols}x{res_rows}'

			self.fps_target  = fps_target		# Hz
			self.threadSleep = 1/fps_target		# seconds
				
			self.postFunctionArgs = postFunctionArgs
			self.postFunctionArgs['idName'] = idName	
			if (postFunction is None):
				self.postFunction = olab_utils._passFunction
			else:
				self.postFunction = postFunction

			self.color = color
			
			self.fps = _make_fps_dict(recheckInterval=5)

			self.deque = deque(maxlen=1)
			self.deque.append({'data': [], 'codeTypes': [], 'qualities': [], 'corners': [], 'color': self.color})
								
			self.isThreadActive = False

		except Exception as e:
			self.camObject.logger.log(f'Error in barcode init: {e}.', severity=olab_utils.SEVERITY_ERROR)


	def _decorate(self, img, **kwargs):
		# print('idName:', idName, 'barcode[idName]:', self.barcode[idName].deque[0])
		# print(self.barcode[idName].deque[0])
		olab_utils.decorateBarcode(img, 
								   self.deque[0]['corners'], 
								   self.deque[0]['data'], 
								   self.deque[0]['color'], addText=True)


	def _thread_Barcode(self):

		'''
		THIS IS A THREAD
		rate is in [Hz] (frames/second)
		self.camObject is the parent (from Camera).
		We are in self.camObject.barcode['default] 
		'''
		self.isThreadActive = True

		while self.camObject.camOn:
			try:
				timeNow = time.time()
							
				# FIXME -- It would be nice to cut out the `if` statements...
				
				# Throttle things if we're going faster than capture speed
				if (self.fps.actual >= self.camObject.fps['capture'].actual):
					with self.camObject.condition:
						self.camObject.condition.wait(1)   # added a timeout, just to keep from getting permanently stuck here

				'''
				# FIXME -- This was copied from ROI.  Is barcode as brittle?
				# This won't work if cam resolution has changed.
				if ((self.res_cols, self.res_rows) != (self.camObject.res_cols, self.camObject.res_rows)):
					raise Exception('Resolution changed. Stopping Barcode thread')
					# self.stop()
					# break
				'''

				data      = []
				codeTypes = []
				qualities = []
				corners   = []	

				codeList = self.pyzbar.decode(self.camObject.getFrameCopy())	   # Don't need a copy?
				for detections in codeList:
					data.append(str(detections.data, 'utf-8'))
					codeTypes.append(detections.type)
					qualities.append(detections.quality)
					'''
					This was giving really inconsistent results.
					We'll just use the rectangle instead.
					poly = []
					for vertex in detections.polygon:
						poly.append([vertex.x, vertex.y])
					corners.append(np.array(poly, np.int32).reshape((-1, 1, 2)))
					'''
					rect = [(int(detections.rect.left), int(detections.rect.top)), 
							(int(detections.rect.left+detections.rect.width), int(detections.rect.top+detections.rect.height))]
					corners.append(rect)
											
				# Add detection info to deque:
				self.deque.append({'data': data, 'codeTypes': codeTypes, 'qualities': qualities, 'corners': corners, 'color': self.color})
								
				# Do some post-processing:
				self.postFunction(self.postFunctionArgs)
				
				self.camObject.calcFramerate(self.fps, 'barcode')

				self.camObject.reachback_pubCamStatus()
			except Exception as e:
				self.stop()
				self.camObject.logger.log(f'Error in barcode {self.idName} thread: {e}', severity=olab_utils.SEVERITY_ERROR)				
				break
	
			if (not self.isThreadActive):
				self.stop()
				self.camObject.logger.log(f'Stopping barcode {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)
				break
	
			# Simplified version of rospy.sleep
			delta = max(0, timeNow + self.threadSleep - time.time())
			if (delta > 0):
				time.sleep(delta)
				
		# If while loop stops, shut down barcode:
		self.stop()


	def start(self):
		"""Start barcode detection thread at the configured frame rate.

		Launches a daemon thread that continuously captures frames, decodes barcodes,
		and stores detection results. The thread automatically throttles itself to
		match the camera's capture rate. Registers a decoration function to visualize
		detected barcodes on the video stream.
		"""
		try:
			'''
			# Add idName to self.decorations['barcode']
			if (self.idName not in self.camObject.decorations['barcode']):
				self.camObject.decorations['barcode'].append(self.idName)
			'''
			# Add to decorations deque
			# FIXME -- Maybe we don't necessarily want to decorate?
			self.decorationID = int(time.time()*1000)
			self.camObject.dec['dequeAdd'].append({'function': self._decorate, 'idName': self.idName, 'decorationID': self.decorationID})

			self.camObject.logger.log(f'Starting barcode {self.idName} thread at {self.fps_target} fps', severity=olab_utils.SEVERITY_INFO)

			barThread = threading.Thread(target=self._thread_Barcode, args=())
			barThread.daemon = True    # Allows your main script to exit, shutting down this thread, too.
			barThread.start()

		except Exception as e:
			self.camObject.logger.log(f'Error in barcode start: {e}.', severity=olab_utils.SEVERITY_ERROR)

		
	def stop(self):
		"""Stop barcode detection thread and clean up resources.

		Signals the detection thread to terminate, removes associated decorations,
		and clears the detection deque. Safe to call multiple times.
		"""
		try:
			if (self.idName in self.camObject.barcode):
				'''
				# Remove idName from self.camObject.decorations['barcode']
				if (self.idName in self.camObject.decorations['barcode']):
					self.camObject.decorations['barcode'].remove(self.idName)
				'''
				self.camObject.dec['dequeRemove'].append(self.decorationID)

				self.camObject.logger.log(f'Stopping barcode {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)

				self.isThreadActive = False
				self.deque.clear()

			else:
				self.camObject.logger.log(f'In stop, barcode {self.idName} name is not defined', severity=olab_utils.SEVERITY_ERROR)
		except Exception as e:
			self.camObject.logger.log(f'Error in barcode stop: {e}.', severity=olab_utils.SEVERITY_ERROR)
		
		
	def edit(self, fps_target=None, res_rows=None, res_cols=None):
		self.camObject.logger.log('Sorry, barcode editing is not supported.', severity=olab_utils.SEVERITY_WARNING)
		

class _FaceDetect():
	"""Internal face detection feature class using OpenCV DNN models.

	This class detects human faces in camera frames using pre-trained deep neural
	network models (Caffe or TensorFlow). Detection runs continuously in a separate
	thread at a specified frame rate, supporting both CPU and GPU inference.

	Attributes:
		camObject: Parent Camera instance managing this face detector.
		idName (str): Unique identifier for this face detection instance.
		res_rows (int): Target vertical resolution in pixels.
		res_cols (int): Target horizontal resolution in pixels.
		fps_target (float): Target detection rate in frames per second.
		postFunction (callable): Callback function invoked after each detection cycle.
		postFunctionArgs (dict): Arguments passed to the post-processing callback.
		color (tuple): RGB color for visualization of detected faces.
		conf_threshold (float): Minimum confidence threshold for face detections.
		dnn (str): DNN backend type ("caffe" or "tensorflow").
		device (str): Computation device ("cpu" or "gpu").
		modelPath (str): Directory path containing DNN model files.
		deque (collections.deque): Thread-safe storage for latest detection results.
		fps (dict): Frame rate tracking metrics for this detector.
		isThreadActive (bool): Flag indicating if detection thread is running.

	Key Methods:
		start(): Launches face detection thread.
		stop(): Terminates detection thread and cleans up resources.
	"""
	def __init__(self, camObject, idName, res_rows, res_cols, fps_target, postFunction, postFunctionArgs, color, conf_threshold, dnn, device, modelPath):
		"""Initialize face detection feature.

		Args:
			camObject: Parent Camera instance.
			idName (str): Unique identifier for this face detector.
			res_rows (int): Target vertical resolution in pixels.
			res_cols (int): Target horizontal resolution in pixels.
			fps_target (float): Target detection rate in Hz.
			postFunction (callable): Callback executed after each detection cycle.
			postFunctionArgs (dict): Arguments for post-processing callback.
			color (tuple): RGB color tuple for face bounding box visualization.
			conf_threshold (float): Minimum confidence (0.0-1.0) for detections.
			dnn (str): Neural network backend ("caffe" or "tensorflow").
			device (str): Computation device ("cpu" or "gpu").
			modelPath (str): Path to directory containing model files, or None for default.
		"""
		try:
			# https://learnopencv.com/face-detection-opencv-dlib-and-deep-learning-c-python/
			# https://pyimagesearch.com/2018/02/26/face-detection-with-opencv-and-deep-learning/
			# https://pyimagesearch.com/2018/09/24/opencv-face-recognition/
			# https://github.com/spmallick/learnopencv/tree/master/FaceDetectionComparison
				
			self.camObject = camObject  # This is the parent!
								
			self.idName   = idName
			self.decorationID = None
			
			self.res_rows = res_rows
			self.res_cols = res_cols		
			self.resolution = f'{res_cols}x{res_rows}'

			self.fps_target  = fps_target		# Hz
			self.threadSleep = 1/fps_target		# seconds
				
			self.postFunctionArgs = postFunctionArgs
			self.postFunctionArgs['idName'] = idName
			if (postFunction is None):
				self.postFunction = olab_utils._passFunction
			else:
				self.postFunction = postFunction

			if (modelPath):
				self.modelPath = modelPath
			else:
				# Use DNN models from the package installation directory
				module_dir = os.path.dirname(os.path.abspath(__file__))
				self.modelPath = os.path.join(module_dir, 'cv2_dnn_models')


			self.color = color
			
			self.conf_threshold = conf_threshold
			self.dnn            = dnn
			self.device         = device
			
			self.fps = _make_fps_dict(recheckInterval=5)

			self.deque = deque(maxlen=1)
			self.deque.append({'confidence': [], 'corners': [], 'color': self.color})
														
			self.isThreadActive = False

		except Exception as e:
			self.camObject.logger.log(f'Error in facedetect init: {e}.', severity=olab_utils.SEVERITY_ERROR)


	def _decorate(self, img, **kwargs):
		# print('idName:', idName, 'facedetect[idName]:', self.facedetect[idName].deque[0])
		# print(self.facedetect[idName].deque[0])
		olab_utils.decorateFaceDetect(img, 
								   self.deque[0]['confidence'], 
								   self.deque[0]['corners'], 
								   self.deque[0]['color'], addText=True)

			
	def _blobCaffe(self, frameCopy):
		return cv2.dnn.blobFromImage(frameCopy, 1.0, (300, 300), [104, 117, 123], False, False,)
		
	def _blobTF(self, frameCopy):
		return cv2.dnn.blobFromImage(frameCopy, 1.0, (300, 300), [104, 117, 123], True, False,)
		
	def _detectFaceOpenCVDnn(self, frameCopy, blobFunction, net):
		frameHeight = frameCopy.shape[0]
		frameWidth = frameCopy.shape[1]
		blob = blobFunction(frameCopy)   # self._blobCaffe or self._blobTF

		net.setInput(blob)
		detections = net.forward()
		confidence = []
		bboxes     = []
		for i in range(detections.shape[2]):
			conf = detections[0, 0, i, 2]
			if conf > self.conf_threshold:
				x1 = int(detections[0, 0, i, 3] * frameWidth)
				y1 = int(detections[0, 0, i, 4] * frameHeight)
				x2 = int(detections[0, 0, i, 5] * frameWidth)
				y2 = int(detections[0, 0, i, 6] * frameHeight)
				bboxes.append([(x1, y1), (x2, y2)])
				confidence.append(conf)

		return confidence, bboxes
    		

	def _thread_FaceDetect(self):

		'''
		THIS IS A THREAD
		rate is in [Hz] (frames/second)
		self.camObject is the parent (from Camera).
		We are in self.camObject.facedetect['default] 
		'''
		self.isThreadActive = True

		# OpenCV DNN supports 2 networks.
		# 1. FP16 version of the original Caffe implementation ( 5.4 MB )
		# 2. 8 bit Quantized version using TensorFlow ( 2.7 MB )

		if (self.dnn == "caffe"):
			modelFile  = f"{self.modelPath}/res10_300x300_ssd_iter_140000_fp16.caffemodel"
			configFile = f"{self.modelPath}/deploy.prototxt"
			net = cv2.dnn.readNetFromCaffe(configFile, modelFile)
			blobFunction = self._blobCaffe
		else:
			modelFile  = f"{self.modelPath}/opencv_face_detector_uint8.pb"
			configFile = f"{self.modelPath}/opencv_face_detector.pbtxt"
			net = cv2.dnn.readNetFromTensorflow(modelFile, configFile)
			blobFunction = self._blobTF

		if (self.device == "cpu"):
			net.setPreferableBackend(cv2.dnn.DNN_TARGET_CPU)
		else:
			net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
			net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)


		while self.camObject.camOn:
			try:
				timeNow = time.time()
							
				# FIXME -- It would be nice to cut out the `if` statements...
				
				# Throttle things if we're going faster than capture speed
				if (self.fps.actual >= self.camObject.fps['capture'].actual):
					with self.camObject.condition:
						self.camObject.condition.wait(1)   # added a timeout, just to keep from getting permanently stuck here

				'''
				# FIXME -- This was copied from ROI and barcode.  Is facedetect as brittle?
				# This won't work if cam resolution has changed.
				if ((self.res_cols, self.res_rows) != (self.camObject.res_cols, self.camObject.res_rows)):
					raise Exception('Resolution changed. Stopping FaceDetect thread')
					# self.stop()
					# break
				'''

				confidence, corners = self._detectFaceOpenCVDnn(self.camObject.getFrameCopy(), blobFunction, net)
				
				'''
				for detections in codeList:
					data.append(str(detections.data, 'utf-8'))
					codeTypes.append(detections.type)
					qualities.append(detections.quality)
					rect = [(int(detections.rect.left), int(detections.rect.top)), 
							(int(detections.rect.left+detections.rect.width), int(detections.rect.top+detections.rect.height))]
					corners.append(rect)
				'''
				
				# Add detection info to deque:
				self.deque.append({'confidence': confidence, 'corners': corners, 'color': self.color})
								
				# Do some post-processing:
				self.postFunction(self.postFunctionArgs)
				
				self.camObject.calcFramerate(self.fps, 'facedetect')

				self.camObject.reachback_pubCamStatus()
			except Exception as e:
				self.stop()
				self.camObject.logger.log(f'Error in facedetect {self.idName} thread: {e}', severity=olab_utils.SEVERITY_ERROR)				
				break
	
			if (not self.isThreadActive):
				self.stop()
				self.camObject.logger.log(f'Stopping facedetect {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)
				break
	
			# Simplified version of rospy.sleep
			delta = max(0, timeNow + self.threadSleep - time.time())
			if (delta > 0):
				time.sleep(delta)
				
		# If while loop stops, shut down facedetect:
		self.stop()


	def start(self):
		"""Start face detection thread at the configured frame rate.

		Launches a daemon thread that loads the DNN model, continuously processes
		frames for face detection, and stores results. The thread automatically
		throttles itself to match the camera's capture rate. Registers a decoration
		function to visualize detected faces with bounding boxes.
		"""
		try:
			'''
			# Add idName to self.decorations['facedetect']
			if (self.idName not in self.camObject.decorations['facedetect']):
				self.camObject.decorations['facedetect'].append(self.idName)
			'''
			# Add to decorations deque
			# FIXME -- Maybe we don't necessarily want to decorate?
			self.decorationID = int(time.time()*1000)
			self.camObject.dec['dequeAdd'].append({'function': self._decorate, 'idName': self.idName, 'decorationID': self.decorationID})

			self.camObject.logger.log(f'Starting facedetect {self.idName} thread at {self.fps_target} fps', severity=olab_utils.SEVERITY_INFO)

			faceThread = threading.Thread(target=self._thread_FaceDetect, args=())
			faceThread.daemon = True    # Allows your main script to exit, shutting down this thread, too.
			faceThread.start()

		except Exception as e:
			self.camObject.logger.log(f'Error in facedetect start: {e}.', severity=olab_utils.SEVERITY_ERROR)

		
	def stop(self):
		"""Stop face detection thread and clean up resources.

		Signals the detection thread to terminate, removes associated decorations,
		and clears the detection deque. Safe to call multiple times.
		"""
		try:
			if (self.idName in self.camObject.facedetect):
				'''
				# Remove idName from self.camObject.decorations['facedetect']
				if (self.idName in self.camObject.decorations['facedetect']):
					self.camObject.decorations['facedetect'].remove(self.idName)
				'''
				self.camObject.dec['dequeRemove'].append(self.decorationID)

				self.camObject.logger.log(f'Stopping FaceDetect {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)

				self.isThreadActive = False
				self.deque.clear()

			else:
				self.camObject.logger.log(f'In stop, FaceDetect {self.idName} name is not defined', severity=olab_utils.SEVERITY_ERROR)
		except Exception as e:
			self.camObject.logger.log(f'Error in FaceDetect stop: {e}.', severity=olab_utils.SEVERITY_ERROR)
		
		
	def edit(self, fps_target=None, res_rows=None, res_cols=None):
		self.camObject.logger.log('Sorry, FaceDetect editing is not supported.', severity=olab_utils.SEVERITY_WARNING)
		


class _Timelapse():
	"""Internal timelapse photography feature class for automated image capture.

	This class captures camera frames at regular intervals and saves them to disk,
	enabling creation of timelapse videos. Capture runs in a separate thread with
	configurable timing, resolution, and duration limits.

	Attributes:
		camObject: Parent Camera instance managing this timelapse feature.
		idName (str): Unique identifier for this timelapse instance.
		outputDir (str): Directory path where captured images will be saved.
		timeLimitSec (float): Maximum capture duration in seconds, or None for unlimited.
		delayStartSec (float): Initial delay before starting capture.
		resOption (tuple): Target resolution as (width, height) in pixels.
		threadSleep (float): Time interval in seconds between photo captures.
		postPostFunction (callable): Callback invoked after timelapse completes.
		isThreadActive (bool): Flag indicating if timelapse thread is running.

	Key Methods:
		start(): Launches timelapse capture thread.
		stop(): Terminates timelapse thread and cleans up resources.
	"""
	def __init__(self, camObject, idName, outputDir, secBetwPhotos, timeLimitSec, delayStartSec, res_rows, res_cols, postPostFunction):
		"""Initialize timelapse capture feature.

		Args:
			camObject: Parent Camera instance.
			idName (str): Unique identifier for this timelapse.
			outputDir (str): Directory path for saving captured images.
			secBetwPhotos (float): Time interval in seconds between captures.
			timeLimitSec (float): Maximum duration in seconds, or None for unlimited.
			delayStartSec (float): Initial delay in seconds before first capture.
			res_rows (int): Target vertical resolution in pixels.
			res_cols (int): Target horizontal resolution in pixels.
			postPostFunction (callable): Callback executed after timelapse ends.
		"""
		try:
			self.camObject = camObject  # This is the parent!
						
			self.idName     = idName
			self.decorationID = None   # We're not going to use this.
			
			self.outputDir = outputDir
			# self.secBetwPhotos = secBetwPhotos
			self.timeLimitSec  = timeLimitSec
			self.delayStartSec = delayStartSec
			# self.res_rows = res_rows
			# self.res_cols = res_cols		
			self.resOption = (res_cols, res_rows)   # (width x, height y)

			self.threadSleep = secBetwPhotos   # seconds
		
			# In other threads, this is where we do post-processing (per capture).
			# For timelapse, we have postPostProcessing (after thread ends)
			if (postPostFunction is None):
				self.postPostFunction = olab_utils._passFunction
			else:
				self.postPostFunction = postPostFunction
			self.isThreadActive = False

		except Exception as e:
			self.camObject.logger.log(f'Error in Timelapse init: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def _thread_Timelapse(self):
		'''
		THIS IS A THREAD
		rate is in [Hz] (frames/second)
		self.camObject is the parent (from Camera).
		We are in self.camObject.timelapse['default] 
		'''

		# Add a delayed start
		time.sleep(self.delayStartSec)

		# Create directory (if it does not already exist)
		if (not os.path.exists(self.outputDir)):
			print('Directory {} does not exist.  Making it now.'.format(self.outputDir))            
			os.makedirs(self.outputDir, exist_ok=True)
		
		startTime = time.time()
		
		self.isThreadActive = True

		while self.camObject.camOn:
			try:
				timeNow = time.time()
							
				# Save Photo self.camObject.getFrameCopy( change res )
				self.camObject.takePhotoLocal(path=self.outputDir, filename=None, resOption=self.resOption)
				# (roiSuccess, roiBox) = olab_utils.roiTrack(self.roiTracker, self.camObject.getFrameCopy())
				
				# Add detection info to deque:
				# self.deque.append({'success': roiSuccess, 'box': roiBox, 'color': self.color})
	
				# In other threads, this is where we do post-processing (per capture).
				# For timelapse, we have postPostProcessing (after thread ends)
				# Do some post-processing:
				# self.postFunction()
				
				# self.camObject.calcFramerate(self.fps, 'roi')

				self.camObject.reachback_pubCamStatus()
			except Exception as e:
				self.stop()
				self.camObject.logger.log(f'Error in Timelapse {self.idName} thread: {e}', severity=olab_utils.SEVERITY_ERROR)				
				break
	
			if (not self.isThreadActive):
				self.stop()
				self.camObject.logger.log(f'Stopping Timelapse {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)
				break
	
			# Simplified version of rospy.sleep
			delta = max(0, timeNow + self.threadSleep - time.time())
			if (delta > 0):
				time.sleep(delta)
								
			# Check for hitting time limit	
			if (self.timeLimitSec is not None):
				if ((time.time() - startTime) >= self.timeLimitSec):
					self.stop()
					self.camObject.logger.log(f'Stopping Timelapse {self.idName} thread; time limit reached', severity=olab_utils.SEVERITY_INFO)
					break
			
		# If while loop stops, shut down timelapse:
		self.stop()
		
	def start(self):
		"""Start timelapse capture thread with configured intervals.

		Launches a daemon thread that waits for the initial delay, creates the output
		directory if needed, and begins capturing photos at regular intervals. The
		thread automatically stops when the time limit is reached (if specified) or
		when explicitly stopped.
		"""
		try:
			# Not using decorations deque
			'''
			self.decorationID = int(time.time()*1000)
			self.camObject.dec['dequeAdd'].append({'function': self._decorate, 'idName': self.idName, 'decorationID': self.decorationID})
			'''

			self.camObject.logger.log(f'Starting Timelapse thread {self.idName} at {self.threadSleep} sec between photos', severity=olab_utils.SEVERITY_INFO)

			tlThread = threading.Thread(target=self._thread_Timelapse, args=())
			tlThread.daemon = True    # Allows your main script to exit, shutting down this thread, too.
			tlThread.start()
		except Exception as e:
			self.camObject.logger.log(f'Error in Timelapse start: {e}.', severity=olab_utils.SEVERITY_ERROR)
		
	def stop(self):
		"""Stop timelapse capture thread.

		Signals the capture thread to terminate after completing the current photo.
		The post-processing callback is invoked after the thread stops. Safe to call
		multiple times.
		"""
		try:
			if (self.idName in self.camObject.timelapse):

				self.camObject.logger.log(f'Stopping timelapse {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)

				self.isThreadActive = False
				# self.deque.clear()
			else:
				self.camObject.logger.log(f'In stop, timelapse {self.idName} name is not defined', severity=olab_utils.SEVERITY_ERROR)
		except Exception as e:
			self.camObject.logger.log(f'Error in timelapse stop: {e}.', severity=olab_utils.SEVERITY_ERROR)	

		
			

class _ROI():
	"""Internal region-of-interest tracking feature class using OpenCV trackers.

	This class tracks a specified rectangular region in camera frames using OpenCV's
	object tracking algorithms. The tracker continuously updates the ROI position as
	objects move, running in a separate thread at a specified frame rate.

	Attributes:
		camObject: Parent Camera instance managing this ROI tracker.
		idName (str): Unique identifier for this ROI tracker instance.
		roiBB (tuple): Initial bounding box as (x, y, width, height).
		roiTracker: OpenCV tracker object for ROI tracking.
		res_rows (int): Vertical resolution in pixels (must match camera).
		res_cols (int): Horizontal resolution in pixels (must match camera).
		fps_target (float): Target tracking rate in frames per second.
		postFunction (callable): Callback function invoked after each tracking update.
		color (tuple): RGB color for visualization of tracked ROI.
		deque (collections.deque): Thread-safe storage for latest tracking results.
		fps (dict): Frame rate tracking metrics for this tracker.
		isThreadActive (bool): Flag indicating if tracking thread is running.

	Key Methods:
		start(): Launches ROI tracking thread.
		stop(): Terminates tracking thread and cleans up resources.

	Note:
		ROI tracking requires the camera resolution to remain constant. The thread
		will terminate if resolution changes are detected.
	"""
	def __init__(self, camObject, idName, roiTrackerName, roiBB, fps_target, postFunction, color):
		"""Initialize region-of-interest tracking feature.

		Args:
			camObject: Parent Camera instance.
			idName (str): Unique identifier for this ROI tracker.
			roiTrackerName (str): Name of OpenCV tracker algorithm to use.
			roiBB (tuple): Initial bounding box as (x, y, width, height).
			fps_target (float): Target tracking rate in Hz.
			postFunction (callable): Callback executed after each tracking update.
			color (tuple): RGB color tuple for ROI box visualization.
		"""
		try:
			self.camObject = camObject  # This is the parent!
						
			self.idName     = idName
			self.decorationID = None
							
			self.roiBB      = roiBB  #  (x, y, w, h)
			self.roiTracker = olab_utils.OPENCV_OBJECT_TRACKERS[roiTrackerName]()
			self.roiTracker.init(self.camObject.getFrameCopy(), self.roiBB)

			# We must maintain same resolution as the camera feed.
			self.res_rows = self.camObject.res_rows
			self.res_cols = self.camObject.res_cols		
			self.resolution = f'{self.res_cols}x{self.res_rows}'

			self.fps_target  = fps_target		# Hz
			self.threadSleep = 1/fps_target		# seconds
				
			if (postFunction is None):
				self.postFunction = olab_utils._passFunction
			else:
				self.postFunction = postFunction

			self.color = color
			
			self.fps = _make_fps_dict(recheckInterval=5)

			self.deque = deque(maxlen=1)
			self.deque.append({'success': False, 'box': [], 'color': self.color})
								
			self.isThreadActive = False

		except Exception as e:
			self.camObject.logger.log(f'Error in ROI init: {e}.', severity=olab_utils.SEVERITY_ERROR)


	def _decorate(self, img, **kwargs):
		if (self.deque[0]['success']):
			olab_utils.roiDrawBox(img, self.deque[0]['box'], self.deque[0]['color'])
		
	def _thread_ROI(self):
		'''
		THIS IS A THREAD
		rate is in [Hz] (frames/second)
		self.camObject is the parent (from Camera).
		We are in self.camObject.roi['default] 
		'''
		self.isThreadActive = True

		while self.camObject.camOn:
			try:
				timeNow = time.time()
							
				# FIXME -- It would be nice to cut out the `if` statements...
				
				# Throttle things if we're going faster than capture speed
				if (self.fps.actual >= self.camObject.fps['capture'].actual):
					with self.camObject.condition:
						self.camObject.condition.wait(1)   # added a timeout, just to keep from getting permanently stuck here

				# This won't work if cam resolution has changed.
				if ((self.res_cols, self.res_rows) != (self.camObject.res_cols, self.camObject.res_rows)):
					raise Exception('Resolution changed. Stopping ROI thread')
					# self.stop()
					# break
				
				(roiSuccess, roiBox) = olab_utils.roiTrack(self.roiTracker, self.camObject.getFrameCopy())
				
				# Add detection info to deque:
				self.deque.append({'success': roiSuccess, 'box': roiBox, 'color': self.color})
	
				# Do some post-processing:
				self.postFunction()
				
				self.camObject.calcFramerate(self.fps, 'roi')

				self.camObject.reachback_pubCamStatus()
			except Exception as e:
				self.stop()
				self.camObject.logger.log(f'Error in ROI {self.idName} thread: {e}', severity=olab_utils.SEVERITY_ERROR)				
				break
	
			if (not self.isThreadActive):
				self.stop()
				self.camObject.logger.log(f'Stopping ROI {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)
				break
	
			# Simplified version of rospy.sleep
			delta = max(0, timeNow + self.threadSleep - time.time())
			if (delta > 0):
				time.sleep(delta)
				
		# If while loop stops, shut down roi:
		self.stop()
	
	
	def start(self):
		"""Start ROI tracking thread at the configured frame rate.

		Launches a daemon thread that continuously tracks the region of interest
		across frames, updating the bounding box position. The thread automatically
		throttles itself to match the camera's capture rate. Registers a decoration
		function to visualize the tracked ROI on the video stream.
		"""
		try:
			'''
			# Add 'default' to self.decorations['roi']
			if (self.idName not in self.camObject.decorations['roi']):
				self.camObject.decorations['roi'].append(self.idName)
			'''
			# Add to decorations deque
			# FIXME -- Maybe we don't necessarily want to decorate?
			self.decorationID = int(time.time()*1000)
			self.camObject.dec['dequeAdd'].append({'function': self._decorate, 'idName': self.idName, 'decorationID': self.decorationID})

			self.camObject.logger.log(f'Starting ROI thread {self.idName} at {self.fps_target} fps', severity=olab_utils.SEVERITY_INFO)

			roiThread = threading.Thread(target=self._thread_ROI, args=())
			roiThread.daemon = True    # Allows your main script to exit, shutting down this thread, too.
			roiThread.start()
		except Exception as e:
			self.camObject.logger.log(f'Error in ROI start: {e}.', severity=olab_utils.SEVERITY_ERROR)
				
		
	def stop(self):
		"""Stop ROI tracking thread and clean up resources.

		Signals the tracking thread to terminate, removes associated decorations,
		and clears the tracking deque. Safe to call multiple times.
		"""
		try:
			if (self.idName in self.camObject.roi):
				'''
				# Remove idName from self.camObject.decorations['roi']
				if (self.idName in self.camObject.decorations['roi']):
					self.camObject.decorations['roi'].remove(self.idName)
				'''
				self.camObject.dec['dequeRemove'].append(self.decorationID)

				self.camObject.logger.log(f'Stopping ROI {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)

				self.isThreadActive = False
				self.deque.clear()
			else:
				self.camObject.logger.log(f'In stop, ROI {self.idName} name is not defined', severity=olab_utils.SEVERITY_ERROR)
		except Exception as e:
			self.camObject.logger.log(f'Error in ROI stop: {e}.', severity=olab_utils.SEVERITY_ERROR)
		
	
	def edit(self):
		self.camObject.logger.log('Sorry, ROI editing is not supported.', severity=olab_utils.SEVERITY_WARNING)


class _Ultralytics():
	"""Internal Ultralytics YOLO feature class for object detection and tracking.

	This class performs real-time object detection, classification, segmentation, pose
	estimation, or tracking using Ultralytics YOLO models. Processing runs continuously
	in a separate thread at a specified frame rate, supporting various YOLO tasks.

	Attributes:
		camObject: Parent Camera instance managing this YOLO feature.
		idName (str): Task identifier ("detect", "classify", "pose", "obb", "track", "segment").
		model_name (str): YOLO model filename (e.g., "yolo11n.pt", "yolo11n-seg.pt").
		model: Loaded Ultralytics YOLO model instance.
		res_rows (int): Target vertical resolution in pixels.
		res_cols (int): Target horizontal resolution in pixels.
		fps_target (float): Target inference rate in frames per second.
		postFunction (callable): Callback function invoked after each inference cycle.
		postFunctionArgs (dict): Arguments passed to the post-processing callback.
		color (tuple): RGB color for visualization of detections.
		conf_threshold (float): Minimum confidence threshold for detections.
		verbose (bool): Whether to print detailed inference information.
		drawBox (bool): Whether to draw bounding boxes on detections.
		drawLabel (bool): Whether to draw class labels on detections.
		maskOutline (bool): Whether to draw mask outlines for segmentation.
		deque (collections.deque): Thread-safe storage for latest inference results.
		fps (dict): Frame rate tracking metrics for this feature.
		isThreadActive (bool): Flag indicating if inference thread is running.

	Key Methods:
		start(): Launches YOLO inference thread.
		stop(): Terminates inference thread and cleans up resources.
	"""
	def __init__(self, camObject, idName, res_rows, res_cols, fps_target, postFunction, postFunctionArgs, color, conf_threshold, model_name, verbose, drawBox, drawLabel, maskOutline):
		"""Initialize Ultralytics YOLO feature.

		Args:
			camObject: Parent Camera instance.
			idName (str): Task type ("detect", "classify", "pose", "obb", "track", "segment").
			res_rows (int): Target vertical resolution in pixels.
			res_cols (int): Target horizontal resolution in pixels.
			fps_target (float): Target inference rate in Hz.
			postFunction (callable): Callback executed after each inference cycle.
			postFunctionArgs (dict): Arguments for post-processing callback.
			color (tuple): RGB color tuple for visualization.
			conf_threshold (float): Minimum confidence (0.0-1.0) for detections.
			model_name (str): YOLO model filename to load.
			verbose (bool): Enable detailed inference logging.
			drawBox (bool): Draw bounding boxes on detections, or None for auto.
			drawLabel (bool): Draw class labels on detections, or None for auto.
			maskOutline (bool): Draw mask outlines for segmentation tasks.
		"""
		self.camObject = camObject  # This is the parent!

		try:
			from ultralytics import YOLO
		except Exception as e:
			self.camObject.logger.log(f'Error in ultralytics import: {e}.', severity=olab_utils.SEVERITY_ERROR)
			return
			
		try:												
			self.idName   = idName          # "detect", "classify", "pose", "obb", "track", or "segment"
			self.model_name = model_name    # "yolo11n.pt", "yolo11n-cls.pt", etc
			self.model = YOLO(model_name)
			self.verbose = verbose
			if (drawBox is None): 
				if (idName == 'pose'):
					self.drawBox = False
				else:
					self.drawBox = True
			else:
				self.drawBox = drawBox	
			if (drawLabel is None):
				self.drawLabel = self.drawBox
			else:
				self.drawLabel = drawLabel	
			self.maskOutline = maskOutline
						
			self.decorationID = None   # FIXU -- What will this be?
			
			self.res_rows = res_rows
			self.res_cols = res_cols		
			self.resolution = f'{res_cols}x{res_rows}'
			
			self.fps_target  = fps_target		# Hz
			self.threadSleep = 1/fps_target		# seconds
				
			self.postFunctionArgs = postFunctionArgs
			self.postFunctionArgs['idName'] = idName
			if (postFunction is None):
				self.postFunction = olab_utils._passFunction
			else:
				self.postFunction = postFunction

			self.color = color
			
			self.conf_threshold = conf_threshold
			
			self.fps = _make_fps_dict(recheckInterval=5)

			self.deque = deque(maxlen=1)
			self.deque.append(self._initDeque()) 
			
			self.isThreadActive = False

		except Exception as e:
			self.camObject.logger.log(f'Error in ultralytics init: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def _decorate(self, img, **kwargs):
		# print('idName:', idName, 'ultralytics[idName]:', self.ultralytics[idName].deque[0])
		# print(self.ultralytics[idName].deque[0])
		olab_utils.decorateUltralytics(img, self.res_cols, self.res_rows, self.idName, self.deque[0], self.drawBox, self.drawLabel, self.maskOutline)
		# FIXU -- Needs to match deque as defined in __init__

	def _initDeque(self):
		return {'class': [], 'class_conf': [], 'is_track': False, 'id': [], 
				'xywh': [], 'xyxy': [],
				'xywhr': [], 'xyxyxyxy': [],  
				'keypoints': [], 'keypoints_conf': [],  
				'masks_data': [], 'masks_xy': []}

	def _to_np(self, x):
		'''
		Converts Cuda tensor to numpy array
		Tensor -> NumPy on CPU; passthrough for NumPy arrays.
		'''			
		if isinstance(x, np.ndarray):
			return x
		else:
			return x.detach().cpu().numpy()
		
		# I'm trying to avoid importing torch	
		# if isinstance(x, torch.Tensor):
		#	return x.detach().cpu().numpy()

		raise TypeError(f"Unsupported type: {type(x)}")
		
		
	def _processResults(self, results):
		dequeInfo = self._initDeque()

		np_res  = np.array([self.res_cols, self.res_rows])
		np_res2 = np.array([self.res_cols, self.res_rows, self.res_cols, self.res_rows])
		 
		if results[0].boxes is not None:		
			bx = results[0].boxes 
			dequeInfo['xywh'] = (self._to_np(bx.xywhn)*(np_res2)).astype(int).tolist()
			# dequeInfo['xywhn'] = bx.xywhn.tolist()
			# dequeInfo['xywhr'] = []
			dequeInfo['xyxy'] = (self._to_np(bx.xyxyn)*(np_res2)).astype(int).tolist()
			# dequeInfo['xyxyn'] = bx.xyxyn.tolist()
			# dequeInfo['xyxyxyxy'] = []
		elif results[0].obb is not None:
			bx = results[0].obb
			# dequeInfo['xywh'] = []
			dequeInfo['xywhr'] = self._to_np(bx.xywhr).tolist()    # This is the center point of obb, in original resolution
			# dequeInfo['xywhrn'] = bx.xywhrn.tolist()  # There's no such thing as `xywhrn` 
			# dequeInfo['xyxy'] = []
			dequeInfo['xyxyxyxy'] = (self._to_np(bx.xyxyxyxyn)*(np_res)).astype(int).tolist()
			# dequeInfo['xyxyxyxyn'] = bx.xyxyxyxyn.tolist()

		else:
			bx = None
			'''
			dequeInfo['class'] = []
			dequeInfo['class_conf'] = []
			dequeInfo['is_track'] = False 
			dequeInfo['id'] = [] 
			dequeInfo['xywh'] = []
			dequeInfo['xywhr'] = []
			dequeInfo['xyxy'] = []
			dequeInfo['xyxyxyxy'] = []
			'''
						
		if bx is not None:
			dequeInfo['class'] = [results[0].names.get(key) for key in bx.cls.tolist()]
			dequeInfo['class_conf'] = bx.conf.tolist()
			dequeInfo['is_track'] = bx.is_track 
			dequeInfo['id'] = bx.id.tolist() if bx.id is not None else []
		
		if (results[0].keypoints is not None):
			# dequeInfo['keypoints'] = results[0].keypoints.xyn.tolist() if results[0].keypoints.xyn is not None else []
			# dequeInfo['keypoints'] = (results[0].keypoints.xyn*np_res).int().tolist() if results[0].keypoints.xyn is not None else []
			dequeInfo['keypoints'] = np.array(results[0].keypoints.xyn*np_res).astype(int) if results[0].keypoints.has_visible else []
			dequeInfo['keypoints_conf'] = results[0].keypoints.conf.tolist() if results[0].keypoints.conf is not None else []
		# else:
		# 	dequeInfo['keypoints'] = [] 
		#	dequeInfo['keypoints_conf'] = []

		if (results[0].masks is not None):
			for i in range(0, len(results[0].masks.data)):
				dequeInfo['masks_data'].append(
					cv2.resize(np.array(results[0].masks.data[i]), np_res, interpolation=cv2.INTER_LINEAR).round())  
				dequeInfo['masks_xy'].append((results[0].masks.xyn[i]*np_res).astype(int)) 
		# else:
		#	dequeInfo['masks_data'] = [] 
		#	dequeInfo['masks_xy'] = []
		
		return(dequeInfo)
		
	def _thread_Ultralytics(self):

		'''
		THIS IS A THREAD
		rate is in [Hz] (frames/second)
		self.camObject is the parent (from Camera).
		We are in self.camObject.ultralytics[idName] 
		'''
		self.isThreadActive = True

		while self.camObject.camOn:
			try:
				timeNow = time.time()
							
				# FIXME -- It would be nice to cut out the `if` statements...
				
				# Throttle things if we're going faster than capture speed
				if (self.fps.actual >= self.camObject.fps['capture'].actual):
					with self.camObject.condition:
						self.camObject.condition.wait(1)   # added a timeout, just to keep from getting permanently stuck here


				# Predict or Track?
				if (self.idName == 'track'):
					results = self.model.track(self.camObject.getFrameCopy(), stream=False, persist=True, conf=self.conf_threshold, verbose=self.verbose) 
				else:
					results = self.model.predict(self.camObject.getFrameCopy(), stream=False, conf=self.conf_threshold, verbose=self.verbose) 
					# FIXME -- Can also specify a subset of classes/objects to detect.
					# See https://docs.ultralytics.com/modes/predict/#inference-arguments
								
				# Process the results
				dequeInfo = self._processResults(results)
				
				# Add detection info to deque:
				self.deque.append(dequeInfo)
								
				# Do some post-processing:
				self.postFunctionArgs['results'] = results
				self.postFunction(self.postFunctionArgs)
				
				self.camObject.calcFramerate(self.fps, 'ultralytics') 

				self.camObject.reachback_pubCamStatus()
			except Exception as e:
				self.stop()
				self.camObject.logger.log(f'Error in ultralytics {self.idName} thread: {e}', severity=olab_utils.SEVERITY_ERROR)				
				break
	
			if (not self.isThreadActive):
				self.stop()
				self.camObject.logger.log(f'Stopping ultralytics {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)
				break
	
			# Simplified version of rospy.sleep
			delta = max(0, timeNow + self.threadSleep - time.time())
			if (delta > 0):
				time.sleep(delta)
				
		# If while loop stops, shut down ultralytics:
		self.stop()

	def edit(self, *args, **kwargs):
		self.camObject.logger.log('Sorry, ultralytics editing is not yet supported.', severity=olab_utils.SEVERITY_WARNING)

	def start(self):
		"""Start YOLO inference thread at the configured frame rate.

		Launches a daemon thread that continuously runs YOLO inference on camera
		frames, processes results, and stores detections. The thread automatically
		throttles itself to match the camera's capture rate. Registers a decoration
		function to visualize detections with boxes, labels, masks, or keypoints
		depending on the task type.
		"""
		try:
			self.camObject.logger.log(f'Starting Ultralytics {self.idName} thread at {self.fps_target} fps', severity=olab_utils.SEVERITY_INFO)

			ultraThread = threading.Thread(target=self._thread_Ultralytics, args=())
			ultraThread.daemon = True    # Allows your main script to exit, shutting down this thread, too.
			ultraThread.start()

			# Add to decorations deque
			# FIXME -- Maybe we don't necessarily want to decorate?
			self.decorationID = int(time.time()*1000)
			self.camObject.dec['dequeAdd'].append({'function': self._decorate, 'idName': self.idName, 'decorationID': self.decorationID})

		except Exception as e:
			self.camObject.logger.log(f'Error in ultralytics start: {e}.', severity=olab_utils.SEVERITY_ERROR)


	def stop(self):
		"""Stop YOLO inference thread and clean up resources.

		Signals the inference thread to terminate, removes associated decorations,
		and clears the detection deque. Safe to call multiple times.
		"""
		try:
			if (self.idName in self.camObject.ultralytics):
				'''
				# Remove idName from self.camObject.decorations['ultralytics']
				if (self.idName in self.camObject.decorations['ultralytics']):
					self.camObject.decorations['ultralytics'].remove(self.idName)
				'''
				self.camObject.dec['dequeRemove'].append(self.decorationID)

				self.camObject.logger.log(f'Stopping Ultralytics {self.idName} thread.', severity=olab_utils.SEVERITY_INFO)

				self.isThreadActive = False
				self.deque.clear()
			else:
				self.camObject.logger.log(f'In stop, ultralytics {self.idName} dictionary is not defined', severity=olab_utils.SEVERITY_ERROR)
		except Exception as e:
			self.camObject.logger.log(f'Error in ultralytics stop: {e}.', severity=olab_utils.SEVERITY_ERROR)

				
