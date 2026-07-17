"""High-quality PCM sample-rate conversion, kept separate from the `analysis`
extra so a voice/streaming consumer doesn't need to pull in the full
librosa/soundfile/matplotlib DSP-and-teaching stack just to resample captured
audio to a target rate (e.g. a real microphone's native 32kHz down to the
16kHz a streaming STT engine expects).

Backend: `soxr` (https://github.com/dofuuz/python-soxr) -- lightweight (no
heavy transitive deps, unlike librosa), high quality, and has a real
streaming API (`soxr.ResampleStream`) for chunk-by-chunk conversion with
explicit end-of-stream flushing, which a future live olab_voice adapter
needs to avoid boundary artifacts from naive stateless per-chunk conversion.

Not yet validated on target Raspberry Pi hardware -- see the plan doc's
acceptance checklist. Treat as a reasonable default choice, not a
production-proven one, until that validation happens.
"""

from __future__ import annotations

import numpy as np


def _require_soxr():
	try:
		import soxr
	except ImportError as exc:
		raise RuntimeError(
			"olab-audio needs the 'resample' extra to convert between sample "
			"rates. Install with: pip install olab-audio[resample]"
		) from exc
	return soxr


def resample(ys: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
	'''
	ys        -- waveform, np.ndarray (float32)
	orig_sr   -- sample rate (framerate) of ys
	target_sr -- desired sample rate

	Returns a new waveform array at the desired sample rate. A same-rate
	call (orig_sr == target_sr) is a true no-op -- it returns `ys`
	unchanged without importing soxr at all, so callers on the common
	native-rate-everywhere path never need the `resample` extra installed.
	'''
	if orig_sr == target_sr:
		return ys
	soxr = _require_soxr()
	return soxr.resample(ys, orig_sr, target_sr)


class StreamResampler:
	'''Stateful, chunk-by-chunk resampler for live/streaming audio.

	Wraps `soxr.ResampleStream`, which needs consistent state across calls
	to avoid boundary artifacts at chunk edges -- do not create a fresh
	converter per chunk. Call `process()` for each captured chunk, then
	`flush()` exactly once at the end of the stream to emit any samples
	soxr buffered internally (soxr's `resample_chunk(..., last=True)`).

	A same-rate StreamResampler is still valid to construct (so callers
	don't need special-case branching), but process()/flush() just pass
	data through unchanged and never import soxr.

	**Canonical representation, for both input and output: flat/interleaved
	1D** (e.g. stereo: `[L0, R0, L1, R1, ...]`), matching what
	`Mic._callback_np()` actually produces and what the rest of
	olab_audio's `Recording_np`/`Wave`/etc. already assume. `soxr` itself
	requires 2D `(frames, channels)` for anything but mono -- process()/
	flush() reshape to `(frames, channels)` immediately before calling
	soxr, and flatten the result immediately after, so callers on either
	side of this class never need to think about that shape at all.
	'''

	def __init__(self, orig_sr: int, target_sr: int, channels: int = 1, dtype='float32'):
		self.orig_sr = orig_sr
		self.target_sr = target_sr
		self.channels = channels
		self._passthrough = (orig_sr == target_sr)
		self._stream = None
		if not self._passthrough:
			soxr = _require_soxr()
			self._stream = soxr.ResampleStream(orig_sr, target_sr, channels, dtype=dtype)

	def _to_frames(self, flat_chunk: np.ndarray) -> np.ndarray:
		if self.channels == 1:
			return flat_chunk
		return flat_chunk.reshape(-1, self.channels)

	def _to_flat(self, frames: np.ndarray) -> np.ndarray:
		if self.channels == 1:
			return frames
		return frames.reshape(-1)

	def process(self, chunk: np.ndarray) -> np.ndarray:
		'''Convert one flat/interleaved chunk. Do not call after flush().'''
		if self._passthrough:
			return chunk
		return self._to_flat(self._stream.resample_chunk(self._to_frames(chunk), last=False))

	def flush(self, final_chunk: np.ndarray | None = None) -> np.ndarray:
		'''Signal end-of-stream and return any remaining buffered samples
		(flat/interleaved, like process()'s input and output).

		final_chunk, if given, is the last real chunk of audio (equivalent
		to calling process() on it, but flushed in the same call soxr
		expects). Pass an empty array if there's no final chunk of new
		data, just buffered state to flush.
		'''
		if final_chunk is None:
			final_chunk = np.array([], dtype=np.float32)
		if self._passthrough:
			return final_chunk
		return self._to_flat(self._stream.resample_chunk(self._to_frames(final_chunk), last=True))
