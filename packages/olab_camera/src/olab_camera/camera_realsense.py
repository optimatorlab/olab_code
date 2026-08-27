"""Intel RealSense camera backend (color + optional depth + optional IMU)."""

import threading
import time
from collections import deque

import cv2
import numpy as np

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .camera import Camera, STREAM_MAX_WAIT_TIME_SEC

try:
	import pyrealsense2 as rs
except Exception as e:
	print(f'INFO: pyrealsense2 is not installed and was not imported.  You may ignore this message.  Unless you are using a RealSense camera you do not need pyrealsense2.')
	rs = None


class CameraRealSense(Camera):
	"""Intel RealSense camera implementation using the pyrealsense2 SDK.

	Sibling to CameraUSB/CameraPi/CameraROS/CameraGazebo, subclassing the shared
	Camera base class. Developed/tested against a D435i, general enough for other
	RealSense models exposing color, depth, and motion (accel/gyro) streams.

	Color is the default stream and flows through self.frameDeque exactly like
	every other camera class -- addAruco()/startStream()/decorations all work
	unmodified. Depth and IMU are both independently opt-in (enableDepth=False,
	enableIMU=False by default) so a color-only caller pays no extra cost.

	Depth is exposed two ways:
		- Raw metric depth (meters, float32) via self.depthDeque / getDepthFrame()
		  / getDepthFrameCopy() -- for algorithmic use (e.g. obstacle avoidance),
		  never pushed through the video/streaming pipeline.
		- A colorized live view, by constructing with streamSource='depth' -- the
		  colorized depth image is placed into self.frameDeque instead of color,
		  so it flows through the *existing* MJPEG/WebSocket/WebRTC streaming code
		  with no new streaming-pipeline code.

	IMU (accel/gyro) is exposed as a single latest-value self.imuData dict (not a
	buffered history), read via getIMUData().

	Failure-mode contract:
		- Pure input-shape errors (invalid streamSource, streamSource='depth'
		  without enableDepth, non-positive/non-integer depth dimensions, an
		  invalid serial_number) raise ValueError synchronously from __init__,
		  before any hardware interaction.
		- Hardware/runtime failures (no matching device, no matching stream
		  profile, pipeline.start() SDK failure) all follow the same contract as
		  CameraUSB.start(): caught internally, logged, camOn stays False, never
		  raised to the caller. A partially-started pipeline is torn down before
		  start() returns.

	Attributes:
		serial_number (str or None): Target device serial number, or None to
			auto-select the first connected RealSense device.
		enableDepth (bool): Whether the depth stream is requested from the device.
		enableIMU (bool): Whether accel/gyro motion streams are requested.
		streamSource (str): 'color' (default) or 'depth' -- which image lands in
			self.frameDeque (and therefore in the video stream/CV features).
		depth_res_rows/depth_res_cols/depth_framerate (int or None): Depth stream
			resolution/framerate. Default to the color stream's own values.
		alignDepthToColor (bool): Whether depth is spatially aligned to color
			(rs.align) so depth and color pixels correspond. Default True.
		enableDepthFilters (bool): Whether pyrealsense2's spatial/temporal/
			hole-filling post-processing filters are applied to depth. Default
			True -- confirmed via real D435i hardware testing to make a
			dramatic difference in depth quality (much less jitter/noise,
			especially at longer range, and softer occlusion "shadow"
			artifacts at object edges when aligned to color), worth the extra
			CPU cost by default. Set False to skip it.
		depth_color_scheme (int or None): Which of pyrealsense2's colorizer
			color schemes to use for `streamSource='depth'` (0=Jet, 1=Classic,
			2=WhiteToBlack, 3=BlackToWhite, 4=Bio, 5=Cold, 6=Warm, 7=Quantized,
			8=Pattern, 9=Hue -- range confirmed against the SDK's own
			`colorizer.get_option_range()`). `None` (default) leaves the SDK's
			own default (`0`, Jet) untouched. Confirmed via real hardware that
			Jet's default direction is near=blue, far=red -- the reverse of
			the "near=red/hot=danger" convention some obstacle-avoidance UIs
			expect; pick a different scheme here if that matters for your use.
			A non-`None` value requires `streamSource='depth'` -- the
			colorizer it configures is only ever constructed in that mode, so
			any other combination would be a silent no-op.
		imu_accel_rate/imu_gyro_rate (int or None): Requested motion stream
			rates. None resolves to the highest fps the device offers for
			that stream (there is no separate SDK-level "default" -- an
			explicit fps must always be requested).
		depthDeque (deque): Single-slot deque holding the latest raw depth frame,
			already converted to meters (float32).
		depthIntrinsics (dict): Depth stream's own native factory intrinsics,
			kept entirely separate from self.intrinsics (which holds only
			color's calibration) so same-resolution color/depth streams can
			never collide in the shared self.intrinsics key space.
		imuData (dict): {'accel': (x,y,z)|None, 'accel_timestamp_ms': float|None,
			'gyro': (x,y,z)|None, 'gyro_timestamp_ms': float|None}. Each half
			updated independently, only when that motion frame type arrives.
			Timestamps are the SDK's own frame.get_timestamp() (float
			milliseconds, native/system timestamp domain).
	"""

	def __init__(self, paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort': 8000},
			serial_number=None, enableDepth=False, enableIMU=False, streamSource='color',
			depth_res_rows=None, depth_res_cols=None, depth_framerate=None,
			alignDepthToColor=True, enableDepthFilters=True, depth_color_scheme=None,
			imu_accel_rate=None, imu_gyro_rate=None,
			rs_module=None, logger=None, sslPath=None, pubCamStatusFunction=None,
			initROSnode=False, showFPS=True, ipAllowlist=[], ipBlocklist=[]):
		"""Initialize the RealSense camera interface.

		Args:
			paramDict (dict, optional): Configuration dictionary. Defaults to
				480x640 @ 30fps (color stream).
			serial_number (str, optional): Target RealSense device serial
				number. If None (default), the first connected device is used.
			enableDepth (bool): Whether to request the depth stream. Default False.
			enableIMU (bool): Whether to request accel/gyro motion streams.
				Default False.
			streamSource (str): 'color' (default) or 'depth' -- which image is
				placed into self.frameDeque. 'depth' requires enableDepth=True.
			depth_res_rows/depth_res_cols/depth_framerate (int, optional): Depth
				stream resolution/framerate. If None, default to the color
				stream's own res_rows/res_cols/fps_target.
			alignDepthToColor (bool): Align depth to color via rs.align. Default True.
			enableDepthFilters (bool): Apply spatial/temporal/hole-filling depth
				post-processing filters. Default True -- confirmed via real
				hardware to substantially reduce depth jitter/noise. Set False
				to skip the extra CPU cost.
			depth_color_scheme (int, optional): pyrealsense2 colorizer color
				scheme (0-9) for `streamSource='depth'`. If None (default),
				the SDK's own default (0, Jet: near=blue, far=red) is used.
				A non-None value requires streamSource='depth'.
			imu_accel_rate/imu_gyro_rate (int, optional): Requested motion stream
				rates in Hz. If None, resolves to the highest fps the device
				offers for that stream.
			rs_module (module, optional): Injectable pyrealsense2 module
				reference, for testing only. Real callers should never pass this.
			logger, sslPath, pubCamStatusFunction, initROSnode, showFPS,
				ipAllowlist, ipBlocklist: see Camera.__init__.

		Raises:
			ImportError: pyrealsense2 is not installed and rs_module was not
				supplied. Install with `pip install olab-camera[realsense]`.
			ValueError: an invalid streamSource, streamSource='depth' without
				enableDepth=True, a non-positive/non-integer explicit depth
				dimension/framerate, an out-of-range or otherwise
				inapplicable (streamSource != 'depth') depth_color_scheme, or
				an invalid serial_number.
		"""
		resolved_rs = rs_module if rs_module is not None else rs
		if resolved_rs is None:
			raise ImportError(
				'pyrealsense2 is required for CameraRealSense but is not installed. '
				'Install it with: pip install olab-camera[realsense]')

		if streamSource not in ('color', 'depth'):
			raise ValueError(f"streamSource must be 'color' or 'depth', got {streamSource!r}")

		if streamSource == 'depth' and not enableDepth:
			raise ValueError("streamSource='depth' requires enableDepth=True")

		for name, val in (('depth_res_rows', depth_res_rows), ('depth_res_cols', depth_res_cols),
				('depth_framerate', depth_framerate), ('imu_accel_rate', imu_accel_rate),
				('imu_gyro_rate', imu_gyro_rate)):
			olab_utils.validatePositiveIntOrNone(name, val)

		olab_utils.validateIntInRangeOrNone('depth_color_scheme', depth_color_scheme, 0, 9)

		if depth_color_scheme is not None and streamSource != 'depth':
			raise ValueError("depth_color_scheme requires streamSource='depth' (it configures the "
				"colorizer, which is only constructed when streamSource='depth')")

		if serial_number is not None and (not isinstance(serial_number, str) or serial_number == ''):
			raise ValueError(f'serial_number must be None or a non-empty string, got {serial_number!r}')

		super().__init__(paramDict, logger, sslPath, pubCamStatusFunction, initROSnode, showFPS, ipAllowlist, ipBlocklist)

		self._rs = resolved_rs

		self.serial_number  = serial_number
		self.enableDepth    = enableDepth
		self.enableIMU      = enableIMU
		self.streamSource   = streamSource

		self.depth_res_rows  = depth_res_rows
		self.depth_res_cols  = depth_res_cols
		self.depth_framerate = depth_framerate

		self.alignDepthToColor  = alignDepthToColor
		self.enableDepthFilters = enableDepthFilters
		self.depth_color_scheme = depth_color_scheme

		self.imu_accel_rate = imu_accel_rate
		self.imu_gyro_rate  = imu_gyro_rate

		self.pipeline   = None
		self.align      = None
		self.colorizer  = None
		self.depthScale = None
		self.depthIntrinsics = {}

		self._spatialFilter      = None
		self._temporalFilter     = None
		self._holeFillingFilter  = None

		self.depthDeque = deque(maxlen=1)

		self.imuData = {
			'accel': None, 'accel_timestamp_ms': None,
			'gyro':  None, 'gyro_timestamp_ms':  None,
		}

		self._capture_thread  = None
		self._capture_running = False


	def _selectStreamProfile(self, device, stream_type, width, height, fps):
		"""Require an exact-match video stream profile; raise a descriptive
		error (requested tuple + available profiles) if none exists.

		Called from start(); any raise here is caught by start()'s own
		try/except (hardware/runtime failure contract -- logged, camOn stays
		False, never propagated to the caller).
		"""
		for sensor in device.query_sensors():
			for profile in sensor.get_stream_profiles():
				if profile.stream_type() != stream_type:
					continue
				vprofile = profile.as_video_stream_profile()
				if (vprofile.width() == width and vprofile.height() == height
						and int(round(profile.fps())) == int(fps)):
					return profile

		available = sorted({
			(p.as_video_stream_profile().width(), p.as_video_stream_profile().height(), int(round(p.fps())))
			for s in device.query_sensors() for p in s.get_stream_profiles()
			if p.stream_type() == stream_type
		})
		raise Exception(
			f'No {stream_type} profile matching requested ({width}x{height}@{fps}fps). '
			f'Available profiles: {available}')


	def _selectMotionProfile(self, device, stream_type, rate):
		"""Require the device to support `stream_type` at all, and (if `rate`
		is given) an exact-match rate. Raise a descriptive error otherwise.
		Returns the resolved fps to request (the given `rate`, or the highest
		available rate if `rate` is None).

		librealsense's config.enable_stream() has no "pick a sensible
		default" wildcard for motion streams -- confirmed against real D435i
		hardware that the fps-omitted 2-arg form fails to resolve at
		pipeline.start() time (Exception: "Couldn't resolve requests"), even
		when the stream is requested alone. So `rate=None` must still resolve
		to one concrete, real profile here, not be passed through as omitted.

		Same hardware/runtime failure contract as _selectStreamProfile().
		"""
		matches = [p for s in device.query_sensors() for p in s.get_stream_profiles()
				if p.stream_type() == stream_type]
		if not matches:
			raise Exception(f'Device has no {stream_type} stream available.')

		if rate is not None:
			exact = [p for p in matches if int(round(p.fps())) == int(rate)]
			if not exact:
				available = sorted({int(round(p.fps())) for p in matches})
				raise Exception(
					f'No {stream_type} profile matching requested rate {rate}Hz. '
					f'Available rates: {available}')
			return rate

		return max(int(round(p.fps())) for p in matches)


	def _populateIntrinsics(self, targetDict, video_stream_profile):
		"""Auto-populate targetDict[WIDTHxHEIGHT] from the device's own factory
		calibration, in the same {'matrix':..., 'dist':...} shape setIntrinsics()
		produces. Never overwrites an existing entry (user-supplied calibration,
		or an already-populated resolution key, takes precedence).
		"""
		intr = video_stream_profile.get_intrinsics()
		res = f'{intr.width}x{intr.height}'
		if res in targetDict:
			return

		matrix = np.array([[intr.fx, 0.0,     intr.ppx],
							[0.0,     intr.fy, intr.ppy],
							[0.0,     0.0,     1.0]])
		dist = np.array(list(intr.coeffs))
		targetDict[res] = {'matrix': matrix, 'dist': dist}


	def _startCaptureThread(self):
		"""Start the background frame capture thread."""
		self._capture_running = True
		self._capture_thread = threading.Thread(target=self._captureLoop, daemon=True)
		self._capture_thread.start()

	def _stopCaptureThread(self, timeout=3.0):
		"""Deterministic stop order: signal first, then stop the pipeline
		(unblocking any pending wait_for_frames()), then join.

		Args:
			timeout (float): Seconds to wait for the thread to join. Defaults to 3.0.
		"""
		self._capture_running = False
		if self.pipeline is not None:
			try:
				self.pipeline.stop()
			except Exception:
				pass  # expected if the pipeline was never fully started, or is already stopped
		if self._capture_thread is not None:
			self._capture_thread.join(timeout=timeout)
			self._capture_thread = None


	def _captureLoop(self):
		"""Background thread: pull framesets from the RealSense pipeline and
		populate frameDeque/depthDeque/imuData.

		Runs until _capture_running is False. A real (non-shutdown) exception
		-- e.g. the device disconnecting mid-stream -- logs once and stops the
		loop itself (sets _capture_running=False) rather than spinning and
		re-logging forever; matches the class's no-auto-reconnect contract
		(see "Error handling / reconnection" in the plan) instead of hammering
		a dead pipeline. An exception raised by wait_for_frames() *after*
		_capture_running has already gone False (via _stopCaptureThread()) is
		separately expected shutdown noise (pipeline.stop() unblocking a
		pending call) and is not logged at all.
		"""
		rs = self._rs
		while self._capture_running:
			try:
				frameset = self.pipeline.wait_for_frames()

				depth_frame = frameset.get_depth_frame() if self.enableDepth else None

				if self.enableDepth and depth_frame:
					if self.align is not None:
						frameset  = self.align.process(frameset)
						depth_frame = frameset.get_depth_frame()

					if self.enableDepthFilters:
						depth_frame = self._spatialFilter.process(depth_frame)
						depth_frame = self._temporalFilter.process(depth_frame)
						depth_frame = self._holeFillingFilter.process(depth_frame)

					depth_arr = np.asanyarray(depth_frame.get_data()).astype(np.float32) * self.depthScale
					self.depthDeque.append(depth_arr)

				if self.streamSource == 'color':
					color_frame = frameset.get_color_frame()
					if color_frame:
						frame = self.zoomFunction(np.asanyarray(color_frame.get_data()))
						self.frameDeque.append(frame)
						self._lastFrameTime = time.time()
						self.announceCondition()
						self.calcFramerate(self.fps['capture'], 'capture')
				else:  # streamSource == 'depth'
					if depth_frame:
						colorized = self.colorizer.colorize(depth_frame)
						frame = cv2.cvtColor(np.asanyarray(colorized.get_data()), cv2.COLOR_RGB2BGR)
						frame = self.zoomFunction(frame)
						self.frameDeque.append(frame)
						self._lastFrameTime = time.time()
						self.announceCondition()
						self.calcFramerate(self.fps['capture'], 'capture')

				if self.enableIMU:
					accel_frame = frameset.first_or_default(rs.stream.accel)
					if accel_frame:
						m = accel_frame.as_motion_frame().get_motion_data()
						self.imuData['accel'] = (m.x, m.y, m.z)
						self.imuData['accel_timestamp_ms'] = accel_frame.get_timestamp()

					gyro_frame = frameset.first_or_default(rs.stream.gyro)
					if gyro_frame:
						m = gyro_frame.as_motion_frame().get_motion_data()
						self.imuData['gyro'] = (m.x, m.y, m.z)
						self.imuData['gyro_timestamp_ms'] = gyro_frame.get_timestamp()

			except Exception as e:
				if self._capture_running:
					self.logger.log(f'Error in CameraRealSense capture loop: {e}', severity=olab_utils.SEVERITY_ERROR)
					self._capture_running = False  # stop the loop deterministically -- no auto-reconnect
				# else: expected shutdown noise (pipeline.stop() unblocked wait_for_frames())

		self.camOn = False


	def start(self, assetID=None, res_rows=None, res_cols=None, framerate=None,
			depth_res_rows=None, depth_res_cols=None, depth_framerate=None,
			startStream=False, port=None, protocol='mjpeg', imgTopic=None, compImgTopic=None):
		"""Start the RealSense pipeline and optionally start streaming/publishing.

		Args:
			assetID (str, optional): Asset identifier (not used by CameraRealSense).
			res_rows/res_cols/framerate (int, optional): Color stream resolution/
				framerate. If None, uses values from paramDict.
			depth_res_rows/depth_res_cols/depth_framerate (int, optional): Depth
				stream resolution/framerate. If None, uses values from __init__
				(which themselves default to the color stream's values).
			startStream (bool, optional): Whether to start streaming. Defaults to False.
			port (int, optional): Port number for streaming server.
			protocol (str, optional): Streaming protocol. Defaults to 'mjpeg'.
			imgTopic/compImgTopic (str, optional): ROS topic names.

		Raises:
			ValueError: an explicitly-passed depth_res_rows/depth_res_cols/
				depth_framerate override is not a positive integer. Raised
				directly, before any state mutation or SDK configuration --
				not caught/logged by this method's own hardware-failure
				try/except below, since this is a pure input-shape error (same
				category as __init__'s own validation), not a runtime failure.

		Notes:
			- Hardware/runtime failures (no matching device/serial, no matching
			  stream profile, SDK pipeline.start() failure) are caught, logged,
			  and leave camOn False -- they are never raised to the caller. A
			  partially-started pipeline and capture thread are both torn down
			  (via _stopCaptureThread()) before returning.
		"""
		for name, val in (('depth_res_rows', depth_res_rows), ('depth_res_cols', depth_res_cols),
				('depth_framerate', depth_framerate)):
			olab_utils.validatePositiveIntOrNone(name, val)

		rs = self._rs
		try:
			self.res_rows  = self.defaultFromNone(res_rows,  self.res_rows,  int)
			self.res_cols  = self.defaultFromNone(res_cols,  self.res_cols,  int)
			self.framerate = self.defaultFromNone(framerate, self.fps_target, int)
			self.port      = self.defaultFromNone(port,      self.outputPort)

			# Explicit overrides (already validated above as positive ints or
			# None) take precedence; otherwise keep whatever was set at
			# __init__/a previous start(), and only fall back to color's own
			# values if that's also never been set. Deliberately NOT `val or
			# self.x` -- a valid-but-falsy override (0) would have been
			# rejected by the validation above already, but this form avoids
			# relying on that upstream guard to stay correct.
			if depth_res_rows is not None:
				self.depth_res_rows = depth_res_rows
			elif self.depth_res_rows is None:
				self.depth_res_rows = self.res_rows

			if depth_res_cols is not None:
				self.depth_res_cols = depth_res_cols
			elif self.depth_res_cols is None:
				self.depth_res_cols = self.res_cols

			if depth_framerate is not None:
				self.depth_framerate = depth_framerate
			elif self.depth_framerate is None:
				self.depth_framerate = self.framerate

			ctx = rs.context()
			devices = ctx.query_devices()

			target_device = None
			if self.serial_number is not None:
				for d in devices:
					if d.get_info(rs.camera_info.serial_number) == self.serial_number:
						target_device = d
						break
				if target_device is None:
					raise Exception(f'No RealSense device found with serial_number={self.serial_number!r}')
			else:
				if len(devices) == 0:
					raise Exception('No RealSense device found.')
				target_device = devices[0]

			resolved_serial = target_device.get_info(rs.camera_info.serial_number)

			config = rs.config()
			config.enable_device(resolved_serial)

			self._selectStreamProfile(target_device, rs.stream.color, self.res_cols, self.res_rows, self.framerate)
			config.enable_stream(rs.stream.color, self.res_cols, self.res_rows, rs.format.bgr8, int(self.framerate))

			if self.enableDepth:
				self._selectStreamProfile(target_device, rs.stream.depth, self.depth_res_cols, self.depth_res_rows, self.depth_framerate)
				config.enable_stream(rs.stream.depth, self.depth_res_cols, self.depth_res_rows, rs.format.z16, int(self.depth_framerate))

			if self.enableIMU:
				resolved_accel_rate = self._selectMotionProfile(target_device, rs.stream.accel, self.imu_accel_rate)
				resolved_gyro_rate = self._selectMotionProfile(target_device, rs.stream.gyro, self.imu_gyro_rate)
				config.enable_stream(rs.stream.accel, rs.format.motion_xyz32f, int(resolved_accel_rate))
				config.enable_stream(rs.stream.gyro, rs.format.motion_xyz32f, int(resolved_gyro_rate))

			self.pipeline = rs.pipeline(ctx)
			profile = self.pipeline.start(config)

			if self.enableDepth:
				depth_sensor = profile.get_device().first_depth_sensor()
				self.depthScale = depth_sensor.get_depth_scale()

				if self.alignDepthToColor:
					self.align = rs.align(rs.stream.color)

				if self.enableDepthFilters:
					self._spatialFilter     = rs.spatial_filter()
					self._temporalFilter    = rs.temporal_filter()
					self._holeFillingFilter = rs.hole_filling_filter()

				depth_stream = profile.get_stream(rs.stream.depth).as_video_stream_profile()
				self._populateIntrinsics(self.depthIntrinsics, depth_stream)

			if self.streamSource == 'depth':
				self.colorizer = rs.colorizer()
				if self.depth_color_scheme is not None:
					self.colorizer.set_option(rs.option.color_scheme, self.depth_color_scheme)

			color_stream = profile.get_stream(rs.stream.color).as_video_stream_profile()
			self._populateIntrinsics(self.intrinsics, color_stream)

			self.updateResolution(self.res_rows, self.res_cols)
			self.updateFramerate(self.framerate)

			self.camOn = True
			self._startCaptureThread()

			if startStream:
				if self.port is None:
					raise Exception('cannot stream when port is None')
				self.startStream(self.port, protocol=protocol)

			if (imgTopic is not None) or (compImgTopic is not None):
				self.startROStopic(imgTopic=imgTopic, compImgTopic=compImgTopic)

			self.reachback_pubCamStatus()
		except Exception as e:
			self.logger.log(f'Error in CameraRealSense start: {e}', severity=olab_utils.SEVERITY_ERROR)
			# Covers both failure shapes: a pipeline that never (fully) started
			# (_stopCaptureThread() is then just a no-op pipeline.stop()) AND a
			# failure *after* _startCaptureThread() was already called (e.g.
			# startStream()/startROStopic() raising) -- the deterministic stop
			# order here is what stops that thread; clearing self.pipeline
			# without it would leave the thread spinning against a dead/None
			# pipeline indefinitely.
			self._stopCaptureThread()
			self.pipeline = None
			self.camOn = False


	def stop(self, stopStream=True):
		"""Stop the capture thread and RealSense pipeline.

		Args:
			stopStream (bool): Whether to also stop the streaming server.
				Set False when changing resolution/framerate mid-stream.
		"""
		self._stopTrackers()
		self.camOn = False
		self._stopCaptureThread()
		self.pipeline = None

		if stopStream:
			self.stopStream()


	def shutdown(self):
		'''
		Might be as simple as calling self.stop()
		'''
		self.stop()
		time.sleep(STREAM_MAX_WAIT_TIME_SEC + 1)


	def changeResolutionFramerate(self, res_rows=None, res_cols=None, framerate=None):
		"""Change the color stream's resolution and/or framerate.

		Stops and restarts the RealSense pipeline with the new settings, the
		same restart-based approach CameraUSB uses. Depth/IMU settings are
		unaffected (still default to the previous color-relative values unless
		separately reconfigured via a fresh start()).

		Args:
			res_rows (int, optional): New image height in pixels. If None, keeps current value.
			res_cols (int, optional): New image width in pixels. If None, keeps current value.
			framerate (int, optional): New framerate in fps. If None, keeps current value.
		"""
		try:
			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)
			framerate = self.defaultFromNone(framerate, self.fps_target, int)

			if (framerate != self.fps_target or res_rows != self.res_rows or res_cols != self.res_cols):
				self.stop(stopStream=False)
				time.sleep(1)
				self.start(res_rows=res_rows, res_cols=res_cols, framerate=framerate)

			self.logger.log(f'rows: {self.res_rows}, cols: {self.res_cols}, framerate: {self.fps_target}', severity=olab_utils.SEVERITY_DEBUG)
		except Exception as e:
			self.logger.log(f'Failed to change to {res_rows} rows, {res_cols} cols, {framerate} framerate: {e}', severity=olab_utils.SEVERITY_ERROR)


	def changeZoom(self, zoomLevel):
		"""Change zoom level using digital zoom (crop and resize).

		Applied to whichever image is currently landing in frameDeque
		(color, or colorized depth when streamSource='depth').

		Args:
			zoomLevel (float): Zoom level where 1.0 = no zoom, 2.0 = 2x zoom, etc.
		"""
		self._changeZoom(zoomLevel)


	def getDepthFrame(self):
		"""Return the most recent raw depth frame without copying.

		Returns:
			numpy.ndarray: float32 depth values in meters (reference, not a copy).

		Warning:
			Returns a reference, not a copy. Use getDepthFrameCopy() to modify it
			or ensure it won't change. Raises IndexError if no depth frame has
			arrived yet (depthDeque is empty) -- same "no error checking yet"
			behavior as the base class's getFrame().
		"""
		return self.depthDeque[0]

	def getDepthFrameCopy(self):
		"""Return a copy of the most recent raw depth frame (meters, float32)."""
		return self.depthDeque[0].copy()

	def getIMUData(self):
		"""Return a copy of the latest IMU data.

		Returns:
			dict: {'accel': (x,y,z)|None, 'accel_timestamp_ms': float|None,
				'gyro': (x,y,z)|None, 'gyro_timestamp_ms': float|None}.
		"""
		return dict(self.imuData)
