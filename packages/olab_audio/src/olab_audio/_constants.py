import pyaudio

FORMAT = pyaudio.paInt16
SPEAKER_FORMAT = pyaudio.paFloat32
CHANNELS = 1
SAMPLERATE = 44100
CHUNK = 512

# Normalize int16 PCM to float32 in [-1.0, +1.0].
# See https://stackoverflow.com/questions/16778878/python-write-a-wav-file-into-numpy-float-array/62298670#62298670
ONE_OVER_MAX_INT16 = 1 / 32768.0  # 1/2**15
