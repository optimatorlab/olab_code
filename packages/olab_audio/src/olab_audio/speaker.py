from ._constants import CHANNELS, SAMPLERATE, SPEAKER_FORMAT
from .device import audio, get_output_devices


class Speaker():
	def __init__(self, deviceID=None):
		'''
		See https://people.csail.mit.edu/hubert/pyaudio/docs/#class-pyaudio-stream
		deviceID   - Index of output device to use.  Try `olab_audio.get_output_devices()` to see options.
		'''

		if (deviceID is None):
			# Let's just try to get the default output
			try:
				devices = get_output_devices()
				for i in devices:
					if (i['name'] == 'default'):
						deviceID = i['deviceID']
						break
			except Exception as e:
				print(f'Error in Speaker init: {e}')

			if (deviceID is None):
				raise Exception('Error:  Must provide deviceID. Could not find default.')

		self.deviceID = deviceID   # This speaker is locked in to this deviceID.
		self.stream = None

	def play(self, samples, volume=0.2, samplerate=SAMPLERATE, frmt=SPEAKER_FORMAT, channels=CHANNELS):
		'''
		Play a sound thru a speaker.

		samples    - 32-bit np array, waveform.
		volume 	   - float in [0, 1]
		samplerate - Sampling rate
		frmt       - Sampling size and format. paFloat32, paInt32, paInt24, paInt16, paInt8, paUInt8
		             paFloat32 seems to be the only one that works well here.
		             NOTE:  Mic class uses paInt16 (FORMAT)
		channels   - Number of audio channels
		'''

		samples = samples.astype('float32')

		# for paFloat32 sample values must be in range [-1.0, 1.0]
		self.stream = audio.open(format=frmt,
						channels=channels,
						rate=samplerate,
						output=True)

		# Convert 32-bit np array to bytes sequence
		output_bytes = (volume * samples).tobytes()

		# Play.
		self.stream.write(output_bytes)

		self.stop()

	def stop(self):
		# Stop the speaker
		if self.stream is None:
			return
		self.stream.stop_stream()
		self.stream.close()
		self.stream = None

	def terminate(self):
		'''
		Release PortAudio system resources
		This is for all devices.  Do this when you're done with **everything**.
		'''
		from .device import terminate
		terminate()
