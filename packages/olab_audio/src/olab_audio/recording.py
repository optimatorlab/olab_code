import time
from wave import open as open_wave

import numpy as np
import pyaudio

from ._constants import CHANNELS, FORMAT
from ._util import defaultFromNone


def append(ys, extra):
	'''
	Add waveform `extra` to the end of waveform `ys`.

	NOTE:  This assumes both waves have the same framerate.
	'''
	return np.concatenate((ys, extra))


def saveAudio(filepath='.', filename=None, data=None, samplerate=None, frmt=FORMAT, channels=CHANNELS):
	'''
	Write audio data to a .wav file using only the stdlib `wave` module --
	no `soundfile`/`analysis` extra required, so basic recording (the core
	use case) never needs the DSP/teaching dependency stack.

	filepath:  Full filepath where the audio file will be saved.  Default '.' will save file in current directory.
	filename:  Desired name of file.  Should end in `.wav`.
	data:      list of raw bytes chunks, OR a `float32` numpy array containing audio data.
	samplerate:  sample rate of the audio, in [Hz] (e.g., 44100).
	'''

	if (filename is None):
		raise Exception('saveAudio() missing filename')
	if (data is None):
		raise Exception('saveAudio() missing data')
	if (samplerate is None):
		raise Exception('saveAudio() missing samplerate')

	try:
		if (type(data) == list):
			with open_wave(f'{filepath}/{filename}', "wb") as wf:
				wf.setnchannels(channels)
				wf.setsampwidth(pyaudio.get_sample_size(frmt))
				wf.setframerate(samplerate)
				wf.writeframes(b''.join(data))

			print(f'Audio data saved as {filepath}/{filename} at {samplerate} Hz.')
		elif (type(data) == np.ndarray):
			# data is a numpy array of dtype 'float32' in [-1.0, 1.0] -- convert
			# to int16 PCM (matching Mic's own capture precision) and write
			# with the stdlib `wave` module.
			pcm16 = np.clip(data, -1.0, 1.0)
			pcm16 = (pcm16 * 32767.0).astype(np.int16)
			with open_wave(f'{filepath}/{filename}', "wb") as wf:
				wf.setnchannels(channels)
				wf.setsampwidth(2)  # int16
				wf.setframerate(samplerate)
				wf.writeframes(pcm16.tobytes())

			print(f'Audio data saved as {filepath}/{filename} at {samplerate} Hz.')
		else:
			print(f'Failed to save audio.  Unknown data type {type(data)}.')
			return
	except Exception as e:
		print(f'Error in saveAudio: {e}')


class Recording():
	def __init__(self, samplerateMic, samplerateRec, frmt, channels, timeLimitSec, filepath, filename, postFunc):
		self.samplerateMic = samplerateMic	# samplerate of the mic
		self.samplerateRec = samplerateRec	# desired samplerate of this recording
		self.frmt = frmt
		self.channels = channels
		self.timeLimitSec = timeLimitSec
		self.filepath = filepath
		self.filename = filename
		self.postFunc = postFunc

		self.startTime = None   # We haven't started recording.  Set in `append()` function.

		# Frame count (samples per channel, at samplerateRec) -- tracked
		# explicitly by each subclass's append(), rather than derived from
		# len(self.ys), because len(self.ys) means different things for
		# different subclasses: Recording_bytes.ys is a list of *chunks*
		# (not samples), and Recording_np.ys is an interleaved multi-channel
		# array where len() overcounts by a factor of `channels`. Using
		# len(self.ys) directly (the original behavior) gave a wrong
		# duration -- and therefore a wrong timeLimitSec cutoff -- for both.
		self._frame_count = 0

	@property
	def duration(self):
		# Return float duration in seconds
		return self._frame_count / self.samplerateRec

	@property
	def time_remain(self):
		# Returns allowable recording time remaining
		# If timeLimitSec is not defined, return infinity
		if (self.timeLimitSec is None):
			return float('inf')
		else:
			return self.timeLimitSec - self.duration

	def save(self, filepath=None, filename=None):
		filename = defaultFromNone(filename, self.filename)
		filepath = defaultFromNone(filepath, self.filepath)
		if (filename is not None):
			# self.channels/self.frmt must be passed through explicitly --
			# saveAudio() defaults to mono/FORMAT otherwise, which silently
			# writes a stereo (or other multi-channel) recording's
			# interleaved samples with a one-channel WAV header (doubling
			# apparent duration and corrupting playback interpretation).
			saveAudio(filepath=filepath, filename=filename, data=self.ys,
					  samplerate=self.samplerateRec, frmt=self.frmt, channels=self.channels)
		else:
			print('No filename given.  Not saving recorded audio.')

	def make_wave(self):
		'''Build an olab_audio.analysis.Wave from this recording.

		Requires the `analysis` extra (Wave is analysis-only -- it's the
		DSP/teaching toolkit's representation, not a core concept).
		'''
		try:
			from .analysis import Wave
		except ImportError as exc:
			raise RuntimeError(
				"make_wave() needs the 'analysis' extra. "
				"Install with: pip install olab-audio[analysis]"
			) from exc
		return Wave(self._wave_ys(), ts=None, framerate=self.samplerateRec)

	def _wave_ys(self):
		raise NotImplementedError


