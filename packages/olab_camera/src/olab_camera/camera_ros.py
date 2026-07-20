"""ROS camera subscriber backend."""

import time

import cv2
import numpy as np

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .camera import Camera, STREAM_MAX_WAIT_TIME_SEC, rospy, CompressedImage


class CameraROS(Camera):
	"""ROS camera subscriber/publisher implementation for compressed image topics.

	This class provides an interface to cameras that publish to ROS CompressedImage topics,
	including Gazebo simulation cameras and real hardware cameras running ROS (e.g., Clover
	quadcopter). Instead of directly accessing camera hardware, it subscribes to an existing
	ROS topic and makes frames available through the standard Camera interface.

	Key Differences from Base Camera:
		- No direct hardware access - subscribes to ROS CompressedImage topic
		- Cannot change resolution or framerate (determined by publisher)
		- Digital zoom only (crops and resizes frames in software)
		- Requires active ROS master and camera publisher
		- Frame timing depends on topic publication rate

	Supported Sources:
		- Gazebo simulation cameras (e.g., /sim_cam/image_raw/compressed)
		- Clover main camera (e.g., /main_camera/image_raw/compressed)
		- Optical flow debug camera (e.g., /optical_flow/debug/compressed)
		- Any ROS node publishing sensor_msgs/CompressedImage

	Usage Example:
		>>> # Create ROS camera subscriber
		>>> cam = CameraROS(paramDict={'outputPort': 8001})
		>>> cam.topic = "/main_camera/image_raw/compressed"
		>>>
		>>> # Start subscribing and optionally stream via HTTP
		>>> cam.start(startStream=True, port=8001)
		>>>
		>>> # Get current frame
		>>> frame = cam.getFrameCopy()
		>>>
		>>> # Apply digital zoom
		>>> cam.changeZoom(2.0)
		>>>
		>>> # Stop subscribing
		>>> cam.stop()

	Important Notes:
		- Requires ROS environment and active roscore
		- Topic must be publishing sensor_msgs/CompressedImage messages
		- Resolution/framerate cannot be changed (read-only from topic)
		- Frame availability depends on topic publication rate
		- Use {assetID} placeholder in topic string for multi-robot systems
		- Zoom is digital only (crops and resizes frames)

	Attributes:
		topic (str): ROS topic name to subscribe to for compressed images.
		camTopicSubscriber (rospy.Subscriber): ROS subscriber instance.
	"""
	
	def __init__(self, assetID=None, paramDict={}, logger=None, sslPath=None, pubCamStatusFunction=None, showFPS=True,
				 ipAllowlist=[], ipBlocklist=[]):
		"""Initialize ROS camera subscriber interface.

		Args:
			assetID (str, optional): Asset identifier for formatting topic string (replaces {} placeholder).
			paramDict (dict, optional): Configuration dictionary. Defaults to empty dict.
				Supported keys: 'res_rows', 'res_cols', 'fps_target', 'outputPort'.
			logger (Logger, optional): Logger instance. If None, creates default logger.
			sslPath (str, optional): Path to SSL certificates for HTTPS streaming.
			pubCamStatusFunction (callable, optional): Callback function to publish camera status.
			showFPS (bool, optional): Whether to display FPS information. Defaults to True.
			ipAllowlist (list, optional): List of allowed IP addresses for streaming.
			ipBlocklist (list, optional): List of blocked IP addresses for streaming.

		Notes:
			- Does not connect to ROS topic in __init__ (use start() to begin subscription).
			- Topic string should be set on instance before calling start().
			- Resolution and framerate in paramDict are for reference only; actual values
			  come from the ROS topic publisher.
		"""
		super().__init__(paramDict, logger, sslPath, pubCamStatusFunction, showFPS, ipAllowlist, ipBlocklist)

		# See vehicles.json, which includes a topic for Clover and Sim cameras.
		# In make_asset class we replace {} with the assetID (where applicable)
		# self.topic = "/soar_rover/{}/sim_cam/image_raw/compressed"
		# self.topic = "/main_camera/image_raw/compressed"
		# self.topic = "/optical_flow/debug/compressed"			
		
		# from gazebo_msgs.msg import LinkState
		# from gazebo_msgs.srv import SetLinkState	
		from gazebo_msgs.msg import ODEJointProperties
		from gazebo_msgs.srv import SetJointProperties	

		self.camTopicSubscriber = None
		
	def callback_CompressedImage(self, msg):
		"""Callback method for receiving CompressedImage messages from ROS topic.

		This method is called automatically by rospy when a new CompressedImage message
		arrives on the subscribed topic. It decodes the JPEG data, applies zoom if active,
		and adds the frame to the deque.

		Args:
			msg (sensor_msgs.msg.CompressedImage): ROS CompressedImage message containing
				JPEG-encoded frame data.

		Notes:
			- Automatically decodes JPEG data to BGR numpy array using cv2.imdecode.
			- Applies digital zoom (crop and resize) if zoom level > 1.0.
			- Appends frame to self.frameDeque for access by other threads.
			- Triggers threading.Condition notification for threads waiting on new frames.
			- Calculates and updates capture framerate statistics.
			- This is a callback method, not meant to be called directly.
		"""
		try:
			# FIXME -- Do we need to do all of these conversions???
			
			#### direct conversion to CV2 ####
			# np_arr = np.fromstring(msg.data, np.uint8)
			np_arr = np.frombuffer(msg.data, dtype=np.uint8)  # .reshape(self.res_rows, self.res_cols, 3)
			
			# image = cv2.imdecode(np_arr, cv2.CV_LOAD_IMAGE_COLOR)
			frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
			
			# Are we zooming?
			frame = self.zoomFunction(frame)

			self.frameDeque.append(frame) # OpenCV >= 3.0:
					
			# Only call this if we actually have optical flow capabilities/hardware
			# if (self.vhcl.useOptFlowCam):
			#	self.vhcl.optFlowPub(self.myNumpyArray, self.vhcl.oflow.camera_matrix, self.vhcl.oflow.dist_coeffs)

			self.announceCondition()
			
			self.calcFramerate(self.fps['capture'], 'capture')
			
		except Exception as e:
			# raise Exception(f'Error in sim compressed image callback: {e}')
			self.logger.log(f'Error in sim compressed image callback: {e}', severity=olab_utils.SEVERITY_ERROR)

		
	def _changeFramerate(self, req_framerate):
		try:			
			if (req_framerate == self.fps_target):
				# Nothing to change
				return (True, '')

			# FIXME -- I don't think we can actually change ROS framerate	
			if (self.fpsMin <= req_framerate <= self.fpsMax):
				# Do something here if we can?
				return (False, 'cannot change ROS framerate')
			else:
				return (False, 'ROS framerate is at limit')
					
		except Exception as e:
			return (False, f'Could not change ROS framerate: {e}')
		
	def _changeResolution(self, req_height, req_width):
		try:
			# FIXME -- How to get current actual resolution?
			if ((self.res_cols, self.res_rows) != (req_width, req_height)):
				# FIXME -- I don't think we can actually change ROS resolution
				return (False, 'cannot change ROS resolution')
			else:
				return (False, f'ROS resolution is already {req_width}x{req_height}.')
		except Exception as e:
			return (False, f'Could not change ROS resolution to {req_width}x{req_height}: {e}.')
			
	def changeResolutionFramerate(self, res_rows=None, res_cols=None, framerate=None):
		'''
		Change resolution and/or framerate	
		NOTE: I don't think either is possible with ROS compressed image topic	
		'''
		try:
			# If user didn't provide a parameter, use the default value
			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)
			framerate = self.defaultFromNone(framerate, self.fps_target, int)
			
			(successFr,  msgFr)  = self._changeFramerate(framerate)			
			(successRes, msgRes) = self._changeResolution(res_rows, res_cols)
				
			if ((not successFr) or (not successRes)):
				raise Exception(f'{msgFr} {msgRes}')	
			
		except Exception as e:
			self.logger.log(f'Failed to change to {res_rows} rows, {res_cols} cols, {framerate} framerate: {e}', severity=olab_utils.SEVERITY_ERROR)

	def changeZoom(self, zoomLevel):
		"""Change camera zoom level using digital zoom (crop and resize).

		Applies digital zoom by cropping the center region of each frame and resizing
		to the original resolution. This is done in software for each frame via
		the _changeZoom() method shared with CameraUSB and Voxl cameras.

		Args:
			zoomLevel (float): Zoom level where 1.0 = no zoom, 2.0 = 2x zoom, etc.
				Higher values zoom in more (crop more of the frame).

		Notes:
			- Digital zoom reduces effective resolution (not true optical zoom).
			- Zoom is applied to each frame after receiving from ROS topic.
			- Cannot control zoom at the camera source (Gazebo or hardware).
			- Crop is centered on the frame.
			- For Gazebo cameras, consider changing camera properties in simulation instead.
		"""
		# This requires a numpy zoom/crop for each frame?
		# Or, is it possible to change zoom in Gazebo?
		self._changeZoom(zoomLevel)
			

	def shutdown(self):
		'''
		Might be as simple as calling self.stop()
		'''
		self.stop()
		time.sleep(STREAM_MAX_WAIT_TIME_SEC + 1)
			
			
	def start(self, assetID=None, startStream=False, port=None, protocol='mjpeg', **kwargs):
		"""Start subscribing to ROS CompressedImage topic.

		Creates a ROS subscriber to the configured topic and begins receiving camera frames.
		Frames arrive via the callback_CompressedImage() callback method. Optionally starts
		a streaming server to re-broadcast frames.

		Args:
			assetID (str, optional): Asset identifier to format into topic string (replaces {}).
			startStream (bool, optional): Whether to start streaming. Defaults to False.
			port (int, optional): Port number for streaming server. Required if startStream=True.
			protocol (str, optional): Streaming protocol — 'mjpeg' (default), 'websocket',
				or 'webrtc'. Only used when startStream=True.
			**kwargs: Additional keyword arguments (ignored).

		Raises:
			Exception: If startStream=True but port=None.
			Exception: If ROS topic subscription fails.

		Notes:
			- Sets self.camOn = True to indicate active subscription.
			- If self.topic contains {} placeholder, it's replaced with assetID.
			- Frames are received asynchronously via callback.
			- Does not publish to ROS topics (already reading from one).
			- Stream uses HTTPS/WSS with SSL certificates from self.sslPath.
		"""
		try:			
			# If user didn't provide a parameter, use the default value
			port         = self.defaultFromNone(port, self.outputPort)
			
			if (hasattr(self, 'topic')):
				# topic = "/soar_rover/{}/sim_cam/image_raw/compressed"
				# topic = "/main_camera/image_raw/compressed"
				# topic = "/optical_flow/debug/compressed"			
				self.topic = self.topic.format(assetID)
					
			print(self.topic)
					
			self.camOn = True

			self.camTopicSubscriber = rospy.Subscriber(self.topic, CompressedImage, self.callback_CompressedImage)

			# Start streaming?
			if (startStream):
				if (port is None):
					raise Exception('cannot stream when port is None')
				else:
					self.startStream(port, protocol=protocol)
			# NOTE: No need to publish to compressed image topic (we're already subscribing to it!)

			self.reachback_pubCamStatus()
		except Exception as e:
			# raise Exception(f'Error in camera start: {e}')
			self.logger.log(f'Error in camera start: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def stop(self):
		try:
			self.stopStream()
			if (self.camTopicSubscriber is not None):
				self.camTopicSubscriber.unregister()
		except Exception as e:
			# raise Exception(f'Could not stop cameraROS: {e}')
			self.logger.log(f'Could not stop cameraROS: {e}', severity=olab_utils.SEVERITY_ERROR)
			
						
			
