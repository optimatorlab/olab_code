"""DSP, teaching, and research toolkit: tone/chirp/pitch synthesis, spectral
analysis (Wave/Spectrogram/Spectrum), trim/normalize/resample, and
matplotlib-based plotting helpers.

Requires the `analysis` extra (librosa, soundfile, matplotlib) -- this
module is not imported by olab_audio's core (Mic/Speaker/Recording*/device
enumeration), which works with only pyaudio+pulsectl+numpy installed.
"""

import warnings

import librosa
import numpy as np

from .recording import saveAudio
from ._resample import resample

# These values are from https://homes.luddy.indiana.edu/donbyrd/Teach/MusicalPitchesTable.htm
pitch_map = {'C0': {'freq': 16.352,   'midi':  12},'C#0': {'freq':   17.324, 'midi':  13},'D0': {'freq':   18.354, 'midi':  14},'D#0': {'freq':   19.445, 'midi':  15},'E0': {'freq':    20.602, 'midi':  16},'F0': {'freq':    21.827, 'midi':  17},'F#0': {'freq':    23.125, 'midi':  18},'G0': {'freq':    24.500, 'midi':  19},'G#0': {'freq':   25.957, 'midi':  20},'A0': {'freq':  27.5, 'midi':  21},'A#0': {'freq':   29.135, 'midi':  22},'B0': {'freq':   30.868, 'midi':  23},
			 'C1': {'freq': 32.703,   'midi':  24},'C#1': {'freq':   34.648, 'midi':  25},'D1': {'freq':   36.708, 'midi':  26},'D#1': {'freq':   38.891, 'midi':  27},'E1': {'freq':    41.203, 'midi':  28},'F1': {'freq':    43.654, 'midi':  29},'F#1': {'freq':    46.249, 'midi':  30},'G1': {'freq':    48.999, 'midi':  31},'G#1': {'freq':   51.913, 'midi':  32},'A1': {'freq':  55,   'midi':  33},'A#1': {'freq':   58.270, 'midi':  34},'B1': {'freq':   61.735, 'midi':  35},
			 'C2': {'freq': 65.406,   'midi':  36},'C#2': {'freq':   69.296, 'midi':  37},'D2': {'freq':   73.416, 'midi':  38},'D#2': {'freq':   77.782, 'midi':  39},'E2': {'freq':    82.407, 'midi':  40},'F2': {'freq':    87.307, 'midi':  41},'F#2': {'freq':    92.499, 'midi':  42},'G2': {'freq':    97.999, 'midi':  43},'G#2': {'freq':  103.826, 'midi':  44},'A2': {'freq':  110,  'midi':  45},'A#2': {'freq':  116.541, 'midi':  46},'B2': {'freq':  123.471, 'midi':  47},
			 'C3': {'freq': 130.813,  'midi':  48},'C#3': {'freq':  138.591, 'midi':  49},'D3': {'freq':  146.832, 'midi':  50},'D#3': {'freq':  155.564, 'midi':  51},'E3': {'freq':   164.814, 'midi':  52},'F3': {'freq':   174.614, 'midi':  53},'F#3': {'freq':  184.997, 'midi':  54},'G3': {'freq':   195.998, 'midi':  55},'G#3': {'freq':  207.652, 'midi':  56},'A3': {'freq':  220,  'midi':  57},'A#3': {'freq':  233.082, 'midi':  58},'B3': {'freq':  246.942, 'midi':  59},
			 'C4': {'freq': 261.626,  'midi':  60},'C#4': {'freq':  277.183, 'midi':  61},'D4': {'freq':  293.665, 'midi':  62},'D#4': {'freq':  311.127, 'midi':  63},'E4': {'freq':   329.628, 'midi':  64},'F4': {'freq':   349.228, 'midi':  65},'F#4': {'freq':  369.994, 'midi':  66},'G4': {'freq':   391.995, 'midi':  67},'G#4': {'freq':  415.305, 'midi':  68},'A4': {'freq':  440,  'midi':  69},'A#4': {'freq':  466.164, 'midi':  70},'B4': {'freq':  493.883, 'midi':  71},
			 'C5': {'freq': 523.251,  'midi':  72},'C#5': {'freq':  554.365, 'midi':  73},'D5': {'freq':  587.330, 'midi':  74},'D#5': {'freq':  622.254, 'midi':  75},'E5': {'freq':   659.255, 'midi':  76},'F5': {'freq':   698.457, 'midi':  77},'F#5': {'freq':  739.989, 'midi':  78},'G5': {'freq':   783.991, 'midi':  79},'G#5': {'freq':  830.609, 'midi':  80},'A5': {'freq':  880,  'midi':  81},'A#5': {'freq':  932.328, 'midi':  82},'B5': {'freq':  987.767, 'midi':  83},
			 'C6': {'freq': 1046.502, 'midi':  84},'C#6': {'freq': 1108.731, 'midi':  85},'D6': {'freq': 1174.659, 'midi':  86},'D#6': {'freq': 1244.508, 'midi':  87},'E6': {'freq':  1318.510, 'midi':  88},'F6': {'freq':  1396.913, 'midi':  89},'F#6': {'freq': 1479.978, 'midi':  90},'G6': {'freq':  1567.982, 'midi':  91},'G#6': {'freq': 1661.219, 'midi':  92},'A6': {'freq': 1760,  'midi':  93},'A#6': {'freq': 1864.655, 'midi':  94},'B6': {'freq': 1975.533, 'midi':  95},
			 'C7': {'freq': 2093.005, 'midi':  96},'C#7': {'freq': 2217.461, 'midi':  97},'D7': {'freq': 2349.318, 'midi':  98},'D#7': {'freq': 2489.016, 'midi':  99},'E7': {'freq':  2637.021, 'midi': 100},'F7': {'freq':  2793.826, 'midi': 101},'F#7': {'freq': 2959.956, 'midi': 102},'G7': {'freq':  3135.964, 'midi': 103},'G#7': {'freq': 3322.438, 'midi': 104},'A7': {'freq': 3520,  'midi': 105},'A#7': {'freq': 3729.310, 'midi': 106},'B7': {'freq': 3951.066, 'midi': 107},
			 'C8': {'freq': 4186.009, 'midi': 108},'C#8': {'freq': 4434.922, 'midi': 109},'D8': {'freq': 4698.637, 'midi': 110},'D#8': {'freq': 4978.032, 'midi': 111},'E8': {'freq':  5274.042, 'midi': 112},'F8': {'freq':  5587.652, 'midi': 113},'F#8': {'freq': 5919.912, 'midi': 114},'G8': {'freq':  6271.928, 'midi': 115},'G#8': {'freq': 6644.876, 'midi': 116},'A8': {'freq': 7040,  'midi': 117},'A#8': {'freq': 7458.620, 'midi': 118},'B8': {'freq': 7902.133, 'midi': 119},
			 'C9': {'freq': 8372.019, 'midi': 120},'C#9': {'freq': 8869.845, 'midi': 121},'D9': {'freq': 9397.273, 'midi': 122},'D#9': {'freq': 9956.064, 'midi': 123},'E9': {'freq': 10548.083, 'midi': 124},'F9': {'freq': 11175.305, 'midi': 125},'F#9': {'freq': 11839.823, 'midi': 126},'G9': {'freq': 12543.855, 'midi': 127}}


