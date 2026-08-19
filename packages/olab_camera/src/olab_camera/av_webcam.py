"""Thin composition of a paired USB webcam's video and audio devices."""

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .camera_usb import CameraUSB

try:
	from olab_audio import Mic
except Exception as e:
	print(f'INFO: olab_audio is not installed and was not imported. You may '
	      f'ignore this message. Unless you are using AVWebcam you do not '
	      f'need olab_audio.')
	Mic = None


class AVWebcam:
	"""Composes one CameraUSB + one Mic for a single physical USB webcam.

	A caller who already knows a camera device and a mic device for one
	physical webcam (pairing decided by the caller/config -- no discovery
	here) constructs one AVWebcam instead of separately constructing and
	tracking a CameraUSB and a Mic. Both underlying objects are exposed
	directly (`.camera`/`.mic`) rather than re-wrapped -- this class's job
	is composition and lifecycle convenience only.

	`start()`/`stop()` start/stop basic frame/audio capture on both
	devices -- they deliberately do not start MJPEG/WebSocket/WebRTC
	streaming or ROS topic publishing. A caller who wants those makes that
	call directly on `.camera` (`.camera.startStream(...)`,
	`.camera.startROStopic(...)`), same as any other CameraUSB user would.

	Not ffmpeg/muxing/file-writing, not config-file reading or
	device-pairing discovery, not app-specific error reporting.

	Usage:
		>>> av = AVWebcam(camera_device='/dev/video0', mic_device=3)
		>>> av.mic.subscribe(pipe_writer_callback)
		>>> av.start()
		>>> av.camera.startStream(port=8000)
		>>> # ...
		>>> av.stop()
	"""

	def __init__(self, camera_device, mic_device, camera_kwargs=None, mic_kwargs=None):
		"""
		camera_device - Passed through to CameraUSB(device=camera_device, ...).
		mic_device    - Passed through to Mic(deviceID=mic_device, ...).
		camera_kwargs - Optional dict of additional CameraUSB() constructor
		                kwargs (e.g. paramDict, initROSnode, logger, sslPath).
		mic_kwargs    - Optional dict of additional Mic() constructor kwargs
		                (e.g. samplerate, channels, postFunc, excFunc).
		"""
		if Mic is None:
			raise ImportError(
				"AVWebcam needs the 'olab_audio' package. "
				"Install with: pip install olab-camera[av]"
			)

		self.camera = CameraUSB(device=camera_device, **(camera_kwargs or {}))
		self.mic = Mic(deviceID=mic_device, **(mic_kwargs or {}))

	def start(self):
		"""Start basic capture on both devices. If the camera starts but the
		mic fails (or vice versa), stop the succeeded half and raise --
		never leaves a half-open object."""
		self.camera.start()
		if not self.camera.camOn:
			raise RuntimeError('AVWebcam.start(): camera failed to start')

		self.mic.start()
		if not self.mic.micOn:
			self.camera.stop()
			raise RuntimeError('AVWebcam.start(): mic failed to start (camera stopped)')

	def stop(self):
		"""Stop both devices. Each stop is independently try/excepted so a
		failure stopping one half doesn't prevent an attempt to stop the
		other -- including when the diagnostic reporting itself (the
		camera's logger, the mic's excFunc) also raises, which must not be
		allowed to skip the second stop attempt or escape stop() itself."""
		try:
			self.camera.stop()
		except Exception as e:
			try:
				self.camera.logger.log(f'AVWebcam.stop(): error stopping camera: {e}',
				                        severity=olab_utils.SEVERITY_ERROR)
			except Exception as log_e:
				print(f'AVWebcam.stop(): error stopping camera: {e} '
				      f'(and camera.logger.log itself raised: {log_e})')
		try:
			self.mic.stop()
		except Exception as e:
			try:
				self.mic.excFunc(msg=f'AVWebcam.stop(): error stopping mic: {e}')
			except Exception as exc_e:
				print(f'AVWebcam.stop(): error stopping mic: {e} '
				      f'(and mic.excFunc itself raised: {exc_e})')
