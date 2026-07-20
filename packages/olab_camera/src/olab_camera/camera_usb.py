"""USB camera and RTSP/HTTP stream backend."""

import threading
import time

import cv2

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .camera import Camera, STREAM_MAX_WAIT_TIME_SEC


class CameraUSB(Camera):
	"""USB camera and RTSP/HTTP stream implementation using OpenCV VideoCapture.

	This class provides a versatile interface for various video sources using OpenCV's
	VideoCapture API. Despite the name, it supports USB cameras, video device files,
	RTSP streams, HTTP streams, and other sources that OpenCV can read. It uses a
	threaded capture loop to continuously grab frames.

	Key Differences from Base Camera:
		- Uses cv2.VideoCapture for frame acquisition
		- Supports multiple video backends via apiPref parameter (V4L2, FFMPEG, etc.)
		- VideoCapture opened synchronously in start(); frame loop runs in _captureLoop()
		- Digital zoom only (crops and resizes frames in software)
		- Supports dynamic resolution/framerate changes by restarting capture
		- Can connect to RTSP/HTTP streams (not just local devices)
		- Optional frameProcessor hook for per-frame CV processing

	Supported Sources:
		- USB webcams (e.g., /dev/video0 with V4L2 backend)
		- Raspberry Pi cameras on Ubuntu (e.g., /dev/video0)
		- RTSP streams (e.g., rtsp://192.168.1.100:8554/stream with apiPref=cv2.CAP_ANY)
		- HTTP MJPEG streams (e.g., https://localhost:8000/stream.mjpg with apiPref=cv2.CAP_ANY)
		- Video files (supported by OpenCV)
		- VOXL camera feeds (RTSP)

	Usage Example:
		>>> # USB webcam on Linux with V4L2
		>>> cam = CameraUSB(device='/dev/video0', apiPref=cv2.CAP_V4L2)
		>>> cam.start(res_rows=720, res_cols=1280, framerate=30, startStream=True, port=8000)
		>>>
		>>> # RTSP stream from VOXL or IP camera
		>>> cam = CameraUSB(device='rtsp://192.168.1.100:8554/stream')
		>>> cam.start(startStream=True, port=8001)
		>>>
		>>> # Get frame and save photo
		>>> frame = cam.getFrameCopy()
		>>> path, filename = cam.takePhotoLocal(path='/tmp/photos')
		>>>
		>>> # Change resolution and framerate
		>>> cam.changeResolutionFramerate(res_rows=480, res_cols=640, framerate=15)
		>>>
		>>> # Apply digital zoom
		>>> cam.changeZoom(2.0)
		>>>
		>>> # Stop camera
		>>> cam.shutdown()
		>>>
		>>> # Per-frame CV processing via frameProcessor hook:
		>>> #   - Return a frame  → appended to frameDeque and streamed.
		>>> #   - Return None     → frame discarded (not streamed, not published to ROS).
		>>> #   - frameProcessor = None (default) → pass-through, unchanged behavior.
		>>> # The hook fires after zoomFunction, so the frame is always correctly zoomed.
		>>>
		>>> # Process and stream the edited frame:
		>>> cam = CameraUSB(device='/dev/video0')
		>>> def my_pipeline(frame):
		...     frame = apply_color_filter(frame)
		...     frame = cv2.GaussianBlur(frame, (5, 5), 0)
		...     return frame        # return edited frame -> it streams
		...     # return None       # return None -> frame is dropped (not streamed)
		>>> cam.frameProcessor = my_pipeline
		>>> cam.start(startStream=True, port=8000)
		>>>
		>>> # Process a copy, stream the original unchanged:
		>>> def my_pipeline(frame):
		...     processed = frame.copy()
		...     processed = apply_color_filter(processed)
		...     do_something_with(processed)
		...     return frame        # original streams unchanged
		>>>
		>>> # Non-blocking processing via worker thread (e.g. for slow inference):
		>>> # maxsize=1 ensures the worker always sees the latest frame and memory stays bounded.
		>>> import queue, threading
		>>> q = queue.Queue(maxsize=1)
		>>> def worker():
		...     while True:
		...         frame = q.get()
		...         if frame is None: break
		...         do_something_with(run_inference(frame))
		>>> threading.Thread(target=worker, daemon=True).start()
		>>> def my_pipeline(frame):
		...     try: q.put_nowait(frame.copy())  # drop frame if worker is still busy
		...     except queue.Full: pass
		...     return frame        # original always streams without blocking

	Important Notes:
		- For RTSP/HTTP streams, use the default apiPref=cv2.CAP_ANY
		- For USB cameras on Linux, override with apiPref=cv2.CAP_V4L2 for best performance
		- Resolution/framerate changes restart the capture thread (brief interruption)
		- Not all cameras support all resolutions or framerates
		- FOURCC codec can be specified for compatible cameras (e.g., 'MJPG')
		- Stream sources may ignore resolution/framerate parameters
		- Zoom is digital only (reduces effective resolution)

	Attributes:
		device (str): Video source path or URL (e.g., '/dev/video0' or 'rtsp://...').
		apiPref (int): OpenCV VideoCapture API preference (e.g., cv2.CAP_ANY, cv2.CAP_V4L2).
		fourcc (tuple or None): FOURCC codec as 4-char tuple (e.g., ('M','J','P','G')).
		cap (cv2.VideoCapture): OpenCV VideoCapture instance (None when stopped).
		_capture_thread (threading.Thread): Background thread running the frame pull loop.
		_capture_running (bool): Flag used to signal the capture thread to stop.
		frameProcessor (callable or None): Optional per-frame hook. Called as
			frameProcessor(frame) after zoomFunction. Return a frame to stream it,
			or return None to drop the frame (not streamed, not published to ROS).
	"""
	
	def __init__(self, paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort': 8000}, device='/dev/video0',
		apiPref=cv2.CAP_ANY, fourcc=None, logger=None, sslPath=None, pubCamStatusFunction=None, imgTopic=None, compImgTopic=None,
		initROSnode=False, showFPS=True, ipAllowlist=[], ipBlocklist=[]):
		"""Initialize USB camera or video stream interface.

		Args:
			paramDict (dict, optional): Configuration dictionary. Defaults to 480x640 @ 30fps.
				Supported keys: 'res_rows', 'res_cols', 'fps_target', 'outputPort', 'device', 'fourcc'.
			device (str, optional): Video source path or URL. Examples:
				- '/dev/video0' for USB camera on Linux
				- 'rtsp://192.168.1.100:8554/stream' for RTSP stream
				- 'https://localhost:8000/stream.mjpg' for HTTP stream
				Defaults to '/dev/video0'.
			apiPref (int, optional): OpenCV VideoCapture API preference.
				- cv2.CAP_ANY to let OpenCV auto-detect the backend (default, works on all platforms)
				- cv2.CAP_V4L2 for Linux USB cameras (best performance on Linux)
				- cv2.CAP_DSHOW for Windows cameras
				A warning is logged if a stream URL is provided with a backend other than cv2.CAP_ANY.
				Defaults to cv2.CAP_ANY.
			fourcc (tuple or None, optional): FOURCC codec as 4-character tuple,
				e.g., ('M','J','P','G') for MJPEG. If None, uses camera default.
			logger (Logger, optional): Logger instance. If None, creates default logger.
			sslPath (str, optional): Path to SSL certificates for HTTPS streaming.
			pubCamStatusFunction (callable, optional): Callback function to publish camera status.
			imgTopic (str, optional): ROS image topic name for publishing raw images.
			compImgTopic (str, optional): ROS compressed image topic name.
			initROSnode (bool, optional): Whether to initialize ROS node. Defaults to False.
			showFPS (bool, optional): Whether to display FPS information. Defaults to True.
			ipAllowlist (list, optional): List of allowed IP addresses for streaming.
			ipBlocklist (list, optional): List of blocked IP addresses for streaming.

		Notes:
			- Does not open camera in __init__ (use start() to begin capture).
			- device and fourcc can also be specified in paramDict.
			- For RTSP/HTTP streams, use the default apiPref=cv2.CAP_ANY.
			- FOURCC codec support depends on camera hardware and drivers.
		"""

		super().__init__(paramDict, logger, sslPath, pubCamStatusFunction, initROSnode, showFPS, ipAllowlist, ipBlocklist)
		
		# FIXME -- Do some validation on inputs (in addition to what is in Camera)
		# `device` must be present (but it could be a key in paramDict??)
		# `apiPref` must be present
		# `fourcc` could be a key in paramDict
		
		if (not hasattr(self, 'device')):
			self.device  = device   
		if (not hasattr(self, 'fourcc')):
			self.fourcc  = fourcc   

		if apiPref is None:
			apiPref = cv2.CAP_ANY
		self.apiPref = apiPref

		_STREAM_PREFIXES = ('rtsp://', 'rtp://', 'http://', 'https://', 'udp://')
		if self.apiPref != cv2.CAP_ANY and isinstance(self.device, str) and self.device.startswith(_STREAM_PREFIXES):
			self.logger.log(
				f'apiPref={self.apiPref} was specified but device appears to be a stream URL. '
				'Consider using the default apiPref=cv2.CAP_ANY for stream sources.',
				severity=olab_utils.SEVERITY_WARNING)

		self.cap = None
		self._capture_thread  = None
		self._capture_running = False
		self.frameProcessor   = None   # optional callable(frame) -> frame | None
				
				
	def _startCaptureThread(self):
		"""Start the background frame capture thread."""
		self._capture_running = True
		self._capture_thread = threading.Thread(target=self._captureLoop, daemon=True)
		self._capture_thread.start()

	def _stopCaptureThread(self, timeout=3.0):
		"""Signal the capture thread to stop and wait for it to finish.

		Args:
			timeout (float): Seconds to wait for the thread to join. Defaults to 3.0.
		"""
		self._capture_running = False
		if self._capture_thread is not None:
			self._capture_thread.join(timeout=timeout)
			self._capture_thread = None

	def _captureLoop(self):
		"""Background thread: pull frames from cv2.VideoCapture and populate frameDeque.

		Runs until _capture_running is False or VideoCapture closes unexpectedly.
		Applies zoomFunction to each frame, then passes the result to frameProcessor
		(if set). If frameProcessor returns None the frame is dropped — it is not
		appended to frameDeque and therefore not streamed or published to ROS.
		"""
		while self._capture_running:
			try:
				if not self.cap.isOpened():
					self.logger.log('CameraUSB: VideoCapture closed unexpectedly', severity=olab_utils.SEVERITY_ERROR)
					break

				ret, frame = self.cap.read()
				if not ret:
					continue

				frame = self.zoomFunction(frame)

				if self.frameProcessor is not None:
					frame = self.frameProcessor(frame)
					if frame is None:
						continue   # user chose to drop this frame

				self.frameDeque.append(frame)
				self._lastFrameTime = time.time()
				self.announceCondition()
				self.calcFramerate(self.fps['capture'], 'capture')

			except Exception as e:
				self.logger.log(f'Error in CameraUSB capture loop: {e}', severity=olab_utils.SEVERITY_ERROR)

		self.camOn = False
				
			
	def start(self, assetID=None, res_rows=None, res_cols=None, framerate=None, device=None, apiPref=None, startStream=False, port=None, protocol='mjpeg', imgTopic=None, compImgTopic=None):
		"""Start camera capture and optionally start streaming/publishing.

		Opens and configures cv2.VideoCapture synchronously, then launches the
		background capture thread. Optionally starts a streaming server and/or
		ROS topic publishing.

		Args:
			assetID (str, optional): Asset identifier (not used by CameraUSB).
			res_rows (int, optional): Image height in pixels. If None, uses value from paramDict.
			res_cols (int, optional): Image width in pixels. If None, uses value from paramDict.
			framerate (int, optional): Target framerate in fps. If None, uses value from paramDict.
			device (str, optional): Video source path/URL. If None, uses value from __init__.
			apiPref (int or None, optional): OpenCV API preference. If None, uses value from __init__.
			startStream (bool, optional): Whether to start streaming. Defaults to False.
			port (int, optional): Port number for streaming server. Required if startStream=True.
			protocol (str, optional): Streaming protocol — 'mjpeg' (default), 'websocket',
				or 'webrtc'. Only used when startStream=True.
			imgTopic (str, optional): ROS topic name for publishing raw images.
			compImgTopic (str, optional): ROS topic name for publishing compressed images.

		Raises:
			Exception: If VideoCapture fails to open.
			Exception: If startStream=True but port=None.

		Notes:
			- VideoCapture is opened synchronously; failures are logged immediately.
			- For RTSP/HTTP streams, actual resolution/framerate may differ from requested.
			- Stream uses HTTPS/WSS with SSL certificates from self.sslPath.
			- Frames become available in frameDeque shortly after start() returns.
		"""
		try:
			# If user didn't provide a parameter, use the default value
			self.res_rows  = self.defaultFromNone(res_rows,  self.res_rows,  int)
			self.res_cols  = self.defaultFromNone(res_cols,  self.res_cols,  int)
			self.framerate = self.defaultFromNone(framerate, self.fps_target, int)
			self.device    = self.defaultFromNone(device,    self.device)
			self.apiPref   = self.defaultFromNone(apiPref,   self.apiPref)
			self.port      = self.defaultFromNone(port,      self.outputPort)

			# Open and configure VideoCapture synchronously.
			# Stream sources (RTSP, HTTP, etc.) are opened without configuration; resolution and
			# framerate are fixed server-side and read back after open. Local devices use cap.set()
			# after open, which is universally supported across all backends.
			# See https://www.simonwenkel.com/notes/software_libraries/opencv/opencv-frame-io.html
			_STREAM_PREFIXES = ('rtsp://', 'rtp://', 'http://', 'https://', 'udp://')
			_is_stream = isinstance(self.device, str) and self.device.startswith(_STREAM_PREFIXES)
			if _is_stream:
				self.cap = cv2.VideoCapture(self.device, self.apiPref)
			else:
				self.cap = cv2.VideoCapture(self.device, self.apiPref)
				self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  int(self.res_cols))
				self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.res_rows))
				self.cap.set(cv2.CAP_PROP_FPS,          int(self.framerate))
				if self.fourcc is not None:
					fourcc_code = cv2.VideoWriter.fourcc(self.fourcc[0], self.fourcc[1], self.fourcc[2], self.fourcc[3])
					self.cap.set(cv2.CAP_PROP_FOURCC, fourcc_code)

			if not self.cap.isOpened():
				raise Exception(f'cv2.VideoCapture failed to open: {self.device}')

			# Read back what the driver actually configured
			# FIXME -- Need to verify that the updates actually went thru
			self.updateResolution(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT), self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
			self.updateFramerate(self.cap.get(cv2.CAP_PROP_FPS))

			self.camOn = True
			self._startCaptureThread()

			# Start streaming?
			if startStream:
				if self.port is None:
					raise Exception('cannot stream when port is None')
				self.startStream(self.port, protocol=protocol)

			# Start publishing to ROS compressed image topic?
			if (imgTopic is not None) or (compImgTopic is not None):
				self.startROStopic(imgTopic=imgTopic, compImgTopic=compImgTopic)

			self.reachback_pubCamStatus()
		except Exception as e:
			self.logger.log(f'Error in camera start: {e}', severity=olab_utils.SEVERITY_ERROR)
	
	def stop(self, stopStream=True):
		"""Stop the capture thread and release VideoCapture.

		Args:
			stopStream (bool): Whether to also stop the streaming server.
				Set False when changing resolution/framerate mid-stream.
		"""
		self.camOn = False
		self._stopCaptureThread()
		if self.cap is not None:
			self.cap.release()
			self.cap = None

		# We may choose not to stop the stream if we are changing resolution/framerate.
		if stopStream:
			self.stopStream()
		
	def shutdown(self):
		'''
		Might be as simple as calling self.stop()
		'''
		self.stop()
		time.sleep(STREAM_MAX_WAIT_TIME_SEC + 1)
		
			
	def changeResolutionFramerate(self, res_rows=None, res_cols=None, framerate=None):
		"""Change camera resolution and/or framerate.

		Dynamically changes the video source resolution and/or framerate by stopping
		the current capture thread, updating parameters, and restarting capture with
		new settings. The streaming server (if running) continues without interruption.

		Args:
			res_rows (int, optional): New image height in pixels. If None, keeps current value.
			res_cols (int, optional): New image width in pixels. If None, keeps current value.
			framerate (int, optional): New framerate in fps. If None, keeps current value.

		Raises:
			Exception: If framerate is outside configured min/max bounds.
			Exception: If VideoCapture cannot be reconfigured.

		Notes:
			- Briefly interrupts frame capture while restarting (typically ~1 second).
			- Streaming continues during transition (may show same frame briefly).
			- For RTSP/HTTP streams, resolution/framerate changes may be ignored.
			- Actual resolution/framerate are verified after restart.
			- Updates self.res_rows, self.res_cols, and self.fps_target attributes.
			- Not all cameras support all resolutions or framerates.
		"""
		try:
			# If user didn't provide a parameter, use the default value
			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)
			framerate = self.defaultFromNone(framerate, self.fps_target, int)

			if hasattr(self, 'fpsMin') and hasattr(self, 'fpsMax'):
				if not (self.fpsMin <= framerate <= self.fpsMax):
					raise Exception(f'framerate {framerate} outside of [{self.fpsMin},{self.fpsMax}] bounds.')

			# Compare against stored attributes (cap may be None after stop())
			if (framerate != self.fps_target or
					res_rows != self.res_rows or
					res_cols != self.res_cols):

				# Need to stop/release camera to make updates.
				# However, don't stop the stream (if it is running).
				self.stop(stopStream=False)
				time.sleep(1)

				# Re-start, re-opening VideoCapture with new params.
				# start() reads back actuals from the driver and calls updateResolution/updateFramerate.
				self.start(res_rows=res_rows, res_cols=res_cols, framerate=framerate)

			fourccText = self.fourcc2text()
			self.logger.log(f'rows: {self.res_rows}, cols: {self.res_cols}, framerate: {framerate}', severity=olab_utils.SEVERITY_DEBUG)

		except Exception as e:
			self.logger.log(f'Failed to change to {res_rows} rows, {res_cols} cols, {framerate} framerate: {e}', severity=olab_utils.SEVERITY_ERROR)


	def changeZoom(self, zoomLevel):
		"""Change camera zoom level using digital zoom (crop and resize).

		Applies digital zoom by cropping the center region of each frame and resizing
		to the original resolution. This is done in software for each frame via
		the _changeZoom() method shared with CameraROS and Voxl cameras.

		Args:
			zoomLevel (float): Zoom level where 1.0 = no zoom, 2.0 = 2x zoom, etc.
				Higher values zoom in more (crop more of the frame).

		Notes:
			- Digital zoom reduces effective resolution (not optical zoom).
			- Zoom is applied to each frame in the capture thread.
			- Crop is centered on the frame.
			- Consider using camera's optical zoom if available (hardware-dependent).
			- For USB cameras, cv2.CAP_PROP_ZOOM may work on some hardware (not implemented).
		"""
		# This requires a numpy zoom/crop for each frame?
		self._changeZoom(zoomLevel)
					    

	def fourcc2text(self):
		# Find the 4-letter text description of our FOURCC property
		# See https://stackoverflow.com/questions/61659346/how-to-get-4-character-codec-code-for-videocapture-object-in-opencv
		h = int(self.cap.get(cv2.CAP_PROP_FOURCC))
		return chr(h&0xff) + chr((h>>8)&0xff) + chr((h>>16)&0xff) + chr((h>>24)&0xff)


# ---------------------------------------------------------------------------
# WebSocket frame-receiver — sim / virtual camera
# ---------------------------------------------------------------------------