def createPitch(pitch, sr=22050, length=None, duration=None):
	'''
	Construct a pure tone (cosine) signal corresponding to a given musical note (pitch).

	Parameters
		- pitch - string.  Musical note (e.g., 'A4' or 'F#3')
		- sr.  number > 0.  Desired sampling rate of the output signal
		- length.  int > 0.  Desired number of samples in the output signal. When both duration and length are defined, length takes priority.
		- duration. float > 0.  Desired duration in seconds. When both duration and length are defined, length takes priority.
	'''
	pitch = pitch.upper()
	if (pitch not in pitch_map):
		raise Exception(f'(Unknown pitch: {pitch}')

	tone = createTone(pitch_map[pitch]['freq'], sr=sr, length=length, duration=duration)
	return tone


def createTone(frequency, sr=22050, length=None, duration=None, phi=-np.pi*0.5):
	# A wrapper for librosa `tone` function
	# See https://librosa.org/doc/latest/generated/librosa.tone.html#librosa-tone
	'''
	Construct a pure tone (cosine) signal at a given frequency.

	Parameters
		- frequency.  float > 0.  Frequency, in [Hz].
		- sr.  number > 0.  Desired sampling rate of the output signal
		- length.  int > 0.  Desired number of samples in the output signal. When both duration and length are defined, length takes priority.
		- duration. float > 0.  Desired duration in seconds. When both duration and length are defined, length takes priority.
		- phi. float or None.  Phase offset, in radians. If unspecified, defaults to -np.pi * 0.5.

	Returns
		- tone. A `Wave()` object converted to float32.
	'''
	try:
		tone = librosa.tone(frequency, sr=sr, length=length, duration=duration, phi=phi)
		return Wave(tone.astype('float32'), framerate=sr)
	except Exception as e:
		print(f'ERROR in createTone: {e}')

