import numpy as np
import pyaudio

from ._constants import CHANNELS, CHUNK, FORMAT, ONE_OVER_MAX_INT16, SAMPLERATE
from ._util import convert_to_db, defaultFromNone
from .device import audio
from .device import terminate as _terminate_all
from .recording import Recording_bytes, Recording_np


def _passFunction(*args, **kwargs):
	'''a dummy function that does nothing'''
	pass


def _exceptionFunction(msg, *args, **kwargs):
	'''The default function to call when an exception is raised. Other functions could be called instead.'''
	print(msg)


class Mic():
	def __init__(self, deviceID=None, samplerate=None, frmt=FORMAT, channels=CHANNELS,
					   frames_per_buffer=CHUNK, postFunc=_passFunction, excFunc=_exceptionFunction,
					   callbackType='np'):

		'''
		See https://people.csail.mit.edu/hubert/pyaudio/docs/#class-pyaudio-stream
		deviceID   - Index of input device to use.  Try `olab_audio.get_input_devices()` to see options.
		samplerate - Sampling rate. If None (the default), queries the device's own
					 reported default sample rate instead of assuming one rate works
					 universally -- some hardware (e.g. certain USB mics) only supports
					 a non-44100Hz rate and fails to open at a hardcoded default.
		frmt       - Sampling size and format. paFloat32, paInt32, paInt24, paInt16, paInt8, paUInt8
		channels   - Number of audio channels
		frames_per_buffer -

		postFunc - Function to call when .stop() function is executed.  This could be an external function.
		excFunc  - Function to call if an exception occurs.  This could be an external function.

		callbackType - 'np' or 'bytes'.  Specifies which flavor of callback function we use.
		'''
		if (deviceID is None):
			raise Exception('Error:  Must provide deviceID.')

		self.deviceID = deviceID   # This mic is locked in to this deviceID.
		self.postFunc = postFunc   # What to do when .stop() is called?

		self.micOn = False

		# pyaudio stuff - Save our initial values
		self.FORMAT     = self.frmt     = frmt
		self.CHANNELS   = self.channels = channels

		self.SAMPLERATE = samplerate  # None means "query the device's own default at start() time" -- see start().
		self.CHUNK      = frames_per_buffer
		self.POSTFUNC   = postFunc

		self.excFunc = excFunc

		self.samplerate     = -1		# This is on the capture side

		self.np_data        = np.array([], dtype=np.float32)

		self.recording      = None      # Can become a Recording object
		self.isRecording    = False

		# self.stream stays None until start() successfully opens a PortAudio
		# stream. Any code that touches self.stream (_stop_stream()) checks
		# for None first, so a failed/never-started open can never leave a
		# "half-open" Mic whose .stop() crashes with AttributeError.
		self.stream = None

		if (callbackType == 'bytes'):
			self.callbackFunc = self._callback_record_bytes
			self.recordingCls = Recording_bytes
		else:
			self.callbackFunc = self._callback_np
			self.recordingCls = Recording_np

	@property
	def db(self):
		return convert_to_db(self.np_data)

	def _callback_record_bytes(self, in_data, frame_count, time_info, status):
		'''
		in_data is raw bytes data

		This version is optimized for RPi recording.  It saves audio file from raw bytes.
		There is no conversion to numpy array.  Need to use `Recording_bytes` class.
		'''

		if (self.isRecording):
			if (self.recording.time_remain > 0):
				self.recording.append(in_data)
			else:
				self.recordStop()

		self.reachbackFunc(deviceID=self.deviceID, data=in_data)

		return (in_data, pyaudio.paContinue)

	def _callback_np(self, in_data, frame_count, time_info, status):
		'''
		in_data is raw bytes data
		'''

		# Convert to numpy array and normalize in range [-1.0, +1.0]
		self.np_data = np.frombuffer(in_data, np.int16).astype(np.float32) * ONE_OVER_MAX_INT16
		# self.np_data.shape will be (frames_per_buffer,)
		# This data will be at the given samplerate

		if (self.isRecording):
			if (self.recording.time_remain > 0):
				self.recording.append(self.np_data)
			else:
				self.recordStop()

		self.reachbackFunc(deviceID=self.deviceID, data=self.np_data)

		return (in_data, pyaudio.paContinue)

	def make_recording(self, samplerateRec, timeLimitSec=None, filepath='.', filename=None, postFunc=_passFunction):
		return self.recordingCls(self.samplerate, samplerateRec, self.frmt, self.channels, timeLimitSec, filepath, filename, postFunc)

	def recordStart(self, samplerateRec=None, timeLimitSec=None, filepath='.', filename=None, postFunc=_passFunction):
		'''
		Returns True if recording actually started, False otherwise (e.g. a
		cross-rate Recording_np whose `resample` extra isn't installed, or a
		cross-rate Recording_bytes -- both raise inside make_recording()).
		On failure, self.isRecording is left False and self.recording is
		left None -- check the return value (or those attributes) rather
		than assuming success; the failure is also reported to excFunc, but
		that alone must not be the only way to detect it.
		'''
		try:
			samplerateRec = defaultFromNone(samplerateRec, self.samplerate)
			self.recording = self.make_recording(samplerateRec, timeLimitSec, filepath, filename, postFunc)

			self.isRecording = True
			return True
		except Exception as e:
			self.recording = None
			self.isRecording = False
			self.excFunc(msg = f'ERROR in recordStart: {e}')
			return False

	def recordStop(self, filepath=None, filename=None):
		try:
			self.isRecording       = False

			# Try to write to file if a filename has been given (either here or in `recordStart()`):
			self.recording.save(filepath, filename)

			self.recording.postFunc(deviceID=self.deviceID)
		except Exception as e:
			self.excFunc(msg = f'ERROR in recordStop: {e}')

	def _default_samplerate(self):
		'''Query this device's own reported default sample rate.

		Some hardware (confirmed via real-hardware testing: a USB webcam
		mic) only supports a non-44100Hz rate and fails to open with
		PortAudio's "Invalid sample rate" (-9997) if 44100 is assumed
		unconditionally. Falls back to the module default only if the
		device query itself fails.
		'''
		try:
			info = audio.get_device_info_by_host_api_device_index(0, self.deviceID)
			return int(info['defaultSampleRate'])
		except Exception as e:
			self.excFunc(msg = f'Could not read defaultSampleRate for deviceID={self.deviceID}: {e} -- using {SAMPLERATE}Hz')
			return SAMPLERATE

	def start(self, frmt=None, channels=None, samplerate=None, frames_per_buffer=None,
			  reachbackFunc=_passFunction, postFunc=None):
		try:
			# If user didn't provide a parameter, use the default value
			self.frmt              = defaultFromNone(frmt, self.FORMAT, int)
			self.channels          = defaultFromNone(channels, self.CHANNELS, int)
			samplerate              = defaultFromNone(samplerate, self.SAMPLERATE)
			self.samplerate         = int(samplerate) if samplerate is not None else self._default_samplerate()
			self.frames_per_buffer = defaultFromNone(frames_per_buffer, self.CHUNK, int)

			self.reachbackFunc = reachbackFunc

			self.postFunc = defaultFromNone(postFunc, self.POSTFUNC)

			# Start capturing
			self.stream = audio.open(format=self.frmt, channels=self.channels,
							rate=self.samplerate, input=True, input_device_index = self.deviceID,
							frames_per_buffer=self.frames_per_buffer, output=False, stream_callback=self.callbackFunc)

			self.micOn = True
		except Exception as e:
			self.excFunc(msg = f'ERROR in start: {e}')

	def _stop_stream(self):
		# Close the stream, if one was ever successfully opened. self.stream
		# stays None (set in __init__) if start() failed before completing
		# audio.open() -- guarding here means .stop() is always safe to call,
		# regardless of whether start() ever succeeded.
		if self.stream is None:
			return
		try:
			self.stream.stop_stream()
			self.stream.close()
		except Exception as e:
			self.excFunc(msg = f'Could not stop stream: {e}')
		else:
			print('stream stopped')
		finally:
			self.stream = None

	def stop(self, postFunc=None):
		# Stop the processes
		self.isRecording = False

		# Stop the thread
		self.micOn = False

		# Close the stream
		self._stop_stream()

		try:
			postFunc = defaultFromNone(postFunc, self.postFunc)
			postFunc()
		except Exception as e:
			self.excFunc(msg = f'Error in stop postFunc: {e}')

	def terminate(self):
		'''
		Release PortAudio system resources
		This is for all devices.  Do this when you're done with **everything**.
		'''
		self.stop()
		_terminate_all()
