"""Gazebo Transport camera backend."""

import time

import cv2
import numpy as np

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .camera import Camera, STREAM_MAX_WAIT_TIME_SEC


class CameraGazebo(Camera):
	"""Gazebo Transport camera subscriber implementation for gz.msgs.Image topics.

	This class provides an interface to Gazebo simulation cameras that publish on
	Gazebo Transport topics. It uses lazy imports for the versioned Gazebo Python
	modules so that non-Gazebo users do not need those packages installed just to
	import olab_camera.

	Key Differences from Base Camera:
		- No direct hardware access - subscribes to a Gazebo Transport image topic
		- Gazebo Python modules are imported lazily at start() time
		- Supports multiple Gazebo releases by auto-discovering gz.transport<N> and gz.msgs<N>
		- Cannot change source resolution or framerate (determined by the Gazebo publisher)
		- Digital zoom only (crops and resizes frames in software)

	Supported Sources:
		- Gazebo Harmonic camera topics publishing gz.msgs.Image
		- Other Gazebo releases with versioned Python modules installed

	Usage Example:
		>>> cam = CameraGazebo(
		...     topic='/world/default/model/pantilt/link/tilt_link/sensor/camera/image',
		...     paramDict={'res_rows': 480, 'res_cols': 640, 'fps_target': 30, 'outputPort': 8000})
		>>> cam.start(startStream=True, port=8000)
		>>> frame = cam.getFrameCopy()
		>>> cam.shutdown()
	"""

	def __init__(self, topic=None, assetID=None, paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort':8000},
				 logger=None, sslPath=None, pubCamStatusFunction=None, initROSnode=False, showFPS=True,
				 ipAllowlist=[], ipBlocklist=[], transport_module=None, msgs_module=None):
		"""Initialize Gazebo camera subscriber interface.

		Args:
			topic (str, optional): Gazebo Transport topic publishing gz.msgs.Image.
			assetID (str, optional): Asset identifier for formatting topic strings with {}.
			paramDict (dict, optional): Configuration dictionary. Defaults to 480x640 @ 30fps.
			logger (Logger, optional): Logger instance. If None, creates default logger.
			sslPath (str, optional): Path to SSL certificates for HTTPS streaming.
			pubCamStatusFunction (callable, optional): Callback function to publish camera status.
			initROSnode (bool, optional): Whether to initialize ROS node. Defaults to False.
			showFPS (bool, optional): Whether to display FPS information. Defaults to True.
			ipAllowlist (list, optional): List of allowed IP addresses for streaming.
			ipBlocklist (list, optional): List of blocked IP addresses for streaming.
			transport_module (str, optional): Explicit Gazebo transport module name such as
				'gz.transport13'. If None, the highest installed version is discovered lazily.
			msgs_module (str, optional): Explicit Gazebo msgs module name such as
				'gz.msgs10'. If None, the highest installed version is discovered lazily.
		"""
		super().__init__(paramDict, logger, sslPath, pubCamStatusFunction, initROSnode, showFPS, ipAllowlist, ipBlocklist)

		self.topic = topic
		self.topicTemplate = topic
		self.assetID = assetID

		self._transport_module_name = transport_module
		self._msgs_module_name = msgs_module
		self._gz_node_class = None
		self._gz_image_class = None
		self._gz_node = None
		self._is_subscribed = False
		self._unsupported_pixel_formats = set()

	@staticmethod
	def _discover_gz_versioned_module(prefix):
		"""Return the highest installed Gazebo Python module matching gz.<prefix><N>."""
		import importlib
		import pkgutil
		import re

		gz_pkg = importlib.import_module('gz')
		pattern = re.compile(rf'^{re.escape(prefix)}(\d+)$')
		candidates = []

		for module_info in pkgutil.iter_modules(gz_pkg.__path__):
			match = pattern.match(module_info.name)
			if match:
				candidates.append((int(match.group(1)), module_info.name))

		if not candidates:
			raise ImportError(f'Could not find any Gazebo Python modules matching gz.{prefix}<N>.')

		candidates.sort()
		return f"gz.{candidates[-1][1]}"

	def _lazy_import_gz_modules(self):
		"""Import the required Gazebo Python modules only when CameraGazebo is used."""
		if (self._gz_node_class is not None) and (self._gz_image_class is not None):
			return

		try:
			import importlib

			if self._transport_module_name is None:
				self._transport_module_name = self._discover_gz_versioned_module('transport')
			if self._msgs_module_name is None:
				self._msgs_module_name = self._discover_gz_versioned_module('msgs')

			transport_module = importlib.import_module(self._transport_module_name)
			image_module = importlib.import_module(f'{self._msgs_module_name}.image_pb2')

			self._gz_node_class = transport_module.Node
			self._gz_image_class = image_module.Image

		except Exception as e:
			raise ImportError(
				'Could not import Gazebo Python bindings. Install the appropriate '
				'gz.transport<N> and gz.msgs<N> packages for your Gazebo release.'
			) from e

	def _convert_gz_image_to_frame(self, msg):
		"""Convert gz.msgs.Image to an OpenCV-compatible BGR or grayscale frame."""
		pixel_format = msg.pixel_format_type
		frame = np.frombuffer(msg.data, dtype=np.uint8)
		row_step = int(msg.step) if int(msg.step) > 0 else len(msg.data)

		if pixel_format == 3:   # RGB_INT8
			frame = frame.reshape((msg.height, row_step))[:, :int(msg.width) * 3]
			frame = frame.reshape((msg.height, msg.width, 3))
			return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
		if pixel_format == 8:   # BGR_INT8
			frame = frame.reshape((msg.height, row_step))[:, :int(msg.width) * 3]
			return frame.reshape((msg.height, msg.width, 3))
		if pixel_format == 4:   # RGBA_INT8
			frame = frame.reshape((msg.height, row_step))[:, :int(msg.width) * 4]
			frame = frame.reshape((msg.height, msg.width, 4))
			return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
		if pixel_format == 5:   # BGRA_INT8
			frame = frame.reshape((msg.height, row_step))[:, :int(msg.width) * 4]
			frame = frame.reshape((msg.height, msg.width, 4))
			return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
		if pixel_format == 1:   # L_INT8
			frame = frame.reshape((msg.height, row_step))[:, :int(msg.width)]
			return frame.reshape((msg.height, msg.width))

		raise ValueError(f'Unsupported Gazebo pixel format: {pixel_format}')

	def callback_Image(self, msg):
		"""Callback for Gazebo Transport image messages."""
		try:
			frame = self._convert_gz_image_to_frame(msg)

			# Keep stored dimensions synchronized with the actual Gazebo camera output.
			if (self.res_rows != int(msg.height)) or (self.res_cols != int(msg.width)):
				self.updateResolution(msg.height, msg.width)
				if self.zoomLevel > 1.01:
					self._changeZoom(self.zoomLevel)

			frame = self.zoomFunction(frame)

			self.frameDeque.append(frame)
			self.announceCondition()
			self.calcFramerate(self.fps['capture'], 'capture')

		except ValueError as e:
			if msg.pixel_format_type not in self._unsupported_pixel_formats:
				self._unsupported_pixel_formats.add(msg.pixel_format_type)
				self.logger.log(f'Error in gazebo image callback: {e}', severity=olab_utils.SEVERITY_ERROR)
		except Exception as e:
			self.logger.log(f'Error in gazebo image callback: {e}', severity=olab_utils.SEVERITY_ERROR)

	def _changeFramerate(self, req_framerate):
		try:
			if (req_framerate == self.fps_target):
				return (True, '')

			if hasattr(self, 'fpsMin') and hasattr(self, 'fpsMax'):
				if not (self.fpsMin <= req_framerate <= self.fpsMax):
					return (False, 'Gazebo framerate request is outside configured bounds')

			return (False, 'cannot change Gazebo framerate from olab_camera; update the Gazebo sensor or world instead')
		except Exception as e:
			return (False, f'Could not change Gazebo framerate: {e}')

	def _changeResolution(self, req_height, req_width):
		try:
			if ((self.res_cols, self.res_rows) != (req_width, req_height)):
				return (False, 'cannot change Gazebo resolution from olab_camera; update the Gazebo sensor or world instead')
			else:
				return (False, f'Gazebo resolution is already {req_width}x{req_height}.')
		except Exception as e:
			return (False, f'Could not change Gazebo resolution to {req_width}x{req_height}: {e}.')

	def start(self, assetID=None, res_rows=None, res_cols=None, framerate=None, startStream=False, port=None, protocol='mjpeg', imgTopic=None, compImgTopic=None):
		"""Start subscribing to a Gazebo Transport image topic."""
		try:
			previous_topic = self.topic
			self.res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			self.res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)
			self.fps_target = self.defaultFromNone(framerate, self.fps_target, int)
			port = self.defaultFromNone(port, self.outputPort)
			assetID = self.defaultFromNone(assetID, self.assetID)

			if self.topicTemplate is not None:
				if ('{}' in self.topicTemplate) and (assetID is None):
					raise Exception('Topic template requires assetID, but assetID was not provided.')
				self.topic = self.topicTemplate.format(assetID)

			if self.topic is None:
				raise Exception('No Gazebo image topic provided.')

			self._lazy_import_gz_modules()

			if self._gz_node is None:
				self._gz_node = self._gz_node_class()

			if self._is_subscribed and (previous_topic is not None):
				self._gz_node.unsubscribe(previous_topic)
				self._is_subscribed = False

			self.camOn = True
			self._gz_node.subscribe(self._gz_image_class, self.topic, self.callback_Image)
			self._is_subscribed = True

			if startStream:
				if (port is None):
					raise Exception('cannot stream when port is None')
				else:
					self.startStream(port, protocol=protocol)

			if (imgTopic is not None) or (compImgTopic is not None):
				self.startROStopic(imgTopic=imgTopic, compImgTopic=compImgTopic)

			self.reachback_pubCamStatus()
		except Exception as e:
			self.camOn = False
			self.logger.log(f'Error in camera start: {e}.', severity=olab_utils.SEVERITY_ERROR)

	def stop(self, stopStream=True):
		"""Stop Gazebo topic subscription and optionally stop streaming."""
		try:
			self.camOn = False
			self.stopROStopic()

			if self._is_subscribed and (self._gz_node is not None) and (self.topic is not None):
				self._gz_node.unsubscribe(self.topic)
				self._is_subscribed = False

			if stopStream:
				self.stopStream()
		except Exception as e:
			self.logger.log(f'Could not stop cameraGazebo: {e}', severity=olab_utils.SEVERITY_ERROR)

	def shutdown(self):
		"""Shutdown camera and give background streaming threads time to exit."""
		self.stop()
		time.sleep(STREAM_MAX_WAIT_TIME_SEC + 1)

	def changeResolutionFramerate(self, res_rows=None, res_cols=None, framerate=None):
		"""Attempt to change resolution and/or framerate.

		Gazebo camera source properties are controlled by the simulation, so this method
		keeps the same interface as other camera classes but reports that the requested
		change must be made in the Gazebo sensor/world configuration instead.
		"""
		try:
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
		"""Change camera zoom level using digital zoom (crop and resize)."""
		self._changeZoom(zoomLevel)