def createChirp(fmin, fmax, sr=22050, length=None, duration=None, linear=False, phi=-np.pi*0.5):
	# A wrapper for librosa `chirp` function
	# See https://librosa.org/doc/latest/generated/librosa.chirp.html#librosa.chirp
	'''
	Construct a "chirp" or "sine-sweep" signal.
	The chirp sweeps from frequency fmin to fmax (in Hz).

	Parameters
		- fmin.  float > 0.    Initial frequency
		- fmax.  float > 0.    Final frequency
		- sr. number > 0.      Desired sampling rate of the output signal
		- length. int > 0.     Desired number of samples in the output signal. When both duration and length are defined, length takes priority.
		- duration. float > 0. Desired duration in seconds. When both duration and length are defined, length takes priority.
		- linear. boolean.     If True, use a linear sweep, i.e., frequency changes linearly with time; If False, use a exponential sweep.  Default is False.
		- phi. float or None.  Phase offset, in radians. If unspecified, defaults to -np.pi * 0.5.

	Returns
		- chirp. A `Wave()` object converted to float32.
	'''
	try:
		chirp = librosa.chirp(fmin=fmin, fmax=fmax, sr=sr, length=length, duration=duration, linear=linear, phi=phi)
		return Wave(chirp.astype('float32'), framerate=sr)
	except Exception as e:
		print(f'ERROR in createChirp: {e}')


def decorate(**options):
	"""Decorate the current axes.

	Call decorate with keyword arguments like

	decorate(title='Title',
			 xlabel='x',
			 ylabel='y')

	The keyword arguments can be any of the axis properties

	https://matplotlib.org/api/axes_api.html

	In addition, you can use `legend=False` to suppress the legend.

	And you can use `loc` to indicate the location of the legend
	(the default value is 'best')
	"""
	import matplotlib.pyplot as plt

	loc = options.pop("loc", "best")
	if options.pop("legend", True):
		legend(loc=loc)

	plt.gca().set(**options)
	plt.tight_layout()

def legend(**options):
	"""Draws a legend only if there is at least one labeled item.

	options are passed to plt.legend()
	https://matplotlib.org/api/_as_gen/matplotlib.plt.legend.html
	"""
	import matplotlib.pyplot as plt

	underride(options, loc="best", frameon=False)

	ax = plt.gca()
	handles, labels = ax.get_legend_handles_labels()
	if handles:
		ax.legend(handles, labels, **options)


def remove_from_legend(bad_labels):
	"""Removes some labels from the legend.

	bad_labels: sequence of strings
	"""
	import matplotlib.pyplot as plt

	ax = plt.gca()
	handles, labels = ax.get_legend_handles_labels()
	handle_list, label_list = [], []
	for handle, label in zip(handles, labels):
		if label not in bad_labels:
			handle_list.append(handle)
			label_list.append(label)
	ax.legend(handle_list, label_list)


