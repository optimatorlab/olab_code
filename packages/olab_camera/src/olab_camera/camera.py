"""Base `Camera` class shared by all camera backend implementations."""

import datetime
import os
import sys
import threading
import time
from collections import deque
from functools import partial
from pathlib import Path
from threading import Condition

import asyncio
import ssl

import cv2
import numpy as np

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .cv_features import _Aruco, _Calibrate, _Barcode, _QRCode, _FaceDetect, _Timelapse, _ROI, _Ultralytics
from .streaming import (
	StreamingHandler, StreamingServer, WebSocketStreamingServer, WebRTCStreamingServer,
	_make_fps_dict, STREAM_MAX_WAIT_TIME_SEC, _HAS_WEBSOCKETS, _HAS_WEBRTC,
)

ROSPUB_MAX_WAIT_TIME_SEC = 2  # max time (in seconds) we wait for condition

try:
	import rospy
	from cv_bridge import CvBridge  # NOTE:  Does not support CompressedImage in Python
	from sensor_msgs.msg import Image, CompressedImage
except Exception as e:
	print(f'INFO: rospy is not installed and was not imported.  You may ignore this message.  Unless you are using ROS you do not need rospy.')
	# print(f'NOTE: Could not import rospy:  {e}')
	rospy = None
	CvBridge = None
	Image = None
	CompressedImage = None