class Recording_bytes(Recording):
	def __init__(self, samplerateMic, samplerateRec, frmt=FORMAT, channels=CHANNELS, timeLimitSec=None, filepath='.', filename=None,
				 postFunc=None):

		super().__init__(samplerateMic, samplerateRec, frmt, channels, timeLimitSec, filepath, filename, postFunc)

		# Recording_bytes never resamples (unlike Recording_np) -- it saves
		# raw captured bytes as-is. Silently accepting a different
		# samplerateRec here would write those original-rate bytes into a
		# WAV file *labeled* with samplerateRec, producing audibly wrong
		# playback speed/pitch with no error at all. Reject explicitly
		# instead -- use Recording_np (with the `resample` extra) for
		# cross-rate recording.
		if (self.samplerateRec != self.samplerateMic):
			raise ValueError(
				f"Recording_bytes does not support cross-rate recording "
				f"(mic captures at {samplerateMic}Hz, recording requested "
				f"{samplerateRec}Hz) -- it would silently mislabel the "
				f"output WAV's sample rate. Use callbackType='np' "
				f"(Recording_np, with the 'resample' extra installed) instead."
			)

		self.ys = []
		self._sampwidth = pyaudio.get_sample_size(frmt)

	def append(self, extra, orig_sr=None):
		'''
		Add raw bytes to the end of `self.ys`.

		NOTE: Unlike `Recording_np`, we're not resampling -- Recording_bytes
		is the no-resample-dependency recording path (works with only
		pyaudio+pulsectl+numpy installed, no `resample` extra needed) and
		does not support cross-rate recording (rejected in __init__).
		'''
		if (self.startTime is None):
			self.startTime = time.time()

		self.ys.append(extra)
		self._frame_count += len(extra) // (self._sampwidth * self.channels)

	def _wave_ys(self):
		from ._constants import ONE_OVER_MAX_INT16
		return np.frombuffer(b''.join(self.ys), np.int16).astype(np.float32) * ONE_OVER_MAX_INT16