def read_wave(filename="sound.wav"):
	# Copied from thinkDSP
	"""Reads a wave file.

	filename: string

	returns: Wave
	"""
	from wave import open as open_wave

	fp = open_wave(filename, "r")

	nchannels = fp.getnchannels()
	nframes = fp.getnframes()
	sampwidth = fp.getsampwidth()
	framerate = fp.getframerate()

	z_str = fp.readframes(nframes)

	fp.close()

	dtype_map = {1: np.int8, 2: np.int16, 3: "special", 4: np.int32}
	if sampwidth not in dtype_map:
		raise ValueError("sampwidth %d unknown" % sampwidth)

	if sampwidth == 3:
		xs = np.frombuffer(z_str, dtype=np.int8).astype(np.int32)
		ys = (xs[2::3] * 256 + xs[1::3]) * 256 + xs[0::3]
	else:
		ys = np.frombuffer(z_str, dtype=dtype_map[sampwidth])

	# if it's in stereo, just pull out the first channel
	if nchannels == 2:
		ys = ys[::2]

	wave = Wave(ys, framerate=framerate)
	wave.normalize()
	return wave

def read_wave_librosa(filepath='.', filename=None, samplerate=None):
	'''
	Reads an audio file.
	filepath:  Full filepath where the audio file will be saved.  Default '.' will read from current directory.
	filename:  Name of file.  Should end in `.wav`.
	samplerate:  Desired sample rate of the audio, in [Hz] (e.g., 44100).
		Leave samplerate None if you want the audio file to be loaded at its native sample rate.

	Returns
		Wave

	# https://librosa.org/doc/latest/tutorial.html#overview
	# https://librosa.org/doc/latest/generated/librosa.load.html#librosa.load
	'''

	if (filename is None):
		raise Exception('read_wave_librosa() missing filename')

	try:
		ys, framerate = librosa.load(f'{filepath}/{filename}', sr=samplerate)
		wave = Wave(ys, framerate=framerate)
		wave.normalize()
		return wave

	except Exception as e:
		print(f'Error in read_wave_librosa: {e}')


def trim(y, top_db=60, ref=np.max, frame_length=2048, hop_length=512, aggregate=np.max):
	# A wrapper for `librosa.effects.trim` function
	# See https://librosa.org/doc/latest/generated/librosa.effects.trim.html#librosa-effects-trim
	'''
	Trim leading and trailing silence from an audio signal.

	Parameters
		- y.  np.ndarray.  Audio signal, can be mono or stereo.
		- top_db.  number > 0.  The threshold (in decibels) below reference to consider as silence.
		- ref.  number or callable.  The reference amplitude.
		- frame_length.  int > 0.  The number of samples per analysis frame.
		- hop_length.  int > 0.  The number of samples between analysis frames.
		- aggregate.  callable [default: np.max].  Function to aggregate across channels.

	Returns
		- y_trimmed.  np.ndarray.  The trimmed signal.
		- index.  np.ndarray, shape=(2,).  The interval of y corresponding to the non-silent region:
			y_trimmed = y[index[0]:index[1]] (for mono) or
			y_trimmed = y[:, index[0]:index[1]] (for stereo).
	'''
	try:
		return librosa.effects.trim(y, top_db=top_db, ref=ref, frame_length=frame_length, hop_length=hop_length, aggregate=aggregate)
	except Exception as e:
		print(f'ERROR in trim: {e}')


def underride(d, **options):
	"""Add key-value pairs to d only if key is not in d.

	If d is None, create a new dictionary.

	d: dictionary
	options: keyword args to add to d
	"""
	if d is None:
		d = {}

	for key, val in options.items():
		d.setdefault(key, val)

	return d


def truncate(ys, n):
	"""Trims a wave array to the given length.

	ys: wave array
	n: integer length

	returns: wave array
	"""
	return ys[:n]

def zero_pad(ys, n):
	"""Extends a wave array with zeros to the given length.

	ys: wave array
	n: integer length (n >= len(ys))

	returns: wave array
	"""
	padded = np.zeros(n, dtype=ys.dtype)
	padded[:len(ys)] = ys
	return padded

def normalize(ys, amp=1.0):
	"""Normalizes a wave array so the maximum amplitude is +amp or -amp.

	ys: wave array
	amp: max amplitude (pos or neg) in result

	returns: wave array
	"""
	high, low = abs(max(ys)), abs(min(ys))
	return amp * ys / max(high, low)