class Camera():
	"""Base class for all camera implementations in the UB camera framework.

	This is an abstract base class that provides common functionality for camera operations
	including frame capture, video streaming, ROS integration, and computer vision features.
	Subclasses (CameraPi, CameraROS, CameraUSB, CameraVoxl) implement hardware-specific
	capture mechanisms while inheriting shared streaming, processing, and publishing capabilities.

	Key Features:
		- Video streaming over HTTPS/WSS with SSL/TLS support:
		  MJPEG (default), WebSocket + JPEG, and WebRTC
		- ROS topic publishing (raw and compressed image formats)
		- Computer vision modules: ArUco marker detection, barcode/QR code scanning,
		  face detection, ROI tracking, camera calibration, Ultralytics YOLO models
		- Timelapse photography
		- Digital zoom functionality
		- Frame decoration/annotation system
		- Multi-threaded architecture for concurrent operations
		- IP allowlisting/blocklisting for stream access control

	Attributes:
		camOn (bool): Whether the camera is currently active and capturing frames.
		fps (dict): Framerate tracking for 'capture', 'stream', and 'publish' threads.
		fps_target (int): Target framerate for camera capture (from paramDict).
		res_rows (int): Camera resolution height in pixels (from paramDict).
		res_cols (int): Camera resolution width in pixels (from paramDict).
		intrinsics (dict): Camera calibration intrinsics (matrix and distortion coefficients)
			organized by resolution (e.g., '640x480': {'matrix': ndarray, 'dist': ndarray}).
		aruco (dict): Active ArUco marker detection instances keyed by idName.
		roi (dict): Active region-of-interest tracking instances.
		barcode (dict): Active barcode/QR code detection instances.
		calibrate (dict): Active camera calibration instances.
		timelapse (dict): Active timelapse photography instances.
		facedetect (dict): Active face detection instances.
		ultralytics (dict): Active Ultralytics YOLO model instances.
		zoomLevel (float): Current digital zoom level (1.0 = no zoom).
		keepStreaming (bool): Flag to control the active streaming thread.
		activeProtocol (str): Currently active streaming protocol ('mjpeg', 'websocket', 'webrtc'), or None.
		keepPublishing (bool): Flag to control ROS publishing thread.
		numStreams (int): Count of active stream connections.
		frameDeque (deque): Thread-safe deque holding the most recent captured frame.
		condition (Condition): Threading condition variable for frame synchronization.
		logger (Logger): Logging instance for recording events and errors.
		showFPS (bool): Whether to overlay FPS information on streamed frames.
		ipAllowlist (list): List of IP addresses allowed to access streams (empty = all allowed).
		ipBlocklist (list): List of IP addresses blocked from accessing streams.

	Notes:
		- The base Camera class does not implement frame capture. Subclasses must implement
		  their own capture mechanism and populate frameDeque with numpy arrays.
		- All paramDict keys are automatically converted to class attributes.
		- Computer vision features run in separate threads and can operate concurrently.
		- Streaming uses threading.Condition for efficient frame synchronization.
	"""

	# was `cam_capture_initialize`
	def __init__(self, paramDict, logger=None, sslPath=None, pubCamStatusFunction=None,
				 initROSnode=False, showFPS=True, ipAllowlist=[], ipBlocklist=[]):
		"""Initialize the Camera base class with configuration and optional components.

		Args:
			paramDict (dict): Configuration dictionary containing camera parameters.
				Expected keys include:
				- res_rows (int): Image height in pixels.
				- res_cols (int): Image width in pixels.
				- fps_target (int): Target framerate.
				- intrinsics (dict, optional): Camera calibration data by resolution.
				All keys in paramDict are converted to instance attributes.
			logger (Logger, optional): Logger instance for event recording. If None, creates
				a new olab_utils.Logger instance.
			sslPath (str, optional): Path to SSL certificate directory containing ca.key and
				ca.crt files. Defaults to '~/.olab_camera/ssl', where a fresh machine-local
				self-signed certificate is auto-generated (and self-healed/reused on later
				runs) the first time streaming actually starts — see olab_camera.tls. If you
				pass an explicit sslPath, it is used exactly as given: never locked, chmod'd,
				parsed, or regenerated by olab_camera. Bring your own ca.key/ca.crt; an
				invalid or missing pair surfaces load_cert_chain()'s own native error.
			pubCamStatusFunction (callable, optional): Callback function to publish camera
				status updates. If None, uses a no-op function.
			initROSnode (bool): Whether to initialize a ROS node on construction. Default False.
			showFPS (bool): Whether to display FPS overlay on streamed frames. Default True.
			ipAllowlist (list): IP addresses allowed to access streams. Empty list allows all.
			ipBlocklist (list): IP addresses blocked from accessing streams.

		Notes:
			- Camera intrinsics are processed and converted to numpy arrays with 'matrix'
			  and 'dist' (distortion) keys.
			- The frameDeque is initialized as a deque with maxlen=1 to hold only the most
			  recent frame.
			- Computer vision feature dictionaries (aruco, roi, barcode, etc.) are initialized
			  as empty and populated when add*() methods are called.
		"""
		# Here's where we put the stuff that was in __init__ from each specific camera class...

		if (logger):
			self.logger = logger
		else:
			self.logger = olab_utils.Logger()
		# Practice:
		self.logger.log(f'{paramDict}')

		# _usesGeneratedTls tracks whether sslPath is our own auto-managed
		# default or an administrator-supplied path. _ensureSslPath() only
		# ever touches (locks, chmods, parses, regenerates) the former --
		# an explicit sslPath is never written to, inspected, or altered.
		# load_cert_chain() reports its own native error if it's invalid.
		if (sslPath):
			self.sslPath = sslPath
			self._usesGeneratedTls = False
		else:
			# Default path only — nothing is written to disk here. The cert is
			# generated lazily on first actual TLS use (see _ensureSslPath()),
			# so capture-only use of a Camera never touches the filesystem or
			# requires a home directory (containers, service accounts, etc.).
			self.sslPath = str(Path.home() / '.olab_camera' / 'ssl')
			self._usesGeneratedTls = True

		# If provided, the pubCamStatus function would be in the "main" script.
		# Otherwise, we'll just call `pass`
		if (pubCamStatusFunction):
			self.reachback_pubCamStatus = pubCamStatusFunction
		else:
			self.reachback_pubCamStatus = olab_utils._passFunction

		
		# Turn keys in a dictionary into class attributes
		# https://stackoverflow.com/questions/1639174/creating-class-instance-properties-from-a-dictionary
		for k, v in paramDict.items():
			setattr(self, k, v)

		# outputPort is used by all subclass start() methods as the default streaming port.
		# Guard here so callers that omit it from paramDict don't get an AttributeError.
		if not hasattr(self, 'outputPort'):
			self.outputPort = None

		# Create dictionaries of camera intrinsics, if info was in paramDict.
		# self.intrinsics['640x480']['matrix'] and self.intrinsics['640x480']['dist']
		# Or, self.intrinsics = {}
		self.intrinsics = self._getIntrinsics()

		# FIXME -- Do some validation on inputs (paramDict keys/values)
		# `res_rows` and `res_cols` must be int values
		# `fps_target` must be positive numeric (realistically, within some limits)
				
		# Info for calculating framerates.
		# NOTE: aruco and roi (and barcode, etc) will be defined separately.
		self.fps = {'capture': _make_fps_dict(recheckInterval=3), 
					'stream':  _make_fps_dict(recheckInterval=3),
					'publish': _make_fps_dict(recheckInterval=5)}
		self.showFPS = showFPS
		
		self.ipAllowlist = list(ipAllowlist)
		self.ipBlocklist = list(ipBlocklist)

		self.condition = Condition()		# FIXME -- Can we call this self.frameReadyCondition?  NOTE:  This is referenced by camAutoTakePic...If you change names check there, too.

		self.frameDeque = deque(maxlen=1)
		# Timestamp of the most recent real frame append — a genuine "is the
		# stream currently alive" signal (see lastFrameAge()). None until the
		# first frame arrives, or forever on subclasses whose capture loop
		# doesn't update it (currently only CameraUSB does).
		self._lastFrameTime = None

		self.camOn = False		# FIXME -- Group the flags together
		
		self.numStreams      = 0
		self.keepStreaming   = False
		self.activeProtocol = None   # 'mjpeg' | 'websocket' | 'webrtc'
		self.streamPort     = None
		self.streamURL      = None
		
		self.keepPublishing = False   # _thread_ros
		self.hasROSnode = False	
			
		self.keepCalibrating = False  # _thread_calibrate
			
		self.zoomLevel    = 1.0
		self.zoomFunction = self._zoomFunction_pass
				
		self.camTopicSubscriber = None    # Used by CameraROS (compressed image callback)

		# self.pose: the vehicle body's own world-frame pose (set via setPose()),
		# used the same way camera.intrinsics is: read directly by your own
		# postFunction and passed into olab_utils.findTagPoseGlobal()/
		# findCameraPoseGlobal(). None until setPose() is called.
		self.pose = None
		# self.extrinsics: the camera's fixed mount pose relative to the vehicle
		# body frame (set via setExtrinsics()). Defaults to identity (camera at
		# the body origin, boresight aligned with the body's +x/forward axis).
		self.extrinsics = {'position': (0.0, 0.0, 0.0), 'orientation': (0.0, 0.0, 0.0)}

		self.aruco       = {}
		self.roi         = {}
		self.barcode     = {}
		self.qr          = {}
		self.calibrate   = {}
		self.timelapse   = {}
		self.facedetect  = {}
		self.ultralytics = {}
		# self.decorations = {'aruco': [], 'roi': [], 'barcode': [], 'calibrate': []}
		self.dec = {'active': [], 'dequeAdd': deque(), 'dequeRemove': deque(), 'dequeEdit': deque()}
 
		if (initROSnode):
			self._init_ros_node()

	def _ensureSslPath(self):
		"""Ensure self.sslPath has a usable ca.key/ca.crt pair, generating one if needed.

		Called lazily right before actually serving TLS (starting a stream),
		not from __init__ — so constructing a Camera for capture-only use
		never touches the filesystem or requires a home directory.

		This is a **true no-op** if the caller passed an explicit sslPath
		(self._usesGeneratedTls is False) -- an administrator-supplied
		certificate directory (which may be root- or group-managed, use an
		encrypted key, a non-RSA key, or a full chain) is never locked,
		chmod'd, parsed, or regenerated. load_cert_chain() reports its own
		native error if that material is missing or invalid. Only our own
		auto-managed default (~/.olab_camera/ssl) is ever touched here.
		"""
		if not self._usesGeneratedTls:
			return
		from .tls import ensure_local_cert
		self.sslPath = str(ensure_local_cert(Path(self.sslPath)))

	# ── IP allowlist helpers ──────────────────────────────────────────────────
	def set_allowlist(self, ips):
		"""Replace the IP allowlist entirely. Pass an empty list to allow all IPs."""
		self.ipAllowlist = list(ips)

	def add_to_allowlist(self, ip):
		"""Add a single IP to the allowlist if not already present."""
		if ip and ip not in self.ipAllowlist:
			self.ipAllowlist.append(ip)

	def remove_from_allowlist(self, ip):
		"""Remove a single IP from the allowlist (no-op if not present)."""
		try:
			self.ipAllowlist.remove(ip)
		except ValueError:
			pass

	def _getIntrinsics(self):
		'''
		Clean up self.intrinsics, which is populated from the input parameters dictionary.
		We might have something that looks like:
			self.intrinsics = {'640x480': {'cx': 323.09833463, 'cy': 235.34434675, 'fx': 664.11131483, 'fy': 666.96448353, 
										   'dist': [0.0541, -1.545, 0.003, -0.002, 5.536]}}		
		We'll clean this up (remove cx, cy, fx, fy) and add the camera matrix.
		FIXME -- Should we delete cx, cy, fx, and fy?
		If there are no intrinsics, we'll return an empty dictionary
		'''
		if (hasattr(self, 'intrinsics')):
			intr = {}
			for res in self.intrinsics:
				tmp = {}
				if ('dist' in self.intrinsics[res]):
					tmp['dist'] = np.array(self.intrinsics[res]['dist'])
				if (all(k in self.intrinsics[res] for k in ('fx', 'fy', 'cx', 'cy'))):
					tmp['matrix'] = np.array( [[ self.intrinsics[res]['fx'], 0.0,  self.intrinsics[res]['cx']], 
											   [0.0,  self.intrinsics[res]['fy'],  self.intrinsics[res]['cy']], [0.0, 0.0, 1.0]] )
				if (all(k in tmp for k in ('dist', 'matrix'))):
					intr[res] = tmp
			return intr
		else:
			return {}

	def setIntrinsics(self, res, fx, fy, cx, cy, dist):
		"""Set or update camera intrinsics for a given resolution.

		Builds the 3x3 camera matrix from the provided focal length and principal
		point values, converts the distortion coefficients to a numpy array, and
		stores both under the given resolution key in self.intrinsics.

		Args:
			res (str): Resolution key in 'WIDTHxHEIGHT' format (e.g., '640x480').
			fx (float): Focal length in the x direction, in pixels.
			fy (float): Focal length in the y direction, in pixels.
			cx (float): Principal point x-coordinate (horizontal optical center), in pixels.
			cy (float): Principal point y-coordinate (vertical optical center), in pixels.
			dist (list or array-like): Distortion coefficients in OpenCV order.
				Supported lengths:
				- 4:  [k1, k2, p1, p2]
				- 5:  [k1, k2, p1, p2, k3]  (most common)
				- 8:  [k1, k2, p1, p2, k3, k4, k5, k6]
				- 12: [k1, k2, p1, p2, k3, k4, k5, k6, s1, s2, s3, s4]
				- 14: [k1, k2, p1, p2, k3, k4, k5, k6, s1, s2, s3, s4, tx, ty]

		Example:
			>>> cam.setIntrinsics('640x480', fx=664.11, fy=666.96, cx=323.10, cy=235.34,
			...                   dist=[0.0541, -1.545, 0.003, -0.002, 5.536])
		"""
		self.intrinsics[res] = {
			'matrix': np.array([[fx,  0.0, cx ],
								[0.0, fy,  cy ],
								[0.0, 0.0, 1.0]]),
			'dist':   np.array(dist)
		}

	def _init_ros_node(self):
		try:
			rospy.init_node('olab_camera', anonymous=True)
		except Exception as e:
			self.logger.log(f'Error in _init_ros_node: {e}.', severity=olab_utils.SEVERITY_ERROR)			
		else:
			self.hasROSnode = True
			
	def defaultFromNone(self, val, default, test=None):
		"""Return a default value if val is None, optionally applying type conversion.

		Utility method for handling optional parameters with defaults and type coercion.

		Args:
			val: Input value to check. If None, default is returned.
			default: Default value to use when val is None.
			test (type, optional): Type conversion function to apply (e.g., int, float, str).
				If specified, the returned value is cast to this type.

		Returns:
			The value (or default) optionally converted to the specified type.

		Notes:
			- Used internally by add*() methods to handle optional resolution and framerate parameters.
		"""

		try:
			if (val is None):
				val = default
				
			if test in (int, float, str):
				return test(val)
			else: 
				return val				
		except Exception as e:
			# raise Exception(f'Error in defaultFromNone: {e}')
			self.logger.log(f'Error in defaultFromNone: {e}.', severity=olab_utils.SEVERITY_ERROR)

		
	def announceCondition(self):
		# Let our web server (and ros video publisher, and camAuto) know we have a new frame:
		with self.condition:
			self.condition.notify_all()

	def addAruco(self, idName=None, res_rows=None, res_cols=None, fps_target=5, calcRotations=True, postFunction=None, postFunctionArgs={}, configOverrides={}, ids_of_interest=None):
		"""Start ArUco marker detection in a separate thread.

		Creates and starts an _Aruco instance that continuously detects ArUco markers in
		camera frames. Results are stored in self.aruco[idName] and can be accessed by
		other threads. Detected markers can be optionally decorated on streamed frames.

		Args:
			idName (str): Unique identifier for this ArUco detection instance. Must match
				a dictionary in olab_utils.ARUCO_DICT.
			res_rows (int, optional): Processing resolution height. Defaults to camera's res_rows.
			res_cols (int, optional): Processing resolution width. Defaults to camera's res_cols.
			fps_target (int): Target detection framerate. Default 5.
			calcRotations (bool): Whether to calculate marker rotation vectors. Default True.
			postFunction (callable, optional): Callback function executed after each detection.
				Receives detection results as arguments.
			postFunctionArgs (dict): Additional keyword arguments passed to postFunction.
			configOverrides (dict): Override default ArUco drawing configuration from
				olab_utils.ARUCO_DRAWING_DEFAULTS.
			ids_of_interest (list, optional): List of specific marker IDs to detect. If None,
				detects all markers.

		Notes:
			- Prevents starting multiple instances with the same idName.
			- Detection results include marker corners, IDs, centers, and optionally rotations.
			- Uses camera intrinsics for undistortion if available.
			- To get distance/3D pose (not just the in-plane `rotations` angle), call
			  olab_utils.findTagPose() from your own postFunction with your own known
			  marker size -- see docs/usage_guide.md's ArUco section for a full example.
		"""
		# ids_of_interest None --> we don't have any specific IDs we're looking for.
		# Otherwise, this should be a list of integer IDs we're looking for.

		# Set colors to `None` to use the default colors from olab_utils.ARUCO_DICT
		configDefaults = olab_utils.ARUCO_DRAWING_DEFAULTS

		try:
			if (idName is None):
				self.logger.log('Error in addAruco: idName is None', severity=olab_utils.SEVERITY_ERROR)
				return

			if (idName in self.aruco):
				if (self.aruco[idName].isThreadActive):
					self.logger.log(f'An aruco thread for {idName} is already running.', severity=olab_utils.SEVERITY_ERROR)
					return

			configDict = configDefaults
			for k,v in configOverrides:
				configDict[k] = v
				if ('Color' in k):
					configDict[k] = self.defaultFromNone(v, olab_utils.ARUCO_DICT[idName]['color'], None)

			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)

			self.aruco[idName] = _Aruco(self, idName, res_rows, res_cols, int(fps_target), calcRotations, postFunction, postFunctionArgs, configDict, ids_of_interest)

			self.aruco[idName].start()

		except Exception as e:
			self.logger.log(f'Error in addAruco: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def addQR(self, idName=None, decoder='cv2', ids_of_interest=None,
			  res_rows=None, res_cols=None, fps_target=5, postFunction=None, postFunctionArgs=None, color=(0,0,255)):
		"""Start QR-code detection in a separate thread.

		Creates and starts a _QRCode instance that continuously decodes QR codes in camera
		frames -- unlike addBarcode()/pyzbar's generic 1D/2D scanning, this is QR-only and
		lets you pick the decoder. Results (payload data + corners) are stored in
		self.qr[idName], exactly the way ArUco/barcode results are stored -- this does NOT
		compute distance or pose itself; call olab_utils.findTagPose() from your own
		postFunction with your own known tag size, the same way it's done for ArUco (see
		docs/usage_guide.md) -- it works for any single planar tag's 4 corners.

		This is independent of addBarcode(): both may run at once and may both detect the
		same physical QR codes if both are started -- that's the caller's choice, not
		prevented. Use addQR() when you need reliable skewed-tag reads and/or a decoder
		choice; use addBarcode() for generic multi-symbology 1D/2D scanning.

		Args:
			idName (str): Unique identifier for this QR detection instance.
			decoder (str): 'cv2' (default) uses cv2.QRCodeDetector, which does its own
				perspective correction and whose corner order is anchored to the QR
				symbol's own finder-pattern structure -- safe to use for pose. 'pyzbar'
				uses the pyzbar library; its corner order is not reliably anchored to the
				symbol's frame, so if you compute pose from its corners, only
				distance/position are meaningful, not orientation -- see _QRCode's
				docstring.
			ids_of_interest (list, optional): If given, only these decoded payload
				strings are reported at all -- same parameter name/purpose as
				addAruco()'s ids_of_interest, just filtering on payload text instead
				of numeric marker IDs. If None, every decoded payload is reported.
			res_rows (int, optional): Processing resolution height. Defaults to camera's res_rows.
			res_cols (int, optional): Processing resolution width. Defaults to camera's res_cols.
			fps_target (int): Target detection framerate. Default 5.
			postFunction (callable, optional): Callback function executed after each detection.
			postFunctionArgs (dict, optional): Additional keyword arguments passed to
				postFunction. A copy is made internally before `idName` is added, so the
				dict you pass in is never mutated, and each QR instance gets its own
				independent args dict (never `None` or `{}` shared across instances).
			color (tuple): BGR color for drawing QR outlines. Default (0,0,255) red.

		Notes:
			- Prevents starting multiple instances with the same idName.
			- Detection results include payload data and corners.
		"""
		try:
			if (idName is None):
				self.logger.log('Error in addQR: idName is None', severity=olab_utils.SEVERITY_ERROR)
				return

			if (decoder not in ('cv2', 'pyzbar')):
				self.logger.log(f"Error in addQR: unknown decoder '{decoder}'; expected 'cv2' or 'pyzbar'", severity=olab_utils.SEVERITY_ERROR)
				return

			if (idName in self.qr):
				if (self.qr[idName].isThreadActive):
					self.logger.log(f'A QR thread for {idName} is already running.', severity=olab_utils.SEVERITY_ERROR)
					return

			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)

			self.qr[idName] = _QRCode(self, idName, decoder, ids_of_interest,
									   res_rows, res_cols, int(fps_target), postFunction, postFunctionArgs, color)
			self.qr[idName].start()

		except Exception as e:
			self.logger.log(f'Error in addQR: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def setPose(self, x, y, z, roll, pitch, yaw):
		"""Set the vehicle body's pose in the world (ENU) frame.

		This is the *vehicle's* pose (e.g. from flight-controller state), not the
		camera's own pose -- see setExtrinsics() for the camera's fixed mount offset
		relative to the vehicle body. Stored on self.pose (mirrors how self.intrinsics is
		stored/read directly), for your own postFunction to read and pass into
		olab_utils.findTagPoseGlobal()/findCameraPoseGlobal() -- see
		docs/usage_guide.md's ArUco section for the equivalent local-pose pattern this
		builds on.

		Args:
			x, y, z (float): Position of the body origin in the world (ENU) frame, meters.
			roll, pitch, yaw (float): Orientation of the body frame (FLU: x forward, y
				left, z up) relative to world (ENU), in radians, per REP-103.
		"""
		self.pose = {'position': (x, y, z), 'orientation': (roll, pitch, yaw)}

	def setExtrinsics(self, x, y, z, roll, pitch, yaw):
		"""Set the camera's fixed mount pose relative to the vehicle body frame.

		This is a one-time hardware calibration (camera position/orientation on the
		airframe) -- call it once after construction, or whenever the physical mount
		changes, not per-frame. Same units/convention as setPose(): meters, radians RPY,
		FLU-style relative frame. Stored on self.extrinsics, which defaults to identity
		(camera at the body origin, boresight aligned with the body's +x/forward axis)
		until this is called.

		Args:
			x, y, z (float): Position of the camera's mount origin in the body frame, meters.
			roll, pitch, yaw (float): Orientation of the camera-link frame relative to the
				body frame, in radians.
		"""
		self.extrinsics = {'position': (x, y, z), 'orientation': (roll, pitch, yaw)}

	def addBarcode(self, res_rows=None, res_cols=None, fps_target=5, postFunction=None, postFunctionArgs={}, color=(0,0,255)):
		"""Start barcode and QR code detection using pyzbar in a separate thread.

		Creates and starts a _Barcode instance that continuously scans for 1D/2D barcodes
		and QR codes in camera frames. Supports multiple barcode formats.

		Args:
			res_rows (int, optional): Processing resolution height. Defaults to camera's res_rows.
			res_cols (int, optional): Processing resolution width. Defaults to camera's res_cols.
			fps_target (int): Target detection framerate. Default 5.
			postFunction (callable, optional): Callback function executed after each detection.
			postFunctionArgs (dict): Additional keyword arguments passed to postFunction.
			color (tuple): BGR color for drawing barcode bounding boxes. Default (0,0,255) red.

		Notes:
			- Only one barcode detection instance ('default') is allowed at a time.
			- Detection results include barcode data, type, and corner coordinates.
		"""
		# Start pyzbar to track barcodes/QRcodes
		try:
			# self.barcode is a dictionary.  We'll limit ourselves to just 1 barcode thread. though.
			idName = 'default'
			
			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)
			
			self.barcode[idName] = _Barcode(self, idName, res_rows, res_cols, int(fps_target), postFunction, postFunctionArgs, color)
			self.barcode[idName].start() 

		except Exception as e:
			self.logger.log(f'Error in addBarcode: {e}.', severity=olab_utils.SEVERITY_ERROR)


	def addCalibrate(self, res_rows=None, res_cols=None, secBetweenImages=3, numImages=25, timeoutSec=20, pattern_size=(6,8), square_size=0.0254, postFunction=None):
		"""Start camera calibration process using a checkerboard pattern.

		Creates and starts a _Calibrate instance that captures multiple images of a
		checkerboard pattern to compute camera intrinsics (matrix and distortion coefficients).

		Args:
			res_rows (int, optional): Calibration resolution height. Defaults to camera's res_rows.
			res_cols (int, optional): Calibration resolution width. Defaults to camera's res_cols.
			secBetweenImages (int): Seconds to wait between capturing calibration images. Default 3.
			numImages (int): Number of checkerboard images to capture for calibration. Default 25.
			timeoutSec (int): Maximum seconds to wait for calibration completion. Default 20.
			pattern_size (tuple): Checkerboard interior corners (columns, rows). Default (6,8).
			square_size (float): Physical size of checkerboard squares in meters. Default 0.0254
				(1 inch).
			postFunction (callable, optional): Callback function executed after calibration
				completes with results.

		Notes:
			- Only one calibration instance ('default') can run at a time.
			- Checkerboard must be held steady and fully visible in each captured frame.
			- Results include camera matrix, distortion coefficients, and reprojection error.
		"""
		# Start an openCV camera calibration thread
		try:
			# self.calibrate is a dictionary.  We'll limit ourselves to just 1 calibration thread, though.
			idName = 'default'
			
			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)
			
			self.calibrate[idName] = _Calibrate(self, idName, res_rows, res_cols, secBetweenImages, numImages, timeoutSec, pattern_size, square_size, postFunction)
			self.calibrate[idName].start() 

		except Exception as e:
			self.logger.log(f'Error in addCalibrate: {e}.', severity=olab_utils.SEVERITY_ERROR)


	def addFaceDetect(self, res_rows=None, res_cols=None, fps_target=5, postFunction=None, postFunctionArgs={}, color=(0,255,255), conf_threshold=0.7, model_name='face_detection_yunet_2023mar.onnx', device='cpu', modelPath=None):
		"""Start face detection using OpenCV's built-in YuNet DNN model (cv2.FaceDetectorYN).

		Creates and starts a _FaceDetect instance that detects faces (plus 5 facial
		landmark points per face) in camera frames.

		Args:
			res_rows (int, optional): Processing resolution height. Defaults to camera's res_rows.
			res_cols (int, optional): Processing resolution width. Defaults to camera's res_cols.
			fps_target (int): Target detection framerate. Default 5.
			postFunction (callable, optional): Callback function executed after each detection.
			postFunctionArgs (dict): Additional keyword arguments passed to postFunction.
			color (tuple): BGR color for drawing face bounding boxes. Default (0,255,255) yellow.
			conf_threshold (float): Minimum confidence threshold for detections. Default 0.7.
			model_name (str): YuNet ONNX model filename, resolved against modelPath. Default
				'face_detection_yunet_2023mar.onnx' (fp32, higher accuracy). Pass
				'face_detection_yunet_2023mar_int8.onnx' for lower resource usage (e.g. on
				a Raspberry Pi).
			device (str): Compute device ('cpu' or 'gpu'). Default 'cpu'.
			modelPath (str, optional): Custom path to DNN model files. If None, uses default
				models bundled with olab_camera.

		Raises:
			Exception: any failure resolving/loading the YuNet model propagates directly
				(see _FaceDetect.__init__) -- caught here and logged as a single error;
				no entry is left in self.facedetect and no thread is started.

		Notes:
			- Only one face detection instance ('default') can run at a time.
			- Detection results include bounding boxes, confidence scores, and landmarks.
		"""
		# Start a cv2.FaceDetectorYN (YuNet)-based face detector
		try:
			# self.facedetect is a dictionary.  We'll limit ourselves to just 1 face detection thread. though.
			idName = 'default'

			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)

			self.facedetect[idName] = _FaceDetect(self, idName, res_rows, res_cols, int(fps_target), postFunction, postFunctionArgs, color, conf_threshold, model_name, device, modelPath)
			self.facedetect[idName].start()

		except Exception as e:
			self.logger.log(f'Error in addFaceDetect: {e}.', severity=olab_utils.SEVERITY_ERROR)
		
	
	def addROI(self, roiTrackerName=None, roiBB=None, fps_target=5, postFunction=None, color=(255,255,255)):
		"""Start region-of-interest (ROI) tracking using OpenCV object trackers.

		Creates and starts an _ROI instance that tracks a specified region across frames
		using OpenCV's tracking algorithms (e.g., KCF, CSRT, MedianFlow).

		Args:
			roiTrackerName (str): OpenCV tracker algorithm name. Examples: 'KCF', 'CSRT',
				'MedianFlow', 'MOSSE'.
			roiBB (tuple): Initial bounding box as (x, y, width, height) in pixels.
			fps_target (int): Target tracking framerate. Default 5.
			postFunction (callable, optional): Callback function executed after each tracking
				update with current bounding box.
			color (tuple): BGR color for drawing tracking box. Default (255,255,255) white.

		Notes:
			- Only one ROI tracking instance ('default') can run at a time.
			- Tracker must be initialized with a valid bounding box.
			- Tracking may fail if object moves out of frame or appearance changes drastically.
		"""
		# Start OpenCV object tracker using the supplied bounding box coordinates
		try:
			if (roiTrackerName is None):
				self.logger.log('Error in addROI: tracker is None', severity=olab_utils.SEVERITY_ERROR)
				return

			if (roiBB is None):
				# This should be an integer 4-tuple, of the form `(x, y, w, h)`
				self.logger.log('Error in addROI: bb is None', severity=olab_utils.SEVERITY_ERROR)
				return
				
			# self.roi is a dictionary.  We'll limit ourselves to just 1 ROI thread. though.
			idName = 'default'
			self.roi[idName] = _ROI(self, idName, roiTrackerName, roiBB, int(fps_target), postFunction, color)
			self.roi[idName].start() 

		except Exception as e:
			self.logger.log(f'Error in addROI: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def addTimelapse(self, outputDir=None, secBetwPhotos=30, timeLimitSec=None, delayStartSec=0, res_rows=None, res_cols=None, postPostFunction=None):
		"""Start automatic timelapse photography to capture images at regular intervals.

		Creates and starts a _Timelapse instance that periodically saves camera frames
		to disk for creating timelapse videos.

		Args:
			outputDir (str): Directory path where timelapse images will be saved. Required.
			secBetwPhotos (int): Seconds between capturing consecutive photos. Default 30.
			timeLimitSec (int, optional): Maximum duration in seconds for timelapse capture.
				If None, runs indefinitely until stopped.
			delayStartSec (int): Seconds to delay before starting timelapse. Default 0.
			res_rows (int, optional): Image resolution height. Defaults to camera's res_rows.
			res_cols (int, optional): Image resolution width. Defaults to camera's res_cols.
			postPostFunction (callable, optional): Callback function executed after each
				photo is saved.

		Notes:
			- Only one timelapse instance ('default') can run at a time.
			- Output directory is created if it doesn't exist.
			- Images are saved with timestamp filenames.
		"""
		# Start taking pictures periodically
		try:
			if (outputDir is None):
				self.logger.log('Error in addTimelapse: outputDir is None', severity=olab_utils.SEVERITY_ERROR)
				return
			
			if (timeLimitSec is not None):
				if (timeLimitSec <= 0):
					self.logger.log('Error in addTimelapse: timeLimitSec must be None or a positive number.', severity=olab_utils.SEVERITY_ERROR)
					return

			# self.timelapse is a dictionary.  We'll limit ourselves to just 1 timelapse thread, though.
			idName = 'default'
			
			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)
			
			self.timelapse[idName] = _Timelapse(self, idName, outputDir, secBetwPhotos, timeLimitSec, delayStartSec, res_rows, res_cols, postPostFunction)
			self.timelapse[idName].start() 

		except Exception as e:
			self.logger.log(f'Error in addTimelapse: {e}.', severity=olab_utils.SEVERITY_ERROR)
		

	def addUltralytics(self, idName=None, res_rows=None, res_cols=None, fps_target=None, postFunction=None, postFunctionArgs={}, color=(0,255,255), conf_threshold=0.25, model_name=None, verbose=False, drawBox=None, drawLabel=None, maskOutline=False):
		"""Start Ultralytics YOLO model inference for object detection, segmentation, or pose estimation.

		Creates and starts an _Ultralytics instance that runs YOLO models on camera frames
		for various computer vision tasks.

		Args:
			idName (str): Task type - must be one of: 'detect', 'segment', 'classify',
				'pose', 'obb', or 'track'.
			res_rows (int, optional): Processing resolution height. Defaults to camera's res_rows.
			res_cols (int, optional): Processing resolution width. Defaults to camera's res_cols.
			fps_target (int, optional): Target inference framerate. Defaults to camera's fps_target.
			postFunction (callable, optional): Callback function executed after each inference.
			postFunctionArgs (dict): Additional keyword arguments passed to postFunction.
			color (tuple): BGR color for drawing bounding boxes. Default (0,255,255) yellow.
			conf_threshold (float): Minimum confidence threshold for detections. Default 0.25.
			model_name (str): Ultralytics model filename (e.g., 'YOLO11n.pt', 'YOLO11n-seg.pt').
				Required.
			verbose (bool): Whether to print verbose model output. Default False.
			drawBox (bool, optional): Whether to draw bounding boxes on detections.
			drawLabel (bool, optional): Whether to draw class labels on detections.
			maskOutline (bool): For segmentation, draw mask outlines instead of filled masks.
				Default False.

		Notes:
			- Requires Ultralytics library installation.
			- Model is automatically downloaded if not found locally.
			- Different task types require corresponding model suffixes (e.g., -seg for segmentation).
		"""
		# Start an Ultralytics task ("detect", "segment", "classify", "pose", "obb", "track")
		try:
			if (idName not in ["detect", "segment", "classify", "pose", "obb", "track"]):
				# idName in this context is the same as Ultralytics' "task" description
				self.logger.log('Error in addUltralytics: idName not in ["detect", "segment", "classify", "pose", "obb", "track"]', severity=olab_utils.SEVERITY_ERROR)
				return

			if (model_name is None):
				# model_name should be something like "YOLO11n.pt" or "YOLO11n-cls.pt"
				self.logger.log('Error in addUltralytics: model_name must be specified', severity=olab_utils.SEVERITY_ERROR)
				return
				
			res_rows   = self.defaultFromNone(res_rows,   self.res_rows,   int)
			res_cols   = self.defaultFromNone(res_cols,   self.res_cols,   int)
			fps_target = self.defaultFromNone(fps_target, self.fps_target, int)
			
			self.ultralytics[idName] = _Ultralytics(self, idName, res_rows, res_cols, int(fps_target), postFunction, postFunctionArgs, color, conf_threshold, model_name, verbose, drawBox, drawLabel, maskOutline)
			self.ultralytics[idName].start() 
			
		except Exception as e:
			self.logger.log(f'Error in addUltralytics: {e}.', severity=olab_utils.SEVERITY_ERROR)
				

	# FIXME -- Remove this function
	def setCamFunction(self, functionType, framerate):
		# FIXME -- Allow multiple simultaneous cam modes
		#          Each runs in its own thread			
		if (functionType == 'PRECISION_LAND_ARUCO'):
			'''
			self.camMode     = 'P-LAND'
			'''
			# self.arucoDict and self.arucoParams are set in ????() function?
				

						
	def startStream(self, port, protocol='mjpeg', force=False, signalingMode='html'):
		"""Start a video streaming server on the specified port.

		Launches a threaded server that streams camera frames to connected clients.
		Only one protocol may be active at a time. Raises RuntimeError if a stream
		is already running unless force=True, which stops the current stream first.

		Args:
			port (int): TCP port number for the streaming server.
			protocol (str): Streaming protocol. One of 'mjpeg' (default),
				'websocket', or 'webrtc'.
			force (bool): If True, stop any currently active stream before starting
				the new one. Default False.
			signalingMode (str): WebRTC only. 'html' (default) serves a built-in
				HTML+JS page at GET /webrtc. 'json' returns a JSON descriptor
				instead, for integration into custom UIs.

		Notes:
			- Server runs in a daemon thread and stops when the main program exits.
			- Multiple clients can connect simultaneously (tracked via numStreams).
			- Frames are decorated with overlays (FPS, ArUco markers, etc.) before streaming.
			- IP filtering is applied based on ipAllowlist and ipBlocklist.
		"""
		try:
			_VALID_PROTOCOLS = ('mjpeg', 'websocket', 'webrtc')
			if protocol not in _VALID_PROTOCOLS:
				raise ValueError(f"Invalid protocol '{protocol}'. Choose from {_VALID_PROTOCOLS}.")

			if self.keepStreaming:
				if force:
					self.stopStream()
				else:
					raise RuntimeError(
						f"A '{self.activeProtocol}' stream is already active on this camera. "
						"Call stopStream() first, or pass force=True to replace it.")

			self.keepStreaming   = True
			self.activeProtocol = protocol
			self.streamPort     = port

			_ip = olab_utils.getIP()
			if protocol == 'mjpeg':
				self.streamURL = f'https://{_ip}:{port}/stream.mjpg'
			elif protocol == 'websocket':
				self.streamURL = f'wss://{_ip}:{port}/'
			elif protocol == 'webrtc':
				self.streamURL = f'https://{_ip}:{port}/webrtc'

			if protocol == 'mjpeg':
				strThread = threading.Thread(target=self._thread_stream_mjpeg, args=(port,))
			elif protocol == 'websocket':
				strThread = threading.Thread(target=self._thread_stream_websocket, args=(port,))
			elif protocol == 'webrtc':
				strThread = threading.Thread(target=self._thread_stream_webrtc, args=(port, signalingMode))

			strThread.daemon = True
			strThread.start()
		except Exception as e:
			self.keepStreaming   = False
			self.activeProtocol = None
			self.streamPort     = None
			self.streamURL      = None
			self.logger.log(f'Error in startStream: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def stopStream(self):
		"""Stop the active video streaming server.

		Sets keepStreaming to False, causing the streaming thread to terminate,
		and clears the active protocol.
		"""
		try:
			self.keepStreaming   = False
			self.activeProtocol = None
			self.streamPort     = None
			self.streamURL      = None
		except Exception as e:
			self.logger.log(f'Error in stopStream: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def streamIncr(self, incr):
		try:
			self.numStreams += incr
			self.numStreams = max(0, self.numStreams)

			self.reachback_pubCamStatus() 	
		except Exception as e:
			self.logger.log(f'Error in streamIncr: {e}.', severity=olab_utils.SEVERITY_ERROR)
			
	def startROStopic(self, imgTopic='/camera/image/raw', compImgTopic='/camera/image/compressed'):
		"""Start publishing camera frames to ROS image topics.

		Launches a threaded ROS publisher that converts camera frames to ROS Image and
		CompressedImage messages and publishes them to specified topics.

		Args:
			imgTopic (str, optional): ROS topic for raw Image messages. Default
				'/camera/image/raw'. Set to None to disable raw image publishing.
			compImgTopic (str, optional): ROS topic for CompressedImage messages (JPEG format).
				Default '/camera/image/compressed'. Set to None to disable compressed publishing.

		Notes:
			- Requires ROS node to be initialized (initROSnode=True in constructor).
			- At least one topic (imgTopic or compImgTopic) must be specified.
			- Publisher runs in a daemon thread using cv_bridge for message conversion.
			- Compressed images use JPEG encoding for reduced bandwidth.
		"""
		try:
			if (not self.hasROSnode):
				self.logger.log('No ROS node found.  Initialize camera with initROSnode=True.', severity=olab_utils.SEVERITY_WARNING)
				return

			if (imgTopic == compImgTopic == None):
				self.logger.log('No ROS image topic provided.', severity=olab_utils.SEVERITY_WARNING)
				return

			self.keepPublishing = True
			rosThread = threading.Thread(target=self._thread_ros, args=(imgTopic, compImgTopic,))
			rosThread.daemon = True
			rosThread.start()
		except Exception as e:
			# raise Exception(f'Error in startROStopic: {e}')
			self.logger.log(f'Error in startROStopic: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def stopROStopic(self):
		"""Stop publishing camera frames to ROS topics.

		Sets the keepPublishing flag to False, causing the ROS publishing thread to terminate.
		"""
		self.keepPublishing = False
									

	def getFrame(self):
		"""Return the most recent camera frame without copying.

		Returns:
			numpy.ndarray: The current frame from frameDeque (reference, not a copy).

		Warning:
			Returns a reference to the frame, not a copy. Use getFrameCopy() if you need
			to modify the frame or ensure it won't change.
		"""
		# FIXME Need to do some error checking (can't copy `None`)
		# Maybe wait for condition if frame is currently None?
		return self.frameDeque[0]

	def getFrameNext(self, timeout=1):
		"""Wait for and return the next camera frame.

		Blocks until a new frame is captured or timeout expires.

		Args:
			timeout (int): Maximum seconds to wait for next frame. Default 1.

		Returns:
			numpy.ndarray: The next captured frame (reference, not a copy).

		Notes:
			- Uses threading.Condition to efficiently wait for frame updates.
			- Returns current frame if timeout expires before new frame arrives.
		"""

		with self.condition:
			self.condition.wait(timeout)

		return self.frameDeque[0]


	def _frameCopy(self, frame):
		return frame.copy()
		

	def _frameCopyGray(self, frame):
		# FIXME -- cv2.COLOR_BGR2GRAY?  Do we have RGB or BGR?
		return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY);
	

	def getFrameCopyNext(self, colorOption=None, resOption=None, timeout=1):
		"""Wait for the next camera frame and return a copy with optional transformations.

		Combines the waiting behavior of getFrameNext() with the transformation and copy
		functionality of getFrameCopy().

		Args:
			colorOption (str, optional): Color transformation (see getFrameCopy).
			resOption (tuple, optional): Target resolution (see getFrameCopy).
			timeout (int): Maximum seconds to wait for next frame. Default 1.

		Returns:
			numpy.ndarray: Copy of the next captured frame with transformations applied.
		"""

		with self.condition:
			self.condition.wait(timeout)

		return self.getFrameCopy(colorOption=colorOption, resOption=resOption)	
		 
				
	def getFrameCopy(self, colorOption=None, resOption=None):
		"""Return a copy of the most recent camera frame with optional transformations.

		Args:
			colorOption (str, optional): Color space transformation to apply.
				- None: Return frame in original color space (typically BGR).
				- 'gray': Convert to grayscale.
			resOption (tuple, optional): Target resolution as (width, height) in pixels.
				If specified, frame is resized to this resolution. If None, returns original size.

		Returns:
			numpy.ndarray: Copy of the camera frame with requested transformations applied.

		Notes:
			- Always returns a copy, never the original frame from frameDeque.
			- Color conversion happens before resizing if both options are specified.
			- Resizing uses cv2.resize with default interpolation.
		"""
		# FIXME Need to do some error checking (can't copy `None`) and apply options.
		
		if colorOption == resOption == None:
			# Just return a copy of the current frame
			return self._frameCopy(self.frameDeque[0])

		img = None
		if (colorOption == 'gray'):
			img = self._frameCopyGray(self.frameDeque[0])
				
		if (resOption is not None):
			# resize creates a copy
			if (img is None):
				img = cv2.resize(self.frameDeque[0], resOption)
			else:
				img = cv2.resize(img, resOption)
				
		return img	       	
	
	
	def lastFrameAge(self):
		"""Seconds since the most recent real frame was captured, or None if
		no frame has ever arrived (or this subclass's capture loop doesn't
		update _lastFrameTime — currently only CameraUSB does).

		This is a genuine "is the stream currently alive" signal — deliberately
		NOT the same thing as camOn (which only reflects whether the
		underlying capture object itself has been torn down; a stalled stream
		where reads keep silently failing can leave camOn True forever) or the
		fps dicts' .actual field (which only recomputes periodically and goes
		stale itself during a stall, rather than dropping to reflect one).
		"""
		if self._lastFrameTime is None:
			return None
		return time.time() - self._lastFrameTime

	def calcFramerate(self, fpsDict, threadType=None):
		'''
		Find the effective framerate for 'capture', 'stream', or 'publish'.
		Also, works for aruco dictionaries, roi, etc, as long as  
		fpsDict is defined by the _make_fps_dict class.
		Ex:  fpsDict = self.fps['capture']
		threadType is a string:  'capture', 'stream', 'aruco', 'roi', 'barcode', 'publish'
		'''
		try:
			if (fpsDict.numFrames == 0):
				fpsDict.startTime = datetime.datetime.now()
				
			fpsDict.numFrames += 1
			t_elapsed = (datetime.datetime.now() - fpsDict.startTime).total_seconds()
			if (t_elapsed >= fpsDict.recheckInterval):
				if (threadType == 'stream'):
					# Streams are inflating our FPS counts.  Divide by number of streams.
					numStreams = max(self.numStreams, 1)
					fpsDict.actual = int((fpsDict.numFrames / numStreams) / t_elapsed)				
				else:	
					fpsDict.actual = int(fpsDict.numFrames / t_elapsed)
				fpsDict.numFrames = 0
				
				self.reachback_pubCamStatus()
		except Exception as e:
			self.logger.log(f'Error in {threadType} calcFramerate: {e}.', severity=olab_utils.SEVERITY_ERROR)
		
	
	
	def addCircle(self, center, radius, thickness=3, color=(150, 25, 25)):
		'''
		Add a circle overlay to the video stream.

		Returns (decorationID, params) where params is a mutable dict.
		Modify params['center'], params['radius'], etc. to update dynamically.
		Call camera.removeDecoration(decorationID) to remove.
		'''
		params = {'center': center, 'radius': radius, 'thickness': thickness, 'color': color}
		decorationID = int(time.time() * 1000)

		def _decorate(img, **kwargs):
			olab_utils.drawCircle(img, params['center'], params['radius'], params['thickness'], params['color'])

		self.dec['dequeAdd'].append({'function': _decorate, 'idName': f'circle_{decorationID}', 'decorationID': decorationID})
		return (decorationID, params)

	def addText(self, text, position, fontScale=0.7, thickness=2, color=(255, 255, 255)):
		'''
		Add a text overlay to the video stream.

		Returns (decorationID, params) where params is a mutable dict.
		Modify params['text'], params['position'], etc. to update dynamically.
		Call camera.removeDecoration(decorationID) to remove.
		'''
		params = {'text': text, 'position': position, 'fontScale': fontScale, 'thickness': thickness, 'color': color}
		decorationID = int(time.time() * 1000)

		def _decorate(img, **kwargs):
			olab_utils.drawText(img, params['text'], params['position'], params['fontScale'], params['thickness'], params['color'])

		self.dec['dequeAdd'].append({'function': _decorate, 'idName': f'text_{decorationID}', 'decorationID': decorationID})
		return (decorationID, params)

	def removeDecoration(self, decorationID):
		'''
		Remove a decoration (circle, text, etc.) by its decorationID.
		'''
		self.dec['dequeRemove'].append(decorationID)

	def manageDecorationsDeque(self):			
		# Add from decorations request add deque
		while self.dec['dequeAdd']:
			self.dec['active'].append(self.dec['dequeAdd'].popleft())
			
		# Remove from decorations request remove deque
		while self.dec['dequeRemove']:
			decorationID = self.dec['dequeRemove'][0]
			
			for q in self.dec['active']:
				if q['decorationID'] == decorationID:
					self.dec['active'].remove(q)
					break
			
			self.dec['dequeRemove'].popleft()
								
		# Remove from decorations request edit deque
		# This should involve a delete and an add.
		while self.dec['dequeEdit']:
			# First remove, then add.
			idRemove = self.dec['dequeEdit'][0]['decorationID']
			
			for q in self.dec['active']:
				if q['decorationID'] == idRemove:
					self.dec['active'].remove(q)
					break

			self.dec['active'].append(self.dec['dequeEdit'].popleft())

		
	def decorateFrame(self, img):
		'''
		FIXME
		Need a list of *active* decoration types.  e.g., ['aruco', 'calibrate'].
		Then, in this function, we'll simply loop over the names in the list.
		Each name should have a function.
		self._decorateProtoFunc = {'aruco': self._decorateAruco, 'roi': self._decorateROI, ...}
		for name in self.activeDecorators:
			self._decorateProtoFunc[name](img)
		'''
			
		try:
			'''
			if (len(self.decorations['aruco']) > 0):
				for idName in self.decorations['aruco']:
					olab_utils.arucoDrawDetections(img, self.aruco[idName].deque[0]['corners'],
												   self.aruco[idName].deque[0]['ids'], 
												   self.aruco[idName].deque[0]['centers'], 
												   self.aruco[idName].deque[0]['rotations'], self.aruco[idName].config)
			if (len(self.decorations['roi']) > 0):
				for idName in self.decorations['roi']:
					if (self.roi[idName].deque[0]['success']):
						olab_utils.roiDrawBox(img, self.roi[idName].deque[0]['box'], self.roi[idName].deque[0]['color'])

			if (len(self.decorations['barcode']) > 0):
				# print(self.decorations['barcode'])
				for idName in self.decorations['barcode']:
					# print('idName:', idName, 'barcode[idName]:', self.barcode[idName].deque[0])
					# print(self.barcode[idName].deque[0])
					olab_utils.decorateBarcode(img, 
											   self.barcode[idName].deque[0]['corners'], 
											   self.barcode[idName].deque[0]['data'], 
											   self.barcode[idName].deque[0]['color'], addText=True)

			if (len(self.decorations['calibrate']) > 0):
				for idName in self.decorations['calibrate']:
					olab_utils.decorateCalibrate(img, 
												 self.calibrate[idName].deque[0]['checkerboard'], 
												 self.calibrate[idName].deque[0]['corners'], 
												 self.calibrate[idName].deque[0]['count'], 
												 self.calibrate[idName].deque[0]['img_x_y'], 
												 self.calibrate[idName].deque[0]['orig_x_y'], addText=True)
			'''

			'''
			self.dec helps us manage decorations
			self.dec['dequeAdd'] - A deque of decorations to be added.
				This will be a list of dictionaries.  
				Each dictionary should have a the following keys:
				- `decorationID`, whose value should be unique across the deque.
				- `decorationFunction` - A convenience function that will later call the appropriate decorator
					self.aruco[idName]._decorate(img, options)
					olab_utils.decorateText(img, options)
				- `idName`
				
			Allow decorating with text, shapes, etc.	
			'''

			# Add to self.dec['active'] from self.dec['dequeAdd'], 
			# Remove from self.dec['active'] from self.dec['dequeRemove']
			# Edit self.dec['active'] from self.dec['dequeEdit']
			self.manageDecorationsDeque()
			
			for d in self.dec['active']:
				d['function'](img = img, function = d['idName'])

		except Exception as e:
			self.logger.log(f'Error in decorateFrame: {e}.', severity=olab_utils.SEVERITY_ERROR)


		if (self.showFPS):
			cv2.putText(img, f"{str(self.fps['stream'].actual)}/{str(self.fps['capture'].actual)} fps",
						(int(20), int(20)),                             # left, down
						cv2.FONT_HERSHEY_SIMPLEX,
						0.5, (255, 255, 255), 1, cv2.LINE_AA)

		
		# FIXME -- Add some other text:
		# stream/capture fps    ArUco     ROI	
						
	def _thread_ros(self, imgTopic, compImgTopic):
		''' 
		See
		* https://wiki.ros.org/cv_bridge/Tutorials/ConvertingBetweenROSImagesAndOpenCVImagesPython
		* https://wiki.ros.org/rospy_tutorials/Tutorials/WritingImagePublisherSubscriber		
		'''
		
		try:
			if (imgTopic):
				# /camera/image/raw
				bridge = CvBridge()
				image_pub = rospy.Publisher(imgTopic, Image, queue_size=2)
			if (compImgTopic):
				# /camera/image/compressed
				comp_image_pub = rospy.Publisher(compImgTopic, CompressedImage, queue_size=2)
				
			while self.keepPublishing:
				with self.condition:
					success = self.condition.wait(ROSPUB_MAX_WAIT_TIME_SEC)

				# We don't get here until the wait condition has finished 
				if (success):
					'''
					FIXME -- Do we want to allow option to stream decorated frames?
					# Must use a copy if we decorate the frame.
					# Otherwise, our vision processing functions get messed up.
					myNumpyArray = np.frombuffer(self.getFrameCopy(), dtype=np.uint8).reshape(self.res_rows, self.res_cols, 3)
					# FIXME -- Do we really need to do all of this conversion?  Isn't getFrameCopy() sufficient?	
						
					# Add annotions/decorations
					# updates myNumpyArray in-place
					self.decorateFrame(myNumpyArray)
					'''
					myNumpyArray = np.frombuffer(self.getFrame(), dtype=np.uint8).reshape(self.res_rows, self.res_cols, 3)
					# FIXME -- Do we really need to do all of this conversion?  Isn't getFrameCopy() sufficient?					
					
					if (imgTopic):
						image_pub.publish(bridge.cv2_to_imgmsg(myNumpyArray, "bgr8"))	
					if (compImgTopic):
						msg = CompressedImage()
						msg.header.stamp = rospy.Time.now()
						msg.format = "jpeg"
						msg.data = np.array(cv2.imencode('.jpg', myNumpyArray)[1]).tostring()
						# Publish new image
						comp_image_pub.publish(msg)	
						
			self.logger.log('_thread_ros stopping', severity=olab_utils.SEVERITY_DEBUG)
					
		except Exception as e:
			# raise Exception(f'_thread_ros error: {e}')
			self.logger.log(f'_thread_ros error: {e}.', severity=olab_utils.SEVERITY_ERROR)
				
	def _thread_stream_mjpeg(self, portNumber):
		'''
		THIS IS A THREAD
		It starts/runs the MJPEG streaming server
		'''
		try:
			try:
				self._ensureSslPath()		# Generate a local cert now, if one doesn't exist yet.

				address = ('', portNumber)
				handler = partial(StreamingHandler, self)				# self --> This CamUSB instance
				server = StreamingServer(address, handler)

				# --- make this server secure (ssl/https) ---
				if ((sys.version_info.major == 3) and (sys.version_info.minor <= 7)):
					# ssl.wrap_socket was deprecated in Python 3.7
					# See https://github.com/eventlet/eventlet/issues/795
					server.socket = ssl.wrap_socket(
						server.socket,
						keyfile  = f'{self.sslPath}/ca.key',
						certfile = f'{self.sslPath}/ca.crt',
						server_side=True)
				else:
					# This is the newer way:
					ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
					ssl_context.load_cert_chain(
						keyfile  = f'{self.sslPath}/ca.key',
						certfile = f'{self.sslPath}/ca.crt')
					server.socket = ssl_context.wrap_socket(server.socket, server_side = True)
				# -------------------------------------------

				server.serve_forever()

			finally:
				self.logger.log('stopping _thread_stream_mjpeg thread', severity=olab_utils.SEVERITY_INFO)

		except Exception as e:
			self.logger.log(f'_thread_stream_mjpeg error: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def _thread_stream_websocket(self, portNumber):
		'''
		THIS IS A THREAD
		It starts/runs the WebSocket streaming server.
		'''
		if not _HAS_WEBSOCKETS:
			self.logger.log(
				"WebSocket streaming requires 'websockets'. "
				"Install with: pip install olab-camera[websocket]",
				severity=olab_utils.SEVERITY_ERROR)
			self.keepStreaming   = False
			self.activeProtocol = None
			return

		try:
			try:
				self._ensureSslPath()		# Generate a local cert now, if one doesn't exist yet.

				ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
				ssl_context.load_cert_chain(
					keyfile  = f'{self.sslPath}/ca.key',
					certfile = f'{self.sslPath}/ca.crt')

				ws_server = WebSocketStreamingServer(self)
				asyncio.run(ws_server.serve(portNumber, ssl_context))

			finally:
				self.logger.log('stopping _thread_stream_websocket thread', severity=olab_utils.SEVERITY_INFO)

		except Exception as e:
			self.logger.log(f'_thread_stream_websocket error: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def _thread_stream_webrtc(self, portNumber, signalingMode):
		'''
		THIS IS A THREAD
		It starts/runs the WebRTC signaling server and manages peer connections.
		'''
		if not _HAS_WEBRTC:
			self.logger.log(
				"WebRTC streaming requires 'aiortc' and 'aiohttp'. "
				"Install with: pip install olab-camera[webrtc]",
				severity=olab_utils.SEVERITY_ERROR)
			self.keepStreaming   = False
			self.activeProtocol = None
			return

		try:
			try:
				self._ensureSslPath()		# Generate a local cert now, if one doesn't exist yet.

				ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
				ssl_context.load_cert_chain(
					keyfile  = f'{self.sslPath}/ca.key',
					certfile = f'{self.sslPath}/ca.crt')

				webrtc_server = WebRTCStreamingServer(self, signalingMode)
				asyncio.run(webrtc_server.serve(portNumber, ssl_context))

			finally:
				self.logger.log('stopping _thread_stream_webrtc thread', severity=olab_utils.SEVERITY_INFO)

		except Exception as e:
			self.logger.log(f'_thread_stream_webrtc error: {e}.', severity=olab_utils.SEVERITY_ERROR)	
			
			
	def _zoomFunction_cv2(self, frame):
		''' 
		Apply digital zoom to input frame
		See `cropAndZoom(self, img)` from aaa_camclasses.py
		'''
		# https://stackoverflow.com/questions/50870405/how-can-i-zoom-my-webcam-in-open-cv-python
		try:

			# Crop
			img = frame[ self.zoomCropYmin:self.zoomCropYmax, self.zoomCropXmin:self.zoomCropXmax, :]

			# Resize to original shape
			# This was *close*, but was a couple of pixels off
			# self.frame = cv2.resize( img, (0, 0), fx=self.zoomLevel, fy=self.zoomLevel)
			frame = cv2.resize( img, (self.res_cols, self.res_rows), interpolation = cv2.INTER_LINEAR)
			
			return frame			
		except Exception as e:
			# raise Exception(f'_zoomFunction_cv2 error: {e}')
			self.logger.log(f'_zoomFunction_cv2 error: {e}.', severity=olab_utils.SEVERITY_ERROR)	
			return frame		# Just return the input?

	def _zoomFunction_pass(self, frame):
		return frame
		
			
	def _changeZoom(self, zoomLevel):
		'''
		This is shared between ROS (sim/clover), USB, and Voxl.  Pi has its own zoom.
		
		We need to set the `zoomCrop...` parameters each time the zoom level changes.
		Then, we crop/resize the image before writing/publishing (in the appropriate thread camClass thread).
		'''
		try:
			w = self.res_cols
			h = self.res_rows
			
			cx = w / 2
			cy = h / 2
			
			self.zoomCropXmin = int(round(cx - w/zoomLevel * 0.5))
			self.zoomCropXmax = int(round(cx + w/zoomLevel * 0.5))
			self.zoomCropYmin = int(round(cy - h/zoomLevel * 0.5))
			self.zoomCropYmax = int(round(cy + h/zoomLevel * 0.5))
						
			self.updateZoom(zoomLevel)
			
		except Exception as e:
			# raise Exception(f'Could not _changeZoom to {zoomLevel}x: {e}.')
			self.logger.log(f'Could not _changeZoom to {zoomLevel}x: {e}.', severity=olab_utils.SEVERITY_ERROR)							
			
			
	
	def updateResolution(self, rows, cols):
		"""Update internal resolution attributes after resolution change.

		This method updates the stored resolution values but does not change the actual
		camera resolution. Subclasses should call this after successfully changing hardware
		resolution settings.

		Args:
			rows (int): New image height in pixels.
			cols (int): New image width in pixels.
		"""
		self.res_rows   = int(rows)	# height
		self.res_cols   = int(cols)	# width

	def updateFramerate(self, framerate):
		"""Update internal framerate attribute after framerate change.

		This method updates the stored framerate value but does not change the actual
		camera framerate. Subclasses should call this after successfully changing hardware
		framerate settings.

		Args:
			framerate (int): New target framerate in frames per second.
		"""
		self.fps_target = int(framerate)

	def updateZoom(self, zoomLevel):
		"""Update internal zoom level and configure zoom processing function.

		This method updates the stored zoom level and selects the appropriate zoom
		processing function. For most cameras (USB, ROS, Voxl), this enables digital
		zoom via frame cropping and resizing. For Raspberry Pi cameras, zoom is handled
		in hardware.

		Args:
			zoomLevel (float): New zoom level (1.0 = no zoom, >1.0 = zoomed in).

		Notes:
			- Zoom levels > 1.01 activate digital zoom processing (_zoomFunction_cv2).
			- Zoom levels <= 1.01 use pass-through (_zoomFunction_pass).
			- Digital zoom crops the center region and resizes to original resolution.
		"""
		self.zoomLevel = zoomLevel

		# Set the zoom function to apply to each frame.
		# This is ignored by picam (it has a one-time zoom adjustment)
		if (self.zoomLevel > 1.01):
			self.zoomFunction = self._zoomFunction_cv2
		else:
			self.zoomFunction = self._zoomFunction_pass

	# was `takePhoto()`
	def takePhotoLocal(self, path=None, filename=None, colorOption=None, resOption=None, timeout=-1):
		"""Capture the current camera frame and save it to local disk as a JPEG image.

		Args:
			path (str, optional): Directory path where image will be saved. If None, saves to
				current working directory. Directory is created if it doesn't exist.
			filename (str, optional): Image filename (without path). If None, generates a
				timestamp-based filename in format 'YYYY-MM-DD_HH-MM-SS.jpg'.
			colorOption (str, optional): Color transformation (see getFrameCopy). Default None.
			resOption (tuple, optional): Target resolution as (width, height) (see getFrameCopy).
				Default None.
			timeout (int): If > 0, waits up to timeout seconds for the next frame before
				capturing. If <= 0, captures the current frame immediately. Default -1.

		Returns:
			tuple: (path, filename) if successful, (None, None) if error occurred.

		Notes:
			- Image is saved in JPEG format using cv2.imwrite.
			- If timeout > 0, uses threading.Condition to wait for next frame update.
			- Automatically creates output directory if it doesn't exist.
		"""
		try:
			if (timeout > 0):			
				myNumpyArray = self.getFrameCopyNext(colorOption=colorOption, resOption=resOption, timeout=timeout)
			else:
				myNumpyArray = self.getFrameCopy(colorOption=colorOption, resOption=resOption)
			
			if (filename is None):
				myTimestamp = datetime.datetime.today()
				# myDate = '{}'.format(myTimestamp.strftime('%Y-%m-%d'))
				# myTime = '{}'.format(myTimestamp.strftime('%H:%M:%S'))
					
				filename = "{}.jpg".format(myTimestamp.strftime('%Y-%m-%d_%H-%M-%S'))
			else:
				filename = filename.strip()
								
			if (path is None):
				path = ''
				pathAndFile = f'{filename}'
			else:
				# Make sure path ends in `/`
				path = olab_utils.setEndingChar(path, '/')
				pathAndFile = f'{path}{filename}'
			
			# Create directory (if it does not already exist)
			if (not os.path.exists(path)):
				print(f'Directory {path} does not exist.  Making it now.')            
				os.makedirs(path, exist_ok=True)
										
			cv2.imwrite(f'{pathAndFile}', myNumpyArray)

			# print(myNumpyArray)
			print(f'Saved image: {pathAndFile}')
			
			return (path, filename)
			
		except Exception as e:
			self.logger.log(f'Error taking photo: {e}', severity=olab_utils.SEVERITY_ERROR)							
			return (None, None)
		
			



	def recordVideoLocal(self, path=None, filename=None, fps=15, colorOption=None, resOption=None):
		"""Start recording frames from this camera to a local .mp4 file in a background thread.

		Args:
			path (str, optional): Directory path where video will be saved. If None, saves to
				current working directory. Directory is created if it doesn't exist.
			filename (str, optional): Video filename (without path). If None, generates a
				timestamp-based filename in format 'YYYY-MM-DD_HH-MM-SS.mp4'.
			fps (int, optional): Target recording framerate (frames pulled from getFrameCopy()
				at this rate, not necessarily the camera's own native framerate). Defaults to 15.
			colorOption (str, optional): Color transformation (see getFrameCopy). Default None.
			resOption (tuple, optional): Target resolution as (width, height) (see getFrameCopy).
				Default None.

		Returns:
			tuple: (path, filename) if recording started, (None, None) if error occurred
				(e.g. no frame available yet to determine video dimensions).

		Notes:
			- Recording runs in a background thread until stopRecordVideoLocal() is called.
			- Video is written in mp4 (mp4v fourcc) format using cv2.VideoWriter.
			- Only one recording may be active at a time per Camera instance — call
				stopRecordVideoLocal() before starting another.
		"""
		try:
			if getattr(self, '_videoRecordThread', None) is not None:
				self.logger.log('recordVideoLocal: a recording is already in progress on this camera', severity=olab_utils.SEVERITY_WARNING)
				return (None, None)

			firstFrame = self.getFrameCopy(colorOption=colorOption, resOption=resOption)
			if firstFrame is None:
				self.logger.log('recordVideoLocal: no frame available yet', severity=olab_utils.SEVERITY_ERROR)
				return (None, None)

			if (filename is None):
				myTimestamp = datetime.datetime.today()
				filename = "{}.mp4".format(myTimestamp.strftime('%Y-%m-%d_%H-%M-%S'))
			else:
				filename = filename.strip()

			if (path is None):
				path = ''
				pathAndFile = f'{filename}'
			else:
				path = olab_utils.setEndingChar(path, '/')
				pathAndFile = f'{path}{filename}'

			if (not os.path.exists(path)):
				os.makedirs(path, exist_ok=True)

			h, w = firstFrame.shape[:2]
			fourcc = cv2.VideoWriter_fourcc(*'mp4v')
			writer = cv2.VideoWriter(pathAndFile, fourcc, fps, (w, h))

			self._videoRecordStopEvent = threading.Event()
			self._videoRecordThread = threading.Thread(
				target=self._recordVideoLocalLoop,
				args=(writer, fps, colorOption, resOption),
				daemon=True,
			)
			self._videoRecordThread.start()

			print(f'Recording video to: {pathAndFile}')
			return (path, filename)

		except Exception as e:
			self.logger.log(f'Error starting video recording: {e}', severity=olab_utils.SEVERITY_ERROR)
			return (None, None)

	def _recordVideoLocalLoop(self, writer, fps, colorOption, resOption):
		"""Background thread body for recordVideoLocal(). Not asyncio —
		cv2.VideoWriter.write() is a blocking call, same reasoning olab_camera
		already uses for its own _captureLoop() background thread."""
		interval = 1.0 / fps
		try:
			while not self._videoRecordStopEvent.is_set():
				frame = self.getFrameCopy(colorOption=colorOption, resOption=resOption)
				if frame is not None:
					writer.write(frame)
				self._videoRecordStopEvent.wait(interval)
		finally:
			writer.release()

	def stopRecordVideoLocal(self, timeout=5.0):
		"""Stop a recording started by recordVideoLocal() and finalize the video file.

		Safe to call even if no recording is in progress (no-op).
		"""
		stopEvent = getattr(self, '_videoRecordStopEvent', None)
		if stopEvent is None:
			return
		stopEvent.set()
		thread = getattr(self, '_videoRecordThread', None)
		if thread is not None:
			thread.join(timeout=timeout)
		self._videoRecordStopEvent = None
		self._videoRecordThread = None


