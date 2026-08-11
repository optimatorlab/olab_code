"""OpenMV camera backend for histogram, regions, and raw GENX event modes."""

import threading
import time
from dataclasses import replace
from os import PathLike

import cv2
import numpy as np

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .camera import Camera, STREAM_MAX_WAIT_TIME_SEC
from .openmv_device import OpenMVDevice
from .openmv_events import (
	EVT20Decoder, EventDecodeError, EventDispatcher, EventPreviewWorker, EventRecorder,
	EventRecorderWorker,
)
from .openmv_movement import MovementRecordDecodeError, decode_movement_record
from .openmv_profiles import PROFILES

# OpenMV's V2 csi stream header identifies a GENX320 grayscale frame as
# `0x06060000` (measured on firmware 5.0.0). This is intentionally distinct
# from the optional host package's `openmv.image.PIXFORMAT_GRAYSCALE`: that
# image-library enum is not the V2 csi stream-header contract.
_PIXFORMAT_GRAYSCALE = 0x06060000
_NO_FRAME_WARNING_THRESHOLD = 25
_NO_FRAME_STARTUP_GRACE_SEC = 5.0
_NO_FRAME_WARNING_INTERVAL_SEC = 5.0
_NO_FRAME_IMMEDIATE_BACKOFF_SEC = 0.01