def unbias(ys):
	"""Shifts a wave array so it has mean 0.

	ys: wave array

	returns: wave array
	"""
	return ys - ys.mean()


def stft(data, n_fft=2048, win_length=2048):
	'''
	Short-time Fourier transform (STFT).
	The STFT represents a signal in the time-frequency domain by computing discrete Fourier
	transforms (DFT) over short overlapping windows.

	See https://librosa.org/doc/latest/generated/librosa.stft.html#librosa.stft

	Parameters
		- data.  np.ndarray.  Input signal. Multi-channel is supported.
		- n_fft. int > 0.  Length of the windowed signal after padding with zeros.
		- win_length. int <= n_fft.  Each frame of audio is windowed by window of length
				 win_length and then padded with zeros to match n_fft.
	Returns
		- D.  np.ndarray[shape=(..., 1 + n_fft/2, n_frames)].  Magnitude of the short-term Fourier transform coefficients.
	'''
	# Rather than returning the raw complex-valued STFT matrix, we'll return the magnitude of the frequencies in each bin:
	return np.abs(librosa.stft(data, n_fft=n_fft, win_length=win_length))

def find_index(x, xs):
	'''
	Find the index corresponding to a given value in an array.

	From thinkDSP
	'''
	n = len(xs)
	start = xs[0]
	end = xs[-1]
	i = round((n - 1) * (x - start) / (end - start))
	return int(i)

def ftt_freq(samplerate=22050, n_fft=2048):
	'''
	Returns the Discrete Fourier Transform sample frequencies (i.e., the frequency bins).

	Alternative implementation of np.fft.fftfreq

	See
		- https://librosa.org/doc/latest/generated/librosa.fft_frequencies.html#librosa.fft_frequencies
		- https://stackoverflow.com/questions/63350459/getting-the-frequencies-associated-with-stft-in-librosa

	Parameters
		- samplerate.  number > 0.  Audio sampling rate.
		- n_fft.  int > 0.  FFT window size.
	Returns
		- freqs.  np.ndarray[shape=(1+n_fft/2)]
	'''
	return librosa.fft_frequencies(sr=samplerate, n_fft=n_fft)


def spectrum(ys, framerate, full=False):
	'''
	Given waveform, returns arrays of amplitudes and frequencies.

	Parameters
		ys -- wave array
		framerate -- samples per second
		full -- boolean.  Compute full FFT (vs. real FFT).  We will only do real
	Returns
		hs -- array of amplitudes (real only)
		fs -- array of frequencies

	This code is a combination of Wave.make_spectrum and _SpectrumParent.__init__ from thinkdsp.
	'''

	n = len(ys)
	d = 1 / framerate

	if full:
		hs = np.fft.fft(ys)
		fs = np.fft.fftfreq(n, d)
	else:
		hs = np.fft.rfft(ys)
		fs = np.fft.rfftfreq(n, d)

	return (hs, fs)


def peaks(hs, fs):
	'''
	Modifed from thinkDSP _SpectrumParent.peaks.
	Finds the highest peaks and their frequencies.

	Returns sorted list of (amplitude, frequency) pairs
	'''
	amps = np.absolute(hs)
	t = list(zip(amps, fs))
	t.sort(reverse=True)
	return t