class Recording_np(Recording):
	def __init__(self, samplerateMic, samplerateRec, frmt=FORMAT, channels=CHANNELS, timeLimitSec=None, filepath='.', filename=None,
				 postFunc=None):

		super().__init__(samplerateMic, samplerateRec, frmt, channels, timeLimitSec, filepath, filename, postFunc)

		# Chunks accumulate in a list during capture -- append() must stay
		# O(1) amortized, not re-concatenate the entire recording-so-far on
		# every single PortAudio callback (np.concatenate() every chunk
		# makes total recording cost grow quadratically with duration, and
		# risks callback overruns/allocation pressure on a long recording,
		# exactly where callback work must stay small and predictable).
		# `ys` is a property: concatenated lazily, on first access after
		# new chunks have arrived, and cached until the next append().
		self._chunks = []
		self._cached_ys = None

		# One persistent, stateful stream resampler for the lifetime of this
		# recording -- NOT a fresh one-shot resample() call per chunk. A
		# fresh stateless conversion per PortAudio chunk would introduce
		# boundary artifacts and rounding/drift at every chunk edge; a
		# single StreamResampler carries the necessary state across calls
		# (see olab_audio._resample.StreamResampler) and must be flushed
		# exactly once, at save() time, to emit its final buffered samples.
		#
		# Fails early and clearly, right now (synchronously, in whatever
		# thread called Mic.recordStart() -- never from inside the
		# PortAudio callback thread that later calls append()) if this is
		# a cross-rate recording and the `resample` extra isn't installed.
		# The common case -- recording at the mic's own native rate -- never
		# constructs a real resampler at all (StreamResampler itself is a
		# same-rate passthrough with no soxr import in that case).
		from ._resample import StreamResampler
		self._stream_resampler = StreamResampler(samplerateMic, samplerateRec, channels=channels)
		self._flushed = False

	@property
	def ys(self):
		if self._cached_ys is None:
			if not self._chunks:
				self._cached_ys = np.array([], dtype=np.float32)
			elif len(self._chunks) == 1:
				self._cached_ys = self._chunks[0]
			else:
				self._cached_ys = np.concatenate(self._chunks)
				self._chunks = [self._cached_ys]  # collapse -- keeps re-access O(1)
		return self._cached_ys

	@ys.setter
	def ys(self, value):
		# Supports direct assignment (used by resample() below) while
		# keeping the chunk-list/cache invariant consistent.
		self._chunks = [value]
		self._cached_ys = value

	def append(self, extra, orig_sr=None):
		'''
		Add waveform `extra` to the end of this recording.

		`extra` is assumed to be captured at `orig_sr` (default: this
		recording's samplerateMic -- i.e. the mic's own native rate this
		Recording_np was constructed against; passing a different orig_sr
		per call is not supported by the persistent StreamResampler and
		will raise). Same-rate recording never touches the resampler.

		O(1) amortized -- appends to an internal chunk list rather than
		concatenating on every call (see `ys` above).
		'''
		orig_sr = defaultFromNone(orig_sr, self.samplerateMic)
		if (orig_sr != self.samplerateMic):
			raise ValueError(
				f"Recording_np.append() was constructed for samplerateMic="
				f"{self.samplerateMic}Hz but received orig_sr={orig_sr}Hz -- "
				f"its StreamResampler carries state across calls and cannot "
				f"switch input rates mid-recording."
			)

		if (self.startTime is None):
			self.startTime = time.time()

		extra = self._stream_resampler.process(extra)
		self._chunks.append(extra)
		self._cached_ys = None
		self._frame_count += len(extra) // self.channels

	def save(self, filepath=None, filename=None):
		self._flush_stream_resampler()
		super().save(filepath, filename)

	def _flush_stream_resampler(self):
		'''Emit the StreamResampler's final buffered samples (if any).
		Idempotent -- safe to call more than once (e.g. if save() is
		called twice), only actually flushes once.'''
		if self._flushed:
			return
		tail = self._stream_resampler.flush()
		if len(tail):
			self._chunks.append(tail)
			self._cached_ys = None
			self._frame_count += len(tail) // self.channels
		self._flushed = True

	def _wave_ys(self):
		self._flush_stream_resampler()
		return self.ys

	def resample(self, framerateOrig=None, framerateNew=None):
		'''
		Explicit, one-shot resample of the recording already captured
		in-memory -- distinct from append()'s automatic per-chunk
		conversion. This is analysis-adjacent post-processing (typically
		used together with make_wave()/Wave-based workflows), so it
		requires the `analysis` extra rather than just `resample`.

		framerateOrig defaults to self.samplerateRec, NOT self.samplerateMic
		-- self.ys is always already at samplerateRec by this point (any
		automatic cross-rate conversion during capture, via append(), has
		already happened). Defaulting to samplerateMic would silently
		double-resample an already-cross-rate-converted recording (treating
		16kHz data as if it were still the mic's original 32kHz, say),
		producing wrong duration/content. After this call, self.samplerateRec
		is updated to framerateNew -- the recording's own rate has changed.
		'''
		try:
			from .analysis import resample as _analysis_resample
		except ImportError as exc:
			raise RuntimeError(
				"Recording_np.resample() needs the 'analysis' extra. "
				"Install with: pip install olab-audio[analysis]"
			) from exc

		self._flush_stream_resampler()

		framerateOrig = defaultFromNone(framerateOrig, self.samplerateRec)
		framerateNew  = defaultFromNone(framerateNew, self.samplerateRec)

		self.ys = _analysis_resample(self.ys, framerateOrig, framerateNew)
		self._frame_count = len(self.ys) // self.channels
		self.samplerateRec = framerateNew
