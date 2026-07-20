"""Raspberry Pi camera backends (`picamera` for CameraPi, `picamera2` for CameraPi2)."""

import threading
import time

import cv2
import numpy as np

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .camera import Camera, STREAM_MAX_WAIT_TIME_SEC


class CameraPi(Camera):
	"""Raspberry Pi camera implementation using the picamera package.

	This class provides an interface to Raspberry Pi Camera Module hardware (both
	the original camera module and Camera Module v2) using the picamera Python library.
	It supports hardware-accelerated video encoding, dynamic resolution/framerate changes,
	and native hardware zoom capabilities.

	Key Differences from Base Camera:
		- Uses picamera library instead of OpenCV VideoCapture
		- Implements hardware zoom via picamera.zoom property (no frame cropping needed)
		- Supports dynamic resolution/framerate changes via picamera API
		- Camera frames arrive via the write() callback method (picamera stream interface)
		- Requires picamera package (Raspberry Pi only)

	Hardware Support:
		- Raspberry Pi Camera Module v1 (OV5647)
		- Raspberry Pi Camera Module v2 (IMX219)
		- Raspberry Pi Camera Module v3 (IMX708)
		- Raspberry Pi High Quality Camera (IMX477)

	Usage Example:
		>>> # Basic usage with default settings
		>>> cam = CameraPi()
		>>> cam.start(res_rows=720, res_cols=1280, framerate=30, startStream=True, port=8000)
		>>>
		>>> # Capture and save photo
		>>> path, filename = cam.takePhotoLocal(path='/tmp/photos')
		>>>
		>>> # Change zoom level (hardware zoom)
		>>> cam.changeZoom(2.0)  # 2x zoom
		>>>
		>>> # Change resolution and framerate
		>>> cam.changeResolutionFramerate(res_rows=480, res_cols=640, framerate=15)
		>>>
		>>> # Shutdown camera
		>>> cam.shutdown()

	Important Notes:
		- Requires picamera library: `pip install picamera`
		- Only works on Raspberry Pi hardware with camera modules
		- Hardware zoom is more efficient than digital zoom (no quality loss)
		- Resolution changes require stopping and restarting recording
		- Some parameter combinations may not be supported by hardware

	FIXME FIXME FIXME -- Does picamera actually use `device` anywhere???

	Attributes:
		cap (picamera.PiCamera): The picamera instance controlling the hardware.
		picamera (module): Reference to the imported picamera module.
	"""
	def __init__(self, paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort': 8000},
				device='/dev/video0', apiPref=cv2.CAP_V4L2, logger=None, sslPath=None, pubCamStatusFunction=None,
				imgTopic=None, compImgTopic=None, initROSnode=False, showFPS=True, ipAllowlist=[], ipBlocklist=[]):
		"""Initialize Raspberry Pi camera interface.

		Args:
			paramDict (dict, optional): Configuration dictionary. Defaults to 480x640 @ 30fps.
				Supported keys: 'res_rows', 'res_cols', 'fps_target', 'outputPort'.
			device (str, optional): Device path (not used by picamera). Defaults to '/dev/video0'.
			apiPref (int, optional): API preference (not used by picamera). Defaults to cv2.CAP_V4L2.
			logger (Logger, optional): Logger instance. If None, creates default logger.
			sslPath (str, optional): Path to SSL certificates for HTTPS streaming.
			pubCamStatusFunction (callable, optional): Callback function to publish camera status.
			imgTopic (str, optional): ROS image topic name for publishing raw images.
			compImgTopic (str, optional): ROS compressed image topic name.
			initROSnode (bool, optional): Whether to initialize ROS node. Defaults to False.
			showFPS (bool, optional): Whether to display FPS information. Defaults to True.
			ipAllowlist (list, optional): List of allowed IP addresses for streaming.
			ipBlocklist (list, optional): List of blocked IP addresses for streaming.

		Raises:
			Exception: If picamera library cannot be imported (not on Raspberry Pi).

		Notes:
			- The device and apiPref parameters are accepted for API consistency but not used.
			- Picamera library must be installed and available.
			- Camera hardware must be enabled in raspi-config.
		"""
		try:
			import picamera
			self.picamera = picamera	# We have some namespace issues, since importing module inside class.
			# self.logger.log(f'i think picamera has been imported', severity=olab_utils.SEVERITY_DEBUG)
		except Exception as e:
			# raise Exception(f'Failed to init CameraPi: {e}') 
			# self.logger.log(f'Failed to init CameraPi: {e}', severity=olab_utils.SEVERITY_ERROR)
			print(f'Failed to init CameraPi: {e}')
			
		super().__init__(paramDict, logger, sslPath, pubCamStatusFunction, initROSnode, showFPS, ipAllowlist, ipBlocklist)
	
		self.cap = None	
	
	def _changeFramerate(self, req_framerate):
		try:			
			if (req_framerate == self.fps_target):
				# Nothing to change
				return (True, '')

			# FIXME -- Need to show new framerate in Cesium		
			if (self.fpsMin <= req_framerate <= self.fpsMax):
				delta = req_framerate - self.cap.framerate - self.cap.framerate_delta
				self.cap.framerate_delta += delta				
				self.updateFramerate(self.cap.framerate + self.cap.framerate_delta)
				return (True, '')
			else:
				return (False, 'picam framerate is at limit')
					
		except Exception as e:
			return (False, f'Could not change picam framerate: {e}')

		
		
	def _changeResolution(self, req_height, req_width):
		try:
			if (self.cap.resolution != (req_width, req_height)):
				self.cap.stop_recording()
	
				# FIXME -- Do we need to shut off ROI/ArUco threads?
				# self.setCamFunction(None, None)	

				time.sleep(1)
	
				self.cap.resolution = (req_width, req_height)
	
				time.sleep(1)

				self.updateResolution(req_height, req_width) 
	
				self.cap.start_recording(self, format='bgr')
	
				return (True, '')
			else:
				return (False, f'picam resolution is already {req_width}x{req_height}.')

		except Exception as e:
			return (False, f'Could not change picam resolution to {req_width}x{req_height}: {e}.')
				
		
	def changeZoom(self, zoomLevel):
		"""Change camera zoom level using hardware zoom.

		Uses Raspberry Pi's native hardware zoom capability by setting the picamera.zoom
		property. This is more efficient than digital zoom as it crops the sensor region
		before readout, maintaining full resolution without quality loss.

		Args:
			zoomLevel (float): Zoom level where 1.0 = no zoom, 2.0 = 2x zoom, etc.
				Higher values zoom in more (crop more of the sensor).

		Notes:
			- Hardware zoom is implemented by cropping the sensor region.
			- No post-processing or frame manipulation required.
			- Output resolution remains constant regardless of zoom level.
			- Maximum zoom depends on camera hardware and resolution.
			- Zoom is centered on the frame.

		References:
			https://picamera.readthedocs.io/en/release-1.13/api_camera.html#picamera.PiCamera.zoom
		"""
		# This involves a single-line RPi zoom setting
		# No need to manipulate individual frames in numpy
		try:
			# https://picamera.readthedocs.io/en/release-1.13/api_camera.html?highlight=zoom#picamera.PiCamera.zoom
			# https://forums.raspberrypi.com/viewtopic.php?t=254521

			w = h = min(1/zoomLevel, 1)
			x = y = (1 - w)/2
			self.cap.zoom = (x, y, w, h)
			
			self.updateZoom(zoomLevel)			
		except Exception as e:
			# raise Exception(f'Could not change picam zoomLevel to {zoomLevel}x: {e}.')
			self.logger.log(f'Could not change picam zoomLevel to {zoomLevel}x: {e}', severity=olab_utils.SEVERITY_ERROR)
		
	def changeResolutionFramerate(self, res_rows=None, res_cols=None, framerate=None):
		'''
		Change resolution and/or framerate		
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
			# raise Exception(f'Failed to change to {res_rows} rows, {res_cols} cols, {framerate} framerate: {e}')
			self.logger.log(f'Failed to change to {res_rows} rows, {res_cols} cols, {framerate} framerate: {e}', severity=olab_utils.SEVERITY_ERROR)
					 
	def shutdown(self):
		"""Shutdown camera and release all resources.

		Stops camera recording, closes the picamera instance, and waits for all streaming
		threads to complete. This is the clean way to release the camera hardware.

		Notes:
			- Calls stop() to halt recording and streaming.
			- Closes the picamera.PiCamera instance to release hardware.
			- Waits STREAM_MAX_WAIT_TIME_SEC + 1 second for threads to finish.
			- Should be called before program exit to properly release camera hardware.
		"""
		try:
			if (self.cap):
				self.stop()	
				self.cap.close()
				time.sleep(STREAM_MAX_WAIT_TIME_SEC + 1)

		except Exception as e:
			# raise Exception(f'Error in camera shutdown: {e}')
			self.logger.log(f'Error in camera shutdown: {e}', severity=olab_utils.SEVERITY_ERROR)
					 
	def start(self, assetID=None, res_rows=None, res_cols=None, framerate=None, startStream=False, port=None, protocol='mjpeg', imgTopic=None, compImgTopic=None):
		"""Initialize and start Raspberry Pi camera recording.

		This method creates a picamera.PiCamera instance, configures resolution and framerate,
		starts recording in BGR format, and optionally starts HTTP streaming and/or ROS topic
		publishing. The camera begins capturing frames immediately via the write() callback.

		Args:
			assetID (str, optional): Asset identifier (not used by CameraPi).
			res_rows (int, optional): Image height in pixels. If None, uses value from paramDict.
			res_cols (int, optional): Image width in pixels. If None, uses value from paramDict.
			framerate (int, optional): Target framerate in fps. If None, uses value from paramDict.
			startStream (bool, optional): Whether to start streaming. Defaults to False.
			port (int, optional): Port number for streaming server. Required if startStream=True.
			protocol (str, optional): Streaming protocol — 'mjpeg' (default), 'websocket',
				or 'webrtc'. Only used when startStream=True.
			imgTopic (str, optional): ROS topic name for publishing raw images.
			compImgTopic (str, optional): ROS topic name for publishing compressed images.

		Raises:
			Exception: If camera cannot be initialized or configured.
			Exception: If startStream=True but port=None.

		Notes:
			- Sets self.camOn = True to indicate camera is active.
			- Camera records continuously in BGR format for OpenCV compatibility.
			- Actual resolution/framerate may differ from requested; check self.res_rows,
			  self.res_cols, and self.fps_target after start.
			- Stream uses HTTPS/WSS with SSL certificates from self.sslPath.
		"""
		try:
			# If user didn't provide a parameter, use the default value
			res_rows     = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols     = self.defaultFromNone(res_cols,  self.res_cols,   int)
			framerate    = self.defaultFromNone(framerate, self.fps_target, int)
			port         = self.defaultFromNone(port, self.outputPort)
			# compImgTopic =
					
			self.cap = self.picamera.PiCamera(resolution=f'{res_cols}x{res_rows}', framerate=framerate)		

			# FIXME -- Need to verify that the updates actually went thru
			(width, height) = self.cap.resolution
			self.updateResolution(height, width)
			frate = self.cap.framerate
			self.updateFramerate(frate)
			
			# camera.start_recording(output, format='bgr', splitter_port=2, resize=(320,240))		
			self.cap.start_recording(self, format='bgr')
			
			self.camOn = True
			
			# Start streaming?
			if (startStream):
				if (port is None):
					raise Exception('cannot stream when port is None')
				else:
					self.startStream(port, protocol=protocol)

			# Start publishing to ROS compressed image topic?
			if ((imgTopic is not None) or (compImgTopic is not None)):
				self.startROStopic(imgTopic=imgTopic, compImgTopic=compImgTopic)	
			
			self.reachback_pubCamStatus()				
		except Exception as e:
			# raise Exception(f'Error in camera start: {e}')
			self.logger.log(f'Error in camera start: {e}', severity=olab_utils.SEVERITY_ERROR)
			
	def stop(self):
		'''
		Stop RPi camera from recording
		'''	
		try:
			self.camOn = False		
			self.cap.stop_recording()		
			self.stopStream()			
		except Exception as e:
			raise Exception(f'Error in camera stop: {e}')
			
		
	def write(self, buf):
		"""Callback method for receiving frames from picamera recording stream.

		This method is called automatically by picamera during recording. It receives
		raw BGR frame data, converts it to a numpy array, and adds it to the frame deque.
		It also triggers frame update notifications and framerate calculations.

		Args:
			buf (bytes): Raw BGR frame data from picamera stream.

		Notes:
			- This is a callback method called by picamera, not meant to be called directly.
			- Frame format is BGR (compatible with OpenCV) with shape (res_rows, res_cols, 3).
			- Each frame is appended to self.frameDeque for access by other threads.
			- Triggers threading.Condition notification for threads waiting on new frames.
			- Automatically calculates and updates capture framerate statistics.

		FIXME: Double check this implementation.
		"""
		try:
			# self.myNumpyArray = np.frombuffer(buf, dtype=np.uint8).reshape(self.res_rows, self.res_cols, 3)
			self.frameDeque.append(np.frombuffer(buf, dtype=np.uint8).reshape(self.res_rows, self.res_cols, 3))
			
			'''
			# Only call this if we actually have optical flow capabilities/hardware
			# if (self.vhcl.useOptFlowCam):
			# 	self.vhcl.optFlowPub(self.myNumpyArray, self.vhcl.oflow.camera_matrix, self.vhcl.oflow.dist_coeffs)
			
			# create a copy, as appropriate?
			
			# FIXME -- NEED TO FIX THESE
			#self.vhcl.asset.camAuto['thread_function'][self.vhcl.asset.camAuto['camName']](self.myNumpyArray)
	
			#self.pub()
			'''
			
			self.announceCondition()
			
			self.calcFramerate(self.fps['capture'], 'capture')
			
		except Exception as e:
			self.logger.log(f'Error writing picam frame: {e}', severity=olab_utils.SEVERITY_ERROR)
			

		return		

class CameraPi2(Camera):
	"""Raspberry Pi camera implementation using the picamera2 package.

	This class provides an interface to Raspberry Pi Camera Module hardware using the
	picamera2 Python library (the successor to picamera). It supports hardware-accelerated
	video capture, dynamic resolution/framerate changes at runtime, and hardware zoom via
	the ScalerCrop control.

	Key Differences from CameraPi:
		- Uses picamera2 library instead of the legacy picamera library
		- Frames are delivered via a background pull thread calling capture_array("main")
		  rather than a push callback (write method)
		- Zoom is implemented via ScalerCrop control using pixel coordinates queried
		  at runtime from camera_properties["PixelArraySize"]
		- Framerate is set via FrameDurationLimits control (microseconds)
		- Headless operation assumed (no preview window)

	Hardware Support:
		- Raspberry Pi 3B, 4, 5, CM5
		- Raspberry Pi Camera Module v2 (IMX219)
		- Raspberry Pi Camera Module v3 (IMX708)
		- Raspberry Pi High Quality Camera (IMX477)

	Usage Example:
		>>> cam = CameraPi2()
		>>> cam.start(res_rows=720, res_cols=1280, framerate=30, startStream=True, port=8000)
		>>>
		>>> cam.changeZoom(2.0)
		>>> cam.changeResolutionFramerate(res_rows=480, res_cols=640, framerate=15)
		>>> cam.shutdown()
		>>>
		>>> # Two cameras on the same Pi, streaming on different ports:
		>>> cam0 = CameraPi2(camID=0)
		>>> cam0.start(startStream=True, port=8000)
		>>> cam1 = CameraPi2(camID=1)
		>>> cam1.start(startStream=True, port=8001)
		>>>
		>>> # Per-frame CV processing via frameProcessor hook:
		>>> #   - Return a frame  → appended to frameDeque and streamed.
		>>> #   - Return None     → frame discarded (not streamed, not published to ROS).
		>>> #   - frameProcessor = None (default) → pass-through, unchanged behavior.
		>>>
		>>> # Process and stream the edited frame:
		>>> cam = CameraPi2()
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
		- Requires picamera2: installed via apt as python3-picamera2 (tested on v0.3.34)
		- Only works on Raspberry Pi hardware with camera modules enabled in raspi-config
		- Headless operation: no preview window is started
		- Frames are captured as BGR888 for OpenCV compatibility

	Attributes:
		cap (Picamera2): The Picamera2 instance controlling the hardware.
		Picamera2 (class): Reference to the imported Picamera2 class.
		_capture_thread (threading.Thread): Background thread running the frame pull loop.
		_capture_running (bool): Flag used to signal the capture thread to stop.
		frameProcessor (callable or None): Optional per-frame hook. Called as
			frameProcessor(frame) on each captured frame. Return a frame to stream it,
			or return None to drop the frame (not streamed, not published to ROS).
	"""
	def __init__(self, paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort': 8000},
				camID=0, device='/dev/video0', apiPref=cv2.CAP_V4L2, logger=None, sslPath=None, pubCamStatusFunction=None,
				imgTopic=None, compImgTopic=None, initROSnode=False, showFPS=True, ipAllowlist=[], ipBlocklist=[]):
		"""Initialize Raspberry Pi camera interface using picamera2.

		Args:
			paramDict (dict, optional): Configuration dictionary. Defaults to 480x640 @ 30fps.
				Supported keys: 'res_rows', 'res_cols', 'fps_target', 'outputPort'.
			camID (int, optional): Camera index passed to Picamera2. Use 0 for the first
				camera, 1 for the second, etc. Defaults to 0.
			device (str, optional): Device path (not used by picamera2). Defaults to '/dev/video0'.
			apiPref (int, optional): API preference (not used by picamera2). Defaults to cv2.CAP_V4L2.
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
			- The device and apiPref parameters are accepted for API consistency but not used.
			- camID is unique to CameraPi2 (passed to Picamera2).
			- picamera2 must be installed (apt install python3-picamera2).
			- Camera hardware must be enabled in raspi-config.
		"""
		try:
			from picamera2 import Picamera2
			self.Picamera2 = Picamera2
		except Exception as e:
			print(f'Failed to init CameraPi2: {e}')

		super().__init__(paramDict, logger, sslPath, pubCamStatusFunction, initROSnode, showFPS, ipAllowlist, ipBlocklist)

		self.camID = camID
		self.cap = None
		self._capture_thread = None
		self._capture_running = False
		self.frameProcessor = None   # optional callable(frame) -> frame | None

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
		"""Background thread: pull frames from picamera2 and populate frameDeque.

		Calls capture_array("main") in a loop. The call blocks until picamera2
		delivers the next frame, providing natural pacing without busy-polling.
		Despite the "RGB888" format label, picamera2 delivers BGR bytes on tested
		hardware (OV5647/vc4 pipeline), so no conversion is applied.

		If frameProcessor is set, it is called on each frame. Return a frame to
		stream it, or return None to drop the frame (not streamed, not published to ROS).
		"""
		while self._capture_running:
			try:
				frame = self.cap.capture_array("main")  # picamera2 delivers BGR despite RGB888 format label

				if self.frameProcessor is not None:
					frame = self.frameProcessor(frame)
					if frame is None:
						continue   # user chose to drop this frame

				self.frameDeque.append(frame)
				self.announceCondition()
				self.calcFramerate(self.fps['capture'], 'capture')
			except Exception as e:
				self.logger.log(f'Error in CameraPi2 capture loop: {e}', severity=olab_utils.SEVERITY_ERROR)

	def _changeFramerate(self, req_framerate):
		try:
			if req_framerate == self.fps_target:
				return (True, '')

			if self.fpsMin <= req_framerate <= self.fpsMax:
				frame_duration_us = int(1e6 / req_framerate)
				self.cap.set_controls({"FrameDurationLimits": (frame_duration_us, frame_duration_us)})
				self.updateFramerate(req_framerate)
				return (True, '')
			else:
				return (False, 'picam2 framerate is at limit')

		except Exception as e:
			return (False, f'Could not change picam2 framerate: {e}')

	def _changeResolution(self, req_height, req_width):
		try:
			current_size = self.cap.camera_configuration()["main"]["size"]  # (width, height)
			if current_size == (req_width, req_height):
				return (False, f'picam2 resolution is already {req_width}x{req_height}.')

			self._stopCaptureThread()
			self.cap.stop()

			config = self.cap.create_video_configuration(
				main={"format": "RGB888", "size": (req_width, req_height)}
			)
			self.cap.configure(config)
			self.cap.start()

			frame_duration_us = int(1e6 / self.fps_target)
			self.cap.set_controls({"FrameDurationLimits": (frame_duration_us, frame_duration_us)})

			self.updateResolution(req_height, req_width)
			self._startCaptureThread()
			return (True, '')

		except Exception as e:
			return (False, f'Could not change picam2 resolution to {req_width}x{req_height}: {e}.')

	def changeZoom(self, zoomLevel):
		"""Change camera zoom level using hardware ScalerCrop control.

		Queries the sensor's full pixel array size at runtime and computes a
		center-crop rectangle corresponding to the requested zoom level. Works
		across all supported camera modules (IMX219, IMX708, IMX477).

		Args:
			zoomLevel (float): Zoom level where 1.0 = no zoom, 2.0 = 2x zoom, etc.

		Notes:
			- Zoom is centered on the frame.
			- Output resolution remains constant regardless of zoom level.
			- Maximum zoom is limited by sensor resolution and hardware.

		References:
			https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf
		"""
		try:
			sensor_w, sensor_h = self.cap.camera_properties["PixelArraySize"]
			crop_w = int(sensor_w / zoomLevel)
			crop_h = int(sensor_h / zoomLevel)
			crop_x = (sensor_w - crop_w) // 2
			crop_y = (sensor_h - crop_h) // 2
			self.cap.set_controls({"ScalerCrop": (crop_x, crop_y, crop_w, crop_h)})
			self.updateZoom(zoomLevel)
		except Exception as e:
			self.logger.log(f'Could not change picam2 zoomLevel to {zoomLevel}x: {e}', severity=olab_utils.SEVERITY_ERROR)

	def changeResolutionFramerate(self, res_rows=None, res_cols=None, framerate=None):
		"""Change resolution and/or framerate."""
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

	def shutdown(self):
		"""Shutdown camera and release all resources.

		Stops the capture thread, halts recording, closes the Picamera2 instance,
		and waits for streaming threads to finish.
		"""
		try:
			if self.cap:
				self.stop()
				self.cap.close()
				time.sleep(STREAM_MAX_WAIT_TIME_SEC + 1)
		except Exception as e:
			self.logger.log(f'Error in camera shutdown: {e}', severity=olab_utils.SEVERITY_ERROR)

	def start(self, assetID=None, res_rows=None, res_cols=None, framerate=None, startStream=False, port=None, protocol='mjpeg', imgTopic=None, compImgTopic=None):
		"""Initialize and start Raspberry Pi camera using picamera2.

		Creates a Picamera2 instance, configures it for continuous video capture
		in BGR888 format, starts the hardware, and launches the background capture
		thread. Optionally starts HTTP streaming and/or ROS topic publishing.

		Args:
			assetID (str, optional): Asset identifier (not used by CameraPi2).
			res_rows (int, optional): Image height in pixels. If None, uses value from paramDict.
			res_cols (int, optional): Image width in pixels. If None, uses value from paramDict.
			framerate (int, optional): Target framerate in fps. If None, uses value from paramDict.
			startStream (bool, optional): Whether to start streaming. Defaults to False.
			port (int, optional): Port number for streaming server. Required if startStream=True.
			protocol (str, optional): Streaming protocol — 'mjpeg' (default), 'websocket', or 'webrtc'.
			imgTopic (str, optional): ROS topic name for publishing raw images.
			compImgTopic (str, optional): ROS topic name for publishing compressed images.
		"""
		try:
			res_rows  = self.defaultFromNone(res_rows,  self.res_rows,   int)
			res_cols  = self.defaultFromNone(res_cols,  self.res_cols,   int)
			framerate = self.defaultFromNone(framerate, self.fps_target, int)
			port      = self.defaultFromNone(port, self.outputPort)

			self.cap = self.Picamera2(self.camID)

			config = self.cap.create_video_configuration(
				main={"format": "RGB888", "size": (res_cols, res_rows)}
			)
			self.cap.configure(config)
			self.cap.start()

			frame_duration_us = int(1e6 / framerate)
			self.cap.set_controls({"FrameDurationLimits": (frame_duration_us, frame_duration_us)})

			# Read back actual configured size
			actual_size = self.cap.camera_configuration()["main"]["size"]
			self.updateResolution(actual_size[1], actual_size[0])
			self.updateFramerate(framerate)  # picamera2 doesn't expose set framerate directly

			self.camOn = True
			self._startCaptureThread()

			if startStream:
				if port is None:
					raise Exception('cannot stream when port is None')
				else:
					self.startStream(port, protocol=protocol)

			if (imgTopic is not None) or (compImgTopic is not None):
				self.startROStopic(imgTopic=imgTopic, compImgTopic=compImgTopic)

			self.reachback_pubCamStatus()

		except Exception as e:
			self.logger.log(f'Error in camera start: {e}', severity=olab_utils.SEVERITY_ERROR)

	def stop(self):
		"""Stop camera capture and streaming."""
		try:
			self.camOn = False
			self._stopCaptureThread()
			self.cap.stop()
			self.stopStream()
		except Exception as e:
			raise Exception(f'Error in camera stop: {e}')