class Spectrogram:
	"""Represents the spectrum of a signal."""

	def __init__(self, spec_map, seg_length):
		"""Initialize the spectrogram.

		spec_map: map from float time to Spectrum
		seg_length: number of samples in each segment
		"""
		self.spec_map = spec_map
		self.seg_length = seg_length

	def any_spectrum(self):
		"""Returns an arbitrary spectrum from the spectrogram."""
		index = next(iter(self.spec_map))
		return self.spec_map[index]

	@property
	def time_res(self):
		"""Time resolution in seconds."""
		spectrum = self.any_spectrum()
		return float(self.seg_length) / spectrum.framerate

	@property
	def freq_res(self):
		"""Frequency resolution in Hz."""
		return self.any_spectrum().freq_res

	def times(self):
		"""Sorted sequence of times.

		returns: sequence of float times in seconds
		"""
		ts = sorted(iter(self.spec_map))
		return ts

	def frequencies(self):
		"""Sequence of frequencies.

		returns: sequence of float freqencies in Hz.
		"""
		fs = self.any_spectrum().fs
		return fs

	def plot(self, low=None, high=None, **options):
		"""Make a pseudocolor plot.

		low:  ignore data below this frequency
		high: ignore data above this frequency
		"""
		import matplotlib.pyplot as plt

		fs = self.frequencies()

		i = None if low  is None else find_index(low,  fs)
		j = None if high is None else find_index(high, fs)
		fs = fs[i:j]
		ts = self.times()

		# make the array
		size = len(fs), len(ts)
		array = np.zeros(size, dtype=float)

		# copy amplitude from each spectrum into a column of the array
		for k, t in enumerate(ts):
			spectrum = self.spec_map[t]
			array[:, k] = spectrum.amps[i:j]

		underride(options, cmap="inferno_r", shading="auto")
		plt.pcolormesh(ts, fs, array, **options)

	def get_data(self, high=None, **options):
		"""Returns spectogram as 2D numpy array

		high: highest frequency component to return
		"""
		fs = self.frequencies()
		i = None if high is None else find_index(high, fs)
		fs = fs[:i]
		ts = self.times()

		# make the array
		size = len(fs), len(ts)
		array = np.zeros(size, dtype=float)

		# copy amplitude from each spectrum into a column of the array
		for j, t in enumerate(ts):
			spectrum = self.spec_map[t]
			array[:, j] = spectrum.amps[:i]

		return array


class Spectrum():
	# This class was mostly copied from Allen Downey's "Think DSP" code:
	# https://github.com/AllenDowney/ThinkDSP
	# It's a combination of the `_SpectrumParent` and `Spectrum` classes.

	def __init__(self, hs, fs, framerate, full=False):
		"""Initializes a spectrum.

		hs: array of amplitudes (real or complex)
		fs: array of frequencies
		framerate: frames per second
		full: boolean to indicate full or real FFT
		"""
		self.hs = np.asanyarray(hs)
		self.fs = np.asanyarray(fs)
		self.framerate = framerate
		self.full = full

	@property
	def max_freq(self):
		"""Returns the Nyquist frequency for this spectrum."""
		return self.framerate / 2

	@property
	def amps(self):
		"""Returns a sequence of amplitudes (read-only property)."""
		return np.absolute(self.hs)

	@property
	def power(self):
		"""Returns a sequence of powers (read-only property)."""
		return self.amps**2

	@property
	def freq_res(self):
		return self.framerate / 2 / (len(self.fs) - 1)

	@property
	def real(self):
		"""Returns the real part of the hs (read-only property)."""
		return np.real(self.hs)

	@property
	def imag(self):
		"""Returns the imaginary part of the hs (read-only property)."""
		return np.imag(self.hs)

	@property
	def angles(self):
		"""Returns a sequence of angles (read-only property)."""
		return np.angle(self.hs)

	def scale(self, factor):
		"""Multiplies all elements by the given factor.

		factor: what to multiply the magnitude by (could be complex)
		"""
		self.hs *= factor

	def low_pass(self, cutoff, factor=0):
		"""Attenuate frequencies above the cutoff.

		cutoff: frequency in Hz
		factor: what to multiply the magnitude by
		"""
		self.hs[abs(self.fs) > cutoff] *= factor

	def high_pass(self, cutoff, factor=0):
		"""Attenuate frequencies below the cutoff.

		cutoff: frequency in Hz
		factor: what to multiply the magnitude by
		"""
		self.hs[abs(self.fs) < cutoff] *= factor

	def band_stop(self, low_cutoff, high_cutoff, factor=0):
		"""Attenuate frequencies between the cutoffs.

		low_cutoff: frequency in Hz
		high_cutoff: frequency in Hz
		factor: what to multiply the magnitude by
		"""
		fs = abs(self.fs)
		indices = (low_cutoff < fs) & (fs < high_cutoff)
		self.hs[indices] *= factor

	def render_full(self, high=None):
		"""Extracts amps and fs from a full spectrum.

		high: cutoff frequency

		returns: fs, amps
		"""
		hs = np.fft.fftshift(self.hs)
		amps = np.abs(hs)
		fs = np.fft.fftshift(self.fs)
		i = 0 if high is None else find_index(-high, fs)
		j = None if high is None else find_index(high, fs) + 1
		return fs[i:j], amps[i:j]

	def plot(self, low=None, high=None, **options):
		"""Plots amplitude vs frequency.

		Note: if this is a full spectrum, it ignores low and high

		low:  ignore data below this frequency
		high: ignore data above this frequency
		"""
		import matplotlib.pyplot as plt

		if self.full:
			fs, amps = self.render_full(high)
			plt.plot(fs, amps, **options)
		else:
			i = None if low  is None else find_index(low,  self.fs)
			j = None if high is None else find_index(high, self.fs)
			plt.plot(self.fs[i:j], self.amps[i:j], **options)

	def plot_power(self, high=None, **options):
		"""Plots power vs frequency.

		high: frequency to cut off at
		"""
		import matplotlib.pyplot as plt

		if self.full:
			fs, amps = self.render_full(high)
			plt.plot(fs, amps**2, **options)
		else:
			i = None if high is None else find_index(high, self.fs)
			plt.plot(self.fs[:i], self.power[:i], **options)

	def peaks(self):
		"""Finds the highest peaks and their frequencies.

		returns: sorted list of (amplitude, frequency) pairs
		"""
		t = list(zip(self.amps, self.fs))
		t.sort(reverse=True)
		return t