class CameraOpenMV(Camera):
	"""OpenMV-backed Camera implementation, standard-frame integration only.

	Sibling to CameraUSB/CameraPi/CameraRealSense, subclassing the shared
	Camera base class. Color/frame output flows through self.frameDeque
	exactly like every other camera class -- addAruco()/startStream()/
	decorations all work unmodified.

	Ownership/concurrency contract: nothing in the `openmv` client library
	is documented thread-safe for concurrent access, unlike librealsense's
	pipeline.stop()-unblocks-wait_for_frames() contract. Once the capture
	thread is running, it is the *sole* legitimate user of the underlying
	OpenMVDevice/protocol client -- see stop()/start()'s docstrings for the
	deferred single-owner cleanup this implies.

	Frame contract:
		- Only the profile's exact fixed resolution and grayscale pixel
		  format are accepted; anything else is dropped (logged), never
		  raised mid-loop.
		- self.frameDeque holds BGR numpy.ndarray frames only, exactly like
		  every other Camera subclass -- no metadata is embedded in it.
		- self._latestFrameMeta / getFrameAndMeta() carry host receipt time
		  (monotonic + wall clock) and a host-assigned sequence number --
		  never a sensor-exposure timestamp, since host receipt time is all
		  that's available at this integration layer.

	Attributes:
		devicePort (str): OpenMV serial device path. Distinct from the
			inherited `self.port` (streaming port) -- see __init__.
	"""

	def __init__(self, devicePort,
			paramDict={'res_rows': 320, 'res_cols': 320, 'fps_target': 30, 'outputPort': 8000},
			profile='genx_histogram_preview', profile_kwargs=None,
			device_class=None, device_kwargs=None,
			logger=None, sslPath=None, pubCamStatusFunction=None,
			initROSnode=False, showFPS=True, ipAllowlist=[], ipBlocklist=[]):
		"""Initialize the OpenMV camera interface (does not connect).

		Args:
			devicePort (str): OpenMV serial device path (e.g.
				'/dev/ttyACM0'). Required, non-empty. Not the same as the
				streaming `port` used by start()/changeResolutionFramerate()
				-- that name is reserved for the streaming-port meaning
				every Camera subclass uses (self.port set in start()).
			paramDict (dict, optional): Configuration dictionary. Defaults
				to 320x320 @ 30fps, matching genx_histogram_preview's fixed
				output.
			profile (str or profile instance): Profile name (looked up in
				`olab_camera.openmv_profiles.PROFILES`) or an
				already-constructed profile instance. Defaults to
				'genx_histogram_preview'.
			profile_kwargs (dict, optional): Keyword arguments forwarded to
				the profile's config constructor when `profile` is a name.
				Ignored if `profile` is already an instance.
			device_class (type, optional): Injectable `OpenMVDevice`-
				compatible class, for testing only. Real callers should
				never pass this.
			device_kwargs (dict, optional): Extra keyword arguments
				forwarded to `OpenMVDevice.__init__` (e.g. `timeout`,
				`baudrate`).
			logger, sslPath, pubCamStatusFunction, initROSnode, showFPS,
				ipAllowlist, ipBlocklist: see Camera.__init__.

		Raises:
			ValueError: `devicePort` is not a non-empty string, `profile` is
				an unrecognized name, or profile config validation fails.
		"""
		if not isinstance(devicePort, str) or devicePort == '':
			raise ValueError(f'devicePort must be a non-empty string, got {devicePort!r}')

		if isinstance(profile, str):
			if profile not in PROFILES:
				raise ValueError(f'unknown profile {profile!r}; available: {sorted(PROFILES)}')
			self._profile = PROFILES[profile](**(profile_kwargs or {}))
		else:
			self._profile = profile

		super().__init__(paramDict, logger, sslPath, pubCamStatusFunction, initROSnode, showFPS, ipAllowlist, ipBlocklist)

		self.devicePort    = devicePort
		self._device_class  = device_class
		self._device_kwargs = dict(device_kwargs or {})

		self._device           = None
		self._capture_thread   = None
		self._capture_running  = False
		self._stopping         = False
		self._captureThreadDone = threading.Event()
		self._frameSeq         = 0
		self._eventCallbacks   = []
		self._eventRecorderOutputDir = None
		self._eventDispatcher  = None
		self._eventPreviewWorker = None
		self._eventRecorderWorker = None
		self._rawWorkerReaper = None
		self.eventStats = {'batches': 0, 'events': 0, 'decode_errors': 0,
			'callback_drops': 0, 'preview_drops': 0, 'callback_errors': 0,
			'recorder_drops': 0, 'recorder_errors': 0}
		self.latestMovementRecord = None

		self._latestFrameMeta = {
			'host_receipt_time': None, 'host_receipt_wall_time': None, 'sequence': None,
		}


	def _isRawEventsProfile(self):
		return 'raw_events' in getattr(self._profile, 'capabilities', ())

	def _hasMovementRegions(self):
		return 'movement_regions' in getattr(self._profile, 'capabilities', ())


	def addEventCallback(self, callback):
		"""Register a non-blocking raw-event consumer for raw-event sessions."""
		if not callable(callback):
			raise ValueError('callback must be callable')
		if self.camOn:
			raise RuntimeError('addEventCallback() must be called before start()')
		self._eventCallbacks.append(callback)
		return callback


	def addEventRecorder(self, outputDir):
		"""Record raw-event batches to `outputDir` during the next raw session."""
		if not isinstance(outputDir, (str, bytes, PathLike)) or not outputDir:
			raise ValueError('outputDir must be a non-empty path')
		if self.camOn:
			raise RuntimeError('addEventRecorder() must be called before start()')
		self._eventRecorderOutputDir = outputDir


	def _deviceTimeout(self):
		"""Bound used for stop()'s join -- falls back to a conservative
		default if no device was ever successfully connected."""
		return self._device.timeout if self._device is not None else 1.0


	def _cleanupDeviceSync(self):
		"""Synchronous, direct device cleanup for a start() failure that
		happens *before* the capture thread is ever launched -- at that
		point nothing else is using the device, so the calling thread is
		trivially the sole owner. Never call this once the capture thread
		has started; see stop()'s docstring for why that case is handled
		differently.
		"""
		if self._device is not None:
			try:
				self._device.disconnect()
			except Exception as e:
				self.logger.log(f'Error disconnecting CameraOpenMV device during startup cleanup: {e}', severity=olab_utils.SEVERITY_ERROR)
			self._device = None
		self._stopRawWorkers()
		self._stopping = False
		self.camOn = False


	def start(self, assetID=None, res_rows=None, res_cols=None, framerate=None,
			startStream=False, port=None, protocol='mjpeg', imgTopic=None, compImgTopic=None):
		"""Connect to the OpenMV device, run the configured profile, and
		start the capture thread; optionally start streaming/publishing.

		Args:
			assetID (str, optional): Not used by CameraOpenMV (same
				convention as CameraRealSense).
			res_rows/res_cols (int, optional): Must equal the profile's
				fixed resolution if given.
			framerate (int, optional): Must equal the profile's configured
				histogram rate if given.
			startStream (bool, optional): Whether to start streaming.
			port (int, optional): Streaming server port. Defaults to
				paramDict's outputPort. Not the OpenMV serial `devicePort`
				given at construction.
			protocol (str, optional): Streaming protocol. Defaults to 'mjpeg'.
			imgTopic/compImgTopic (str, optional): ROS topic names.

		Raises:
			ValueError: an explicit `res_rows`/`res_cols`/`framerate`
				doesn't match the profile's fixed configuration. Raised
				synchronously, before any device interaction.
			RuntimeError: called while a previous stop() is still completing
				its deferred cleanup (see stop()) -- retry once that
				finishes rather than racing a new capture thread against it.

		Notes:
			- Failures before the capture thread is launched (connect,
			  stopScript, script upload, streaming-enable) are cleaned up
			  synchronously and directly here -- no capture thread exists
			  yet to own the device.
			- Failures after the capture thread is launched (startStream()/
			  startROStopic() raising) are cleaned up via stop()'s deferred
			  single-owner machine instead, since the capture thread already
			  owns the device by then.
		"""
		if self._stopping:
			raise RuntimeError(
				'CameraOpenMV.start() called while a previous stop() is still completing '
				'its deferred cleanup; wait for it to finish and try again.')

		config = self._profile.config
		resolution = getattr(config, 'resolution', (320, 320))
		if res_rows is not None and int(res_rows) != resolution[0]:
			raise ValueError(f'res_rows must be {resolution[0]} for profile {self._profile.profile_id!r}, got {res_rows!r}')
		if res_cols is not None and int(res_cols) != resolution[1]:
			raise ValueError(f'res_cols must be {resolution[1]} for profile {self._profile.profile_id!r}, got {res_cols!r}')
		if self._isRawEventsProfile():
			if framerate is not None:
				raise ValueError('framerate is not supported by raw-event profiles; use preview_rate_hz')
			self.framerate = config.preview_rate_hz
		else:
			if framerate is not None and int(framerate) != config.histogram_rate_hz:
				raise ValueError(f'framerate must be {config.histogram_rate_hz} for profile {self._profile.profile_id!r}, got {framerate!r}')
			self.framerate = config.histogram_rate_hz

		self.res_rows, self.res_cols = resolution
		self.port      = self.defaultFromNone(port, self.outputPort)

		self._captureThreadDone.clear()

		try:
			device_class = self._device_class if self._device_class is not None else OpenMVDevice
			device = device_class(self.devicePort, **self._device_kwargs)
			# Assign self._device immediately, before any call that can
			# leave a real connection open -- review round 2 (Stage 2,
			# finding 2) caught that assigning it only after streaming()
			# succeeded meant _cleanupDeviceSync() saw self._device as None
			# (and so could never call disconnect()) for every earlier
			# failure stage, leaking the connection.
			self._device = device
			device.connect()
			device.stopScript()
			device.runSource(self._profile.render_script())
			if not self._isRawEventsProfile():
				device.streaming(True, raw=False)
			else:
				self._startRawWorkers()

			self._frameSeq = 0
			self.camOn = True
			self._startCaptureThread()

			if startStream:
				if self.port is None:
					raise Exception('cannot stream when port is None')
				self.startStream(self.port, protocol=protocol)

			if (imgTopic is not None) or (compImgTopic is not None):
				self.startROStopic(imgTopic=imgTopic, compImgTopic=compImgTopic)

			self.reachback_pubCamStatus()

		except ImportError:
			# openmv not installed and no device_class injected -- a
			# configuration/environment error, not a transient hardware
			# failure. Never swallow it (same convention as
			# CameraRealSense's directly-raised ImportError): clean up
			# whatever little state exists, then re-raise so the caller
			# knows to `pip install olab-camera[openmv]`.
			if self._capture_thread is not None:
				self.stop()
			else:
				self._cleanupDeviceSync()
			raise
		except Exception as e:
			self.logger.log(f'Error in CameraOpenMV start: {e}', severity=olab_utils.SEVERITY_ERROR)
			if self._capture_thread is not None:
				# The capture thread is already running and is now the
				# device's sole legitimate user -- defer to stop()'s
				# single-owner machine rather than touching the device here.
				self.stop()
			else:
				# No capture thread exists yet -- this start() call is
				# trivially the device's sole user so far.
				self._cleanupDeviceSync()

	def _publishFrame(self, bgr):
		"""Publish one BGR frame through the shared Camera frame contract."""
		self._frameSeq += 1
		with self.condition:
			self.frameDeque.append(bgr)
			self._latestFrameMeta = {
				'host_receipt_time': time.monotonic(),
				'host_receipt_wall_time': time.time(),
				'sequence': self._frameSeq,
			}
			self.condition.notify_all()
		self._lastFrameTime = time.time()
		self.calcFramerate(self.fps['capture'], 'capture')


	def _applyHistogramDisplayPolicy(self, bgr):
		if getattr(self._profile.config, 'display_palette', 'grayscale') == 'turbo':
			return cv2.applyColorMap(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), cv2.COLORMAP_TURBO)
		return bgr


	def _readMovementRecord(self):
		try:
			status = self._device.readChannelStatus()
			if not status.get('genx_regions'):
				return
			size = self._device.channelSize('genx_regions')
			if size:
				self.latestMovementRecord = decode_movement_record(
					self._device.readChannel('genx_regions', size))
		except (MovementRecordDecodeError, ValueError) as e:
			self.logger.log(f'CameraOpenMV: malformed movement record: {e}', severity=olab_utils.SEVERITY_WARNING)


	def _overlayMovementRegions(self, bgr):
		if not self.latestMovementRecord:
			return bgr
		out = bgr.copy()
		for region in self.latestMovementRecord['regions']:
			x, y, w, h = (region[k] for k in ('x', 'y', 'w', 'h'))
			cv2.rectangle(out, (x, y), (x + w - 1, y + h - 1), (0, 255, 0), 1)
			cv2.drawMarker(out, (region['cx'], region['cy']), (0, 200, 255), cv2.MARKER_CROSS, 7, 1)
		return out


	def _startRawWorkers(self):
		config = self._profile.config
		recorder = None
		if self._eventRecorderOutputDir is not None:
			recorder = EventRecorder(self._eventRecorderOutputDir, {
				'profile_id': self._profile.profile_id, 'device_port': self.devicePort})
		def _worker_error(kind, exc):
			self.eventStats[f'{kind}_errors'] = self.eventStats.get(f'{kind}_errors', 0) + 1
			self.logger.log(f'CameraOpenMV raw {kind} error: {exc}', severity=olab_utils.SEVERITY_ERROR)
		self._eventDispatcher = EventDispatcher(
			self._eventCallbacks,
			queue_size=config.callback_queue_size, on_error=_worker_error)
		if recorder is not None:
			self._eventRecorderWorker = EventRecorderWorker(
				recorder, queue_size=config.callback_queue_size,
				on_error=lambda exc: _worker_error('recorder', exc))
		if config.preview_enabled:
			self._eventPreviewWorker = EventPreviewWorker(
				self._publishFrame, queue_size=config.callback_queue_size,
				preview_rate_hz=config.preview_rate_hz)
		self._eventDispatcher.start()
		if self._eventPreviewWorker is not None:
			self._eventPreviewWorker.start()
		if self._eventRecorderWorker is not None:
			self._eventRecorderWorker.start()


	def _stopRawWorkers(self):
		all_stopped = True
		for worker in (self._eventPreviewWorker, self._eventDispatcher, self._eventRecorderWorker):
			if worker is not None and not worker.stop(timeout=self._deviceTimeout()):
				self.logger.log('CameraOpenMV raw worker did not exit within bounded shutdown timeout', severity=olab_utils.SEVERITY_WARNING)
				all_stopped = False
		if self._eventDispatcher is not None:
			self.eventStats['callback_drops'] += self._eventDispatcher.queue.dropped
		if self._eventPreviewWorker is not None:
			self.eventStats['preview_drops'] += self._eventPreviewWorker.queue.dropped
		if self._eventRecorderWorker is not None:
			self.eventStats['recorder_drops'] += self._eventRecorderWorker.queue.dropped
		if all_stopped:
			self._eventDispatcher = None
			self._eventPreviewWorker = None
			self._eventRecorderWorker = None
		else:
			# A user callback is allowed to take arbitrarily long.  Preserve
			# the worker references until it returns, and clear `_stopping`
			# only after every prior-session worker is actually gone.  This
			# prevents a raw->histogram re-arm from racing that callback.
			self._scheduleRawWorkerReaper()
		return all_stopped


	def _scheduleRawWorkerReaper(self):
		if self._rawWorkerReaper is not None and self._rawWorkerReaper.is_alive():
			return

		def _reap():
			workers = (self._eventPreviewWorker, self._eventDispatcher, self._eventRecorderWorker)
			for worker in workers:
				if worker is not None:
					worker.thread.join()
			self._eventDispatcher = None
			self._eventPreviewWorker = None
			self._eventRecorderWorker = None
			self._stopping = False

		self._rawWorkerReaper = threading.Thread(target=_reap, daemon=True)
		self._rawWorkerReaper.start()


	def _startCaptureThread(self):
		self._capture_running = True
		self._capture_thread = threading.Thread(target=self._captureLoop, daemon=True)
		self._capture_thread.start()


	def _captureLoop(self):
		"""Background thread: the sole owner of self._device once running.

		Always performs stopScript()/disconnect() cleanup itself,
		immediately before exiting, regardless of *why* it's exiting
		(signaled stop, or an internal error caught here) -- device cleanup
		must never happen from the main thread once this loop has started,
		since nothing in the openmv client library is documented
		thread-safe for concurrent access. See stop()'s docstring.
		"""
		if self._isRawEventsProfile():
			self._captureRawEvents()
			self._capture_running = False

		config = self._profile.config
		expected_width, expected_height = config.resolution
		no_frame_started = None
		consecutive_no_frames = 0
		last_no_frame_warning = None

		while self._capture_running:
			read_started = time.monotonic()
			try:
				frame = self._device.readFrame()
			except Exception as e:
				if self._capture_running:
					self.logger.log(f'Error in CameraOpenMV capture loop: {e}', severity=olab_utils.SEVERITY_ERROR)
					self._capture_running = False
				# else: expected shutdown noise (signaled stop unblocked a
				# pending call, or the call simply timed out as designed).
				break

			if frame is None:
				now = time.monotonic()
				if no_frame_started is None:
					no_frame_started = now
				consecutive_no_frames += 1
				if (now - no_frame_started >= _NO_FRAME_STARTUP_GRACE_SEC
						and consecutive_no_frames >= _NO_FRAME_WARNING_THRESHOLD
						and (last_no_frame_warning is None
							or now - last_no_frame_warning >= _NO_FRAME_WARNING_INTERVAL_SEC)):
					self.logger.log(
						f'CameraOpenMV: no frames from profile {self._profile.profile_id!r} '
						f'for {now - no_frame_started:.1f}s '
						f'({consecutive_no_frames} consecutive; port={self.devicePort!r}; '
						f'capture_running={self._capture_running})',
						severity=olab_utils.SEVERITY_WARNING)
					last_no_frame_warning = now
				# Hardware evidence shows an unavailable stream returns in under
				# 2ms. Back off only that immediate-empty path; a bounded blocking
				# read already provides its own wait and must not be slowed further.
				if time.monotonic() - read_started < _NO_FRAME_IMMEDIATE_BACKOFF_SEC:
					time.sleep(_NO_FRAME_IMMEDIATE_BACKOFF_SEC)
				continue

			no_frame_started = None
			consecutive_no_frames = 0
			last_no_frame_warning = None

			if frame.get('format') != _PIXFORMAT_GRAYSCALE:
				self.logger.log(
					f"CameraOpenMV: dropping frame with unexpected format {frame.get('format')!r}",
					severity=olab_utils.SEVERITY_WARNING)
				continue

			if frame.get('width') != expected_width or frame.get('height') != expected_height:
				self.logger.log(
					f"CameraOpenMV: dropping frame with unexpected size "
					f"{frame.get('width')}x{frame.get('height')} (expected {expected_width}x{expected_height})",
					severity=olab_utils.SEVERITY_WARNING)
				continue

			data = frame.get('data')
			if data is None:
				self.logger.log('CameraOpenMV: dropping frame with no data (conversion failure)', severity=olab_utils.SEVERITY_WARNING)
				continue

			if len(data) != expected_width * expected_height * 3:
				self.logger.log(
					f'CameraOpenMV: dropping frame with unexpected data length {len(data)} '
					f'(expected {expected_width * expected_height * 3})',
					severity=olab_utils.SEVERITY_WARNING)
				continue

			rgb = np.frombuffer(data, dtype=np.uint8).reshape(expected_height, expected_width, 3)
			bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
			bgr = self._applyHistogramDisplayPolicy(bgr)
			if self._hasMovementRegions():
				self._readMovementRecord()
				bgr = self._overlayMovementRegions(bgr)
			bgr = self.zoomFunction(bgr)

			self._publishFrame(bgr)

		# Sole owner of the device from here on -- clean up exactly once,
		# regardless of why we're exiting.
		device, self._device = self._device, None
		if device is not None:
			try:
				device.stopScript()
			except Exception:
				pass  # best-effort; disconnect() below is what actually matters
			try:
				device.disconnect()
			except Exception:
				pass
		workers_stopped = self._stopRawWorkers() if self._isRawEventsProfile() else True

		self.camOn = False
		self._stopping = not workers_stopped
		self._captureThreadDone.set()


	def _captureRawEvents(self):
		"""Capture-owner raw channel loop; it only decodes and enqueues work."""
		decoder = EVT20Decoder()
		sequence = 0
		while self._capture_running:
			try:
				status = self._device.readChannelStatus()
				if not status.get('raw_events'):
					time.sleep(0.002)
					continue
				size = self._device.channelSize('raw_events')
				if not size:
					time.sleep(0.002)
					continue
				payload = self._device.readChannel('raw_events', size)
				sequence += 1
				try:
					batch = decoder.decode(payload, sequence)
				except (EventDecodeError, TypeError, ValueError) as e:
					# A corrupt channel payload is data loss, not a connection loss.
					# Preserve capture ownership and wait for the next independent
					# payload rather than tearing down a live event session.
					self.eventStats['decode_errors'] += 1
					self.logger.log(f'CameraOpenMV: dropping malformed raw event batch: {e}', severity=olab_utils.SEVERITY_WARNING)
					continue
			except Exception as e:
				if self._capture_running:
					self.eventStats['decode_errors'] += 1
					self.logger.log(f'CameraOpenMV raw capture error: {e}', severity=olab_utils.SEVERITY_ERROR)
					self._capture_running = False
				break
			self.eventStats['batches'] += 1
			self.eventStats['events'] += batch.count
			if self._eventDispatcher is not None:
				self._eventDispatcher.submit(batch)
			if self._eventPreviewWorker is not None:
				self._eventPreviewWorker.submit(batch)
			if self._eventRecorderWorker is not None:
				self._eventRecorderWorker.submit(batch)


	def stop(self, stopStream=True):
		"""Stop the capture thread and OpenMV device, deterministically and
		idempotently.

		Unlike CameraRealSense's pipeline.stop()-unblocks-wait_for_frames()
		contract, nothing in the `openmv` client library is documented
		thread-safe for concurrent access -- this method never touches
		self._device directly. It signals the capture thread and waits
		(bounded) for it to finish; the capture thread itself is the only
		thing that ever calls stopScript()/disconnect(), whether it exits
		promptly or only after its currently in-flight call eventually
		returns (bounded by the device's own protocol timeout -- every
		blocking client call is bounded to roughly `2 * timeout`, confirmed
		against the real client's `Transport.recv_packet()`, so it is never
		indefinite).

		If the join times out, this method logs a warning and returns
		without touching the device -- self._stopping stays True, and the
		capture thread completes cleanup and sets self._captureThreadDone
		itself whenever its blocked call returns. A second stop() call made
		while already stopping is safe and idempotent: it simply re-joins.

		Args:
			stopStream (bool): Whether to also stop the streaming server.
		"""
		self.camOn = False
		self._capture_running = False
		self._stopping = True

		if self._capture_thread is not None:
			self._capture_thread.join(timeout=4 * self._deviceTimeout())
			if self._capture_thread.is_alive():
				self.logger.log(
					'CameraOpenMV.stop(): capture thread did not exit within the bounded join '
					'timeout; device cleanup will complete asynchronously once it does.',
					severity=olab_utils.SEVERITY_WARNING)
				# Do NOT touch self._device here -- the thread may still be
				# using it. self._stopping stays True until _captureLoop's
				# own cleanup runs and sets _captureThreadDone.
			else:
				self._capture_thread = None
				# _captureLoop already performed its own device cleanup and
				# set _captureThreadDone before exiting.
				# A callback/recorder worker may still be draining.  In that
				# case its reaper owns the transition back to False; clearing it
				# here would permit an unsafe re-arm.
				if self._rawWorkerReaper is None or not self._rawWorkerReaper.is_alive():
					self._stopping = False

		if stopStream:
			self.stopStream()


	def shutdown(self):
		"""Stop the camera and give the streaming server time to fully close."""
		self.stop()
		time.sleep(STREAM_MAX_WAIT_TIME_SEC + 1)


	def changeResolutionFramerate(self, res_rows=None, res_cols=None, framerate=None):
		"""Change the profile's histogram rate via a stop/re-render/start
		cycle -- the same restart-based approach CameraRealSense uses.

		Resolution cannot be changed in this phase: the profile's
		resolution is fixed and validated at construction/replace time.

		Args:
			res_rows/res_cols (int, optional): Must equal the profile's
				current fixed resolution if given, or ValueError is raised.
			framerate (int, optional): New histogram rate. If None, keeps
				the current value. Re-validated against the profile
				config's own range via `dataclasses.replace()` (so an
				out-of-range value still raises ValueError, not just a
				silent clamp).
		"""
		config = self._profile.config
		if self._isRawEventsProfile():
			if framerate is not None:
				raise ValueError('raw-event profiles use preview_rate_hz, not changeResolutionFramerate()')
			return
		if res_rows is not None and int(res_rows) != config.resolution[0]:
			raise ValueError(f'res_rows must be {config.resolution[0]} for profile {self._profile.profile_id!r}, got {res_rows!r}')
		if res_cols is not None and int(res_cols) != config.resolution[1]:
			raise ValueError(f'res_cols must be {config.resolution[1]} for profile {self._profile.profile_id!r}, got {res_cols!r}')

		try:
			framerate = self.defaultFromNone(framerate, config.histogram_rate_hz, int)

			if framerate != config.histogram_rate_hz:
				self.stop(stopStream=False)
				time.sleep(1)
				# dataclasses.replace() re-invokes __post_init__, so an
				# out-of-range framerate still raises ValueError here
				# rather than being silently accepted.
				self._profile.config = replace(config, histogram_rate_hz=framerate)
				self.start(framerate=framerate)

			self.logger.log(f'rows: {self.res_rows}, cols: {self.res_cols}, framerate: {self.framerate}', severity=olab_utils.SEVERITY_DEBUG)
		except Exception as e:
			self.logger.log(f'Failed to change to {res_rows} rows, {res_cols} cols, {framerate} framerate: {e}', severity=olab_utils.SEVERITY_ERROR)


	def changeZoom(self, zoomLevel):
		"""Change zoom level using digital zoom (crop and resize).

		Identical to every other Camera backend -- purely software,
		operating on whatever ndarray is already in frameDeque. Nothing
		about the OpenMV backend changes this behavior.

		Args:
			zoomLevel (float): Zoom level where 1.0 = no zoom, 2.0 = 2x zoom, etc.
		"""
		self._changeZoom(zoomLevel)


	def getFrameAndMeta(self):
		"""Return the current frame and its metadata together, atomically.

		Returns:
			tuple[numpy.ndarray, dict]: `(frame, meta)`, where `meta` is
			`{'host_receipt_time': float, 'host_receipt_wall_time': float,
			'sequence': int}` -- host receipt time, never a sensor-exposure
			timestamp.

		Warning:
			This is the *only* accessor that guarantees a matched pair.
			Calling `getFrame()` and separately inspecting metadata does
			not, since the capture thread can replace the single-slot
			frameDeque between the two calls. Raises IndexError if no frame
			has arrived yet, same as the base class's getFrame().
		"""
		with self.condition:
			return self.frameDeque[0], dict(self._latestFrameMeta)