class Wave():
	# This class was mostly copied from Allen Downey's "Think DSP" code:
	# https://github.com/AllenDowney/ThinkDSP
	# This class is for discrete-time waveforms.

	def __init__(self, ys=None, ts=None, framerate=None):
		'''
		Initialize the wave.

		ys -- Wave array
		ts -- Array of times
		framerate -- Samples per second (samplerate)
		'''
		if (ys is None):
			ys = np.array([], dtype=np.float32)

		self.ys = np.asanyarray(ys)
		self.framerate = framerate if framerate is not None else 11025

		if ts is None:
			self.ts = np.arange(len(ys)) / self.framerate
		else:
			self.ts = np.asanyarray(ts)

	@property
	def start(self):
		'''Start time (not index).  Property.'''
		return self.ts[0]

	@property
	def end(self):
		'''End time (not index).  Property.'''
		return self.ts[-1]

	@property
	def duration(self):
		'''Duration (property). returns: float duration in seconds'''
		return len(self.ys) / self.framerate

	@property
	def samplerate(self):
		'''samplerate <--> framerate.  (property)'''
		return self.framerate

	def __len__(self):
		return len(self.ys)

	def __add__(self, other):
		"""Adds two waves elementwise.

		other: Wave

		returns: new Wave
		"""
		if other == 0:
			return self

		assert self.framerate == other.framerate

		# make an array of times that covers both waves
		start = min(self.start, other.start)
		end = max(self.end, other.end)
		n = int(round((end - start) * self.framerate)) + 1
		ys = np.zeros(n)
		ts = start + np.arange(n) / self.framerate

		def add_ys(wave):
			i = find_index(wave.start, ts)

			# make sure the arrays line up reasonably well
			diff = ts[i] - wave.start
			dt = 1 / wave.framerate
			if (diff / dt) > 0.1:
				warnings.warn(
					"Can't add these waveforms; their time arrays don't line up."
				)

			j = i + len(wave)
			ys[i:j] += wave.ys

		add_ys(self)
		add_ys(other)

		return Wave(ys, ts, self.framerate)

	__radd__ = __add__

	def __or__(self, other):
		"""Concatenates two waves.

		other: Wave

		returns: new Wave
		"""
		if self.framerate != other.framerate:
			raise ValueError("Wave.__or__: framerates do not agree")

		ys = np.concatenate((self.ys, other.ys))
		return Wave(ys, framerate=self.framerate)

	def __mul__(self, other):
		"""Multiplies two waves elementwise.

		Note: this operation ignores the timestamps; the result
		has the timestamps of self.

		other: Wave

		returns: new Wave
		"""
		# the spectrums have to have the same framerate and duration
		assert self.framerate == other.framerate
		assert len(self) == len(other)

		ys = self.ys * other.ys
		return Wave(ys, self.ts, self.framerate)

	def plot(self, **options):
		"""Plots the wave.

		If the ys are complex, plots the real part.
		"""
		import matplotlib.pyplot as plt

		plt.plot(self.ts, np.real(self.ys), **options)
		decorate(**options)

	def plot_vlines(self, **options):
		"""Plots the wave with vertical lines for samples."""
		import matplotlib.pyplot as plt

		plt.vlines(self.ts, 0, self.ys, **options)

	def hamming(self):
		"""Apply a Hamming window to the wave."""
		self.ys *= np.hamming(len(self.ys))

	def window(self, window):
		"""Apply a window to the wave.

		window: sequence of multipliers, same length as self.ys
		"""
		self.ys *= window

	def normalize(self, amp=1.0):
		"""Normalizes the signal to the given amplitude.

		amp: float amplitude
		"""
		self.ys = normalize(self.ys, amp=amp)

	def truncate(self, n):
		"""Trims this wave to the given length.

		n: integer index
		"""
		self.ys = truncate(self.ys, n)
		self.ts = truncate(self.ts, n)

	def zero_pad(self, n):
		"""Extends this wave with zeros to the given length.

		n: integer index
		"""
		self.ys = zero_pad(self.ys, n)
		self.ts = self.start + np.arange(n) / self.framerate

	def unbias(self):
		"""Unbiases the signal."""
		self.ys = unbias(self.ys)

	def find_index(self, t):
		"""Find the index corresponding to a given time."""
		n = len(self)
		start = self.start
		end = self.end
		i = round((n - 1) * (t - start) / (end - start))
		return int(i)

	def save(self, filepath='.', filename=None):
		if (filename is not None):
			saveAudio(filepath=filepath, filename=filename, data=self.ys, samplerate=self.framerate)
		else:
			print('No filename given.  Not saving wave object.')

	def segment(self, start=None, duration=None):
		"""Extracts a segment.

		start: float start time in seconds
		duration: float duration in seconds

		returns: Wave
		"""
		if start is None:
			start = self.ts[0]
			i = 0
		else:
			i = self.find_index(start)

		j = None if duration is None else self.find_index(start + duration)
		return self.slice(i, j)

	def slice(self, i, j):
		"""Makes a slice from a Wave.

		i: first slice index
		j: second slice index
		"""
		ys = self.ys[i:j].copy()
		ts = self.ts[i:j].copy()
		return Wave(ys, ts, self.framerate)

	def make_audio(self):
		"""Makes an IPython Audio object."""
		try:
			from IPython.display import Audio

			audio = Audio(data=self.ys.real, rate=self.framerate)
			return audio
		except Exception as e:
			print(f'Error in make_audio: {e}')

	def make_spectrogram(self, seg_length, win_flag=True):
		"""Computes the spectrogram of the wave.

		seg_length: number of samples in each segment
		win_flag: boolean, whether to apply hamming window to each segment

		returns: Spectrogram
		"""
		if win_flag:
			window = np.hamming(seg_length)
		i, j = 0, seg_length
		step = int(seg_length // 2)

		# map from time to Spectrum
		spec_map = {}

		while j < len(self.ys):
			segment = self.slice(i, j)
			if win_flag:
				segment.window(window)

			# the nominal time for this segment is the midpoint
			t = (segment.start + segment.end) / 2
			spec_map[t] = segment.make_spectrum()

			i += step
			j += step

		return Spectrogram(spec_map, seg_length)

	def make_spectrum(self, full=False):
		"""Computes the spectrum using FFT.

		full: boolean, whethere to compute a full FFT
			  (as opposed to a real FFT)

		returns: Spectrum
		"""
		(hs, fs) = spectrum(self.ys, self.framerate, full)

		return Spectrum(hs, fs, self.framerate, full)
