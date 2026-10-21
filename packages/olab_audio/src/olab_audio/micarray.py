"""MicArray: hardware-agnostic multichannel capture with an aligned recorder.

One N-channel int32 PortAudio stream at an explicit sample rate; per-label
levels/peaks/spectrum/subscribers; and a recorder that writes one mono 32-bit
WAV per label plus a JSON manifest. See the lifecycle/locking notes on the
class. Labels and per-channel `meta` are opaque passthrough: nothing here
knows what hardware or microphones are attached.

Lock rules (see MicArray docstring): user callbacks (excFunc, postFunc,
session postFunc, subscribers) are never invoked while a MicArray lock is held;
the PortAudio callback never takes `_life_lock`.
"""

import copy
import json
import math
import os
import queue
import re
import threading
import time
import wave
from functools import partial

import numpy as np
import pyaudio

from ._constants import ONE_OVER_MAX_INT32
from ._util import _DEFAULT_FMAX, _DEFAULT_FMIN, _DEFAULT_N_BINS, convert_to_db, spectrum_db
from .device import audio, get_input_device_info
from .mic import _PIPEWIRE_PROPS_LOCK, _temporary_pipewire_mic_identity

# A single estimated overflow fill larger than this is treated as a suspend /
# clock step, not an overflow: filling it could write gigabytes of zeros.
_MAX_GAP_FILL_SECONDS = 10.0
# An UNFLAGGED ADC-time gap larger than this fraction of a buffer is logged.
_UNFLAGGED_GAP_FRACTION = 0.5
# start() waits at most this long for the opened stream to report active.
_ACTIVE_WAIT_SECONDS = 0.5
_LABEL_RE = re.compile(r'^[A-Za-z0-9._-]+$')
_GAP_NOTE = (
	"est_lost_frames with method 'adc_time' are ESTIMATES (tolerance about +/-1 ms, ~50 frames, per gap, validated only for isolated overflows on a healthy stream; "
	"they can be badly wrong when overflows are frequent: ~2.4x too high was observed on an overflowing stream); "
	"'queue_drop' counts are EXACT; 'unflagged_gap' and 'flag_only' entries are NOT filled "
	"('flag_only': PortAudio flagged an overflow but the ADC-time estimator was unavailable or implausible, "
	"est_lost_frames is null). at_frame is the index in the output files where inserted zeros begin "
	"(for unflagged_gap/flag_only, where the discontinuity lies)."
)


def _passFunction(*args, **kwargs):
	'''a dummy function that does nothing'''
	pass


def _exceptionFunction(msg, *args, **kwargs):
	'''The default function to call when an exception is raised.'''
	print(msg)


def _report(excFunc, msg):
	# excFunc is caller-supplied and must never break the PortAudio callback
	# or a fan-out loop: if it raises, fall back to print().
	try:
		excFunc(msg=msg)
	except Exception as e:
		print(f'ERROR: excFunc itself raised while reporting {msg!r}: {e}')


def _is_int(x):
	return isinstance(x, int) and not isinstance(x, bool)


def _valid_adc(x):
	return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


class _ZeroRun:
	'''Tiny queue marker: the writer thread expands it into n zero frames.'''
	__slots__ = ('n',)

	def __init__(self, n):
		self.n = n


_END = object()


class _Session:
	'''One take. A fresh object (queue, writer thread, files) per recordStart.'''

	def __init__(self, labels, cols, dirpath, basename, paths, manifest_path, sr, fpb,
				 max_queue, limit_frames, postFunc, anchor):
		self.labels = labels
		self.cols = cols
		self.dir = dirpath
		self.basename = basename
		self.paths = paths
		self.manifest_path = manifest_path
		self.samplerate = sr
		self.frames_per_buffer = fpb
		self.queue = queue.Queue(maxsize=max_queue)
		self.limit_frames = limit_frames
		self.postFunc = postFunc
		self.anchor = anchor
		self.files = {}
		self.wavs = {}
		self.created = []
		self.writer_thread = None
		# callback-owned (guarded by MicArray._rec_lock)
		self.began = False
		self.start_frame_index = None
		self.first_adc = None
		self.last_adc = None
		self.n_buffers = 0
		self.pos = 0
		self.pos_before_last_data = 0
		self.pending_zeros = 0
		self.tail_zeros = 0
		self.gaps = []
		self.warned_flag_only = False
		self.ended = False
		# writer-owned
		self.frames_written = {label: 0 for label in labels}
		self.writer_error = None
		# finalize-owned
		self.finalize_lock = threading.Lock()
		self.take = None


class MicArray():
	'''
	Multichannel microphone array on ONE PortAudio stream (so channels can never
	misalign), with an aligned recorder.

	channels - ordered list of {'label': 'B1', 'channel': 1, 'meta': {...}}.
	           `channel` is the 1-based hardware jack number; the stream opens
	           max(channel) channels. `meta` is deep-copied and passed through
	           untouched into the manifest.
	deviceName / deviceID - substring match resolved at start() (PortAudio
	           indexes are unstable) / explicit GLOBAL PortAudio index (wins).
	samplerate - ALWAYS used explicitly, never the device default.
	frames_per_buffer - large by default (8192): latency is irrelevant for
	           recording and small buffers overflowed periodically in testing.
	open_channels - optional override of the stream width. By default start()
	           opens the device's FULL maxInputChannels (real hardware: a partial
	           14-of-18 open overflowed about every 1.4 s, the full open was clean);
	           columns for the configured jacks are selected from that width.
	max_queue_buffers - bound on the writer queue (buffers); on overflow the
	           buffer is dropped and replaced by an exact-length zero run.

	Threads / locks:
	  _life_lock  - start()/stop() only, only around stream open/close. Never
	                taken by the PortAudio callback, recordStart, recordStop,
	                subscribe or finalizers (stream.close() waits for the
	                callback). start()/stop() are refused on the callback thread.
	  _rec_lock   - callback record section, recordStart registration,
	                recordStop, stop(). Non-blocking work only.
	  _levels_lock, _subscribers_lock - leaf locks.
	  session.finalize_lock - `_finalize` work only.
	User callbacks never run under any of these.

	Liveness: `lastCallbackTime` is time.monotonic() at the latest callback of the
	current stream (None until the first one) so a caller can detect a stalled
	stream. `db`/`peak_db` values are None until the first buffer; peak_db >= db
	always (reset_peaks() restarts the peak at the current level).

	Gap estimates ('adc_time') can be badly wrong when overflows are frequent
	(observed ~2.4x too high on an overflowing 14-channel stream); they were only
	validated for isolated overflows on a healthy stream.

	Take lifecycle: ARMED (registered, awaiting first buffer) -> RECORDING ->
	ENDED -> FINALIZED. A take starts at the first buffer processed after
	recordStart and ends on a buffer boundary (recordStop, timeLimitSec, writer
	error, stop()); all labels share both boundaries. recordStop() blocks until
	files and manifest are final; call it from a non-callback thread when you
	can (from a subscriber it blocks the audio callback while the writer
	drains). The take is also delivered through recordStart's postFunc(take=...)
	and `.lastTake`. If stop() races a time-limit finalizer, stop's own postFunc
	may fire before that take's postFunc.
	'''

	def __init__(self, channels, deviceName=None, deviceID=None, samplerate=48000,
				 frames_per_buffer=8192, frmt=pyaudio.paInt32, excFunc=_exceptionFunction,
				 postFunc=_passFunction, max_queue_buffers=128, open_channels=None):
		if frmt != pyaudio.paInt32:
			raise ValueError('MicArray only supports frmt=pyaudio.paInt32')
		for name, val in (('samplerate', samplerate), ('frames_per_buffer', frames_per_buffer),
						  ('max_queue_buffers', max_queue_buffers)):
			if not _is_int(val) or val < 1:
				raise ValueError(f'{name} must be a positive int, got {val!r}')
		if deviceID is not None and not _is_int(deviceID):
			raise ValueError(f'deviceID must be an int or None, got {deviceID!r}')
		if not isinstance(channels, (list, tuple)) or not channels:
			raise ValueError('channels must be a non-empty list of {label, channel, meta} dicts')

		self._labels = []
		self._jacks = {}
		self._meta = {}
		for spec in channels:
			if not isinstance(spec, dict):
				raise ValueError(f'channel spec must be a dict, got {spec!r}')
			label, jack = spec.get('label'), spec.get('channel')
			if not isinstance(label, str) or not _LABEL_RE.match(label):
				raise ValueError(f'label must match [A-Za-z0-9._-]+ (it becomes a filename), got {label!r}')
			if label in self._jacks:
				raise ValueError(f'duplicate label {label!r}')
			if not _is_int(jack) or jack < 1:
				raise ValueError(f'channel for {label!r} must be an int >= 1 (1-based jack), got {jack!r}')
			if jack in self._jacks.values():
				raise ValueError(f'duplicate channel {jack} (label {label!r})')
			meta = copy.deepcopy(spec.get('meta', {}))
			try:
				json.dumps(meta, allow_nan=False)
			except (TypeError, ValueError) as e:
				raise ValueError(f'meta for {label!r} is not JSON-serialisable: {e}')
			self._labels.append(label)
			self._jacks[label] = jack
			self._meta[label] = meta
		self._max_jack = max(self._jacks.values())
		if open_channels is not None and (not _is_int(open_channels) or open_channels < self._max_jack):
			raise ValueError(f'open_channels must be an int >= the highest configured jack ({self._max_jack}), got {open_channels!r}')
		self.open_channels = open_channels
		self._width = None    # channels actually opened; set by start()
		self._col = {label: self._jacks[label] - 1 for label in self._labels}
		self._all_cols = [self._col[label] for label in self._labels]

		self.deviceName = deviceName
		self.deviceID = deviceID
		self.samplerate = samplerate
		self.frames_per_buffer = frames_per_buffer
		self.frmt = frmt
		self.excFunc = excFunc
		self.postFunc = postFunc
		self.max_queue_buffers = max_queue_buffers
		self.lastCallbackTime = None

		self.micOn = False
		self.lastTake = None

		self._life_lock = threading.Lock()
		self._rec_lock = threading.Lock()
		self._levels_lock = threading.Lock()
		self._subscribers_lock = threading.Lock()
		self._subscribers = []   # [(callback, labels|None, cols|None)]

		self._stream = None
		self._gen = 0
		self._anchor = None
		self._accepting = False
		self._active = None
		self._unfinalized = []
		self._cb_thread_ident = None

		# callback-private (reset in start() before the stream exists)
		self._stream_frames = 0
		self._prev_adc = None
		self._prev_frames = 0

		self._db = {label: None for label in self._labels}        # None until the first buffer
		self._peak_db = {label: None for label in self._labels}
		self._latest = None

	# ------------------------------------------------------------------ state

	@property
	def labels(self):
		return list(self._labels)

	@property
	def isRecording(self):
		return self._active is not None

	@property
	def db(self):
		with self._levels_lock:
			return dict(self._db)

	@property
	def peak_db(self):
		'''Peak-hold dBFS per label since start()/reset_peaks(). Clip indicator: >= -0.1.'''
		with self._levels_lock:
			return dict(self._peak_db)

	def reset_peaks(self):
		with self._levels_lock:
			# peak-hold restarts AT the current level, so peak_db >= db always holds
			self._peak_db = dict(self._db)

	def spectrum(self, label, n_bins=_DEFAULT_N_BINS, fmin=_DEFAULT_FMIN, fmax=_DEFAULT_FMAX):
		'''Log-binned dB spectrum of one label's latest buffer (see _util.spectrum_db).'''
		if label not in self._col:
			raise ValueError(f'unknown label {label!r}')
		with self._levels_lock:
			latest = self._latest
		if latest is None:
			data = np.array([], dtype=np.float32)
		else:
			data = np.ascontiguousarray(latest[:, self._labels.index(label)])
		return spectrum_db(data, self.samplerate, n_bins, fmin, fmax)

	def _on_callback_thread(self):
		return self._cb_thread_ident == threading.get_ident()

	# ------------------------------------------------------------ subscribers

	def subscribe(self, callback, labels=None):
		'''
		Register callback(data=float32 (frames, nSel), labels=[...]) fired on every
		captured buffer. `labels=None` = all labels in configured order. Idempotent
		per callable. `data` may be shared between subscribers: treat it read-only.
		'''
		cols = None
		if labels is not None:
			if not isinstance(labels, (list, tuple)) or not labels:
				raise ValueError('labels must be a non-empty list/tuple of labels or None')
			unknown = [l for l in labels if l not in self._col]
			if unknown:
				raise ValueError(f'unknown labels: {unknown}')
			labels = list(labels)
			cols = [self._labels.index(l) for l in labels]
		with self._subscribers_lock:
			if all(entry[0] != callback for entry in self._subscribers):
				self._subscribers.append((callback, labels, cols))

	def unsubscribe(self, callback):
		with self._subscribers_lock:
			self._subscribers = [e for e in self._subscribers if e[0] != callback]

	def _fire_subscribers(self, f):
		with self._subscribers_lock:
			subscribers = list(self._subscribers)
		for callback, labels, cols in subscribers:
			try:
				if labels is None:
					callback(data=f, labels=list(self._labels))
				else:
					callback(data=f[:, cols], labels=list(labels))
			except Exception as e:
				_report(self.excFunc, f'ERROR in MicArray subscriber: {e}')

	# -------------------------------------------------------- start() / stop()

	def start(self):
		'''Open the stream. Returns True on success, False otherwise (reported to excFunc).'''
		if self._on_callback_thread():
			_report(self.excFunc, 'ERROR in start: cannot be called from the audio callback thread; call it from another thread')
			return False
		msgs = []
		ok = False
		with self._life_lock:
			if self.micOn or self._stream is not None:
				msgs.append('ERROR in start: already started')
			else:
				try:
					self._open_stream()
					ok = True
				except Exception as e:
					self._stream = None
					msgs.append(f'ERROR in start: {e}')
		for m in msgs:
			_report(self.excFunc, m)
		return ok

	def _open_stream(self):
		# Called with _life_lock held; no user callback is invoked here.
		# PortAudio's ALSA host API segfaults on an out-of-range device index
		# (see Mic.start), so validate before audio.open().
		device_count = audio.get_device_count()
		if self.deviceID is not None:
			dev = self.deviceID
		elif self.deviceName is not None:
			found = get_input_device_info(self.deviceName)
			if found is None:
				raise ValueError(f'No input device matching {self.deviceName!r}')
			dev = found['deviceID']
		else:
			raise ValueError('Must provide deviceName or deviceID')
		if not _is_int(dev) or not (0 <= dev < device_count):
			raise ValueError(f'Invalid deviceID={dev!r}: must be in range [0, {device_count})')
		info = audio.get_device_info_by_index(dev)
		max_in = int(info['maxInputChannels'])
		# Open the device's FULL width (or the explicit override): on real hardware a
		# 14-channel open of an 18-channel UMC1820 overflowed every ~1.4 s while the
		# identical setup at 18 was clean. Columns are selected from this width.
		width = self.open_channels if self.open_channels is not None else max_in
		if width > max_in:
			raise ValueError(f'open_channels={width} exceeds the {max_in} input channels of device {dev}')
		if width < self._max_jack:
			raise ValueError(f'device {dev} opens {width} input channels but jack {self._max_jack} is configured')
		self._width = width
		self.lastCallbackTime = None

		self._stream_frames = 0
		self._prev_adc = None
		self._prev_frames = 0
		with self._levels_lock:
			self._db = {label: None for label in self._labels}
			self._peak_db = {label: None for label in self._labels}
			self._latest = None
		self._anchor = None
		self._gen += 1
		callback = partial(self._callback, self._gen)

		# See Mic.start(): give each open a fresh PipeWire identity.
		with _PIPEWIRE_PROPS_LOCK:
			with _temporary_pipewire_mic_identity():
				stream = audio.open(format=pyaudio.paInt32, channels=width,
									rate=self.samplerate, input=True, input_device_index=dev,
									frames_per_buffer=self.frames_per_buffer, output=False,
									stream_callback=callback)
		# NEVER call Stream.get_time() here: on real hardware (UMC1820/ALSA) it
		# raised OSError(-9986) right after opening a callback stream AND left the
		# stream dead (no callbacks ever). The wall-clock anchor comes from the
		# first callback's time_info instead (see _process).
		# A dead stream must make start() fail, not report success.
		try:
			alive = self._wait_active(stream)
			err = None if alive else 'stream is not active right after open'
		except Exception as e:
			err = f'stream is not usable right after open: {e}'
		if err is not None:
			try:
				stream.close()
			except Exception:
				pass
			raise RuntimeError(err)
		self._stream = stream
		with self._rec_lock:
			self._accepting = True
			self.micOn = True

	@staticmethod
	def _wait_active(stream):
		'''True once stream.is_active() is True (polled briefly: a started callback
		stream may take a moment); raises if is_active() itself raises.'''
		deadline = time.monotonic() + _ACTIVE_WAIT_SECONDS
		while True:
			if stream.is_active():
				return True
			if time.monotonic() >= deadline:
				return False
			time.sleep(0.01)

	def stop(self, postFunc=None):
		'''Close the stream, finalise any take in progress, then fire postFunc. Idempotent.'''
		if self._on_callback_thread():
			_report(self.excFunc, 'ERROR in stop: cannot be called from the audio callback thread; call it from another thread')
			return
		msgs = []
		with self._life_lock:
			with self._rec_lock:
				self._accepting = False
				self.micOn = False
				self._gen += 1     # any in-flight/stale callback now bails out
			stream, self._stream = self._stream, None
			if stream is not None:
				# close() is attempted even if stop_stream() raised: otherwise the
				# PortAudio stream (and the ALSA device) would stay open forever.
				try:
					stream.stop_stream()
				except Exception as e:
					msgs.append(f'Could not stop stream: {e}')
				try:
					stream.close()
				except Exception as e:
					msgs.append(f'Could not close stream: {e}')
			with self._rec_lock:
				active = self._active
				if active is not None:
					self._end_locked(active)
				to_finalize = list(self._unfinalized)
		for m in msgs:
			_report(self.excFunc, m)
		for s in to_finalize:
			self._finalize(s)
		try:
			(postFunc if postFunc is not None else self.postFunc)()
		except Exception as e:
			_report(self.excFunc, f'Error in stop postFunc: {e}')

	# --------------------------------------------------------------- callback

	def _callback(self, gen, in_data, frame_count, time_info, status):
		if gen != self._gen:
			return (None, pyaudio.paComplete)
		self._cb_thread_ident = threading.get_ident()
		self.lastCallbackTime = time.monotonic()
		try:
			self._process(in_data, frame_count, time_info, status)
		except Exception as e:
			_report(self.excFunc, f'ERROR in MicArray callback: {e}')
		finally:
			self._cb_thread_ident = None
		return (None, pyaudio.paContinue)

	def _process(self, in_data, frame_count, time_info, status):
		span = self._width
		if len(in_data) != frame_count * span * 4:
			_report(self.excFunc, f'ERROR in MicArray callback: got {len(in_data)} bytes, expected {frame_count * span * 4}')
			return
		arr = np.frombuffer(in_data, dtype='<i4').reshape(frame_count, span)

		# Levels for every label (cheap), on the float32-normalised matrix.
		f = arr[:, self._all_cols].astype(np.float32) * ONE_OVER_MAX_INT32
		dbs = {label: float(convert_to_db(f[:, k])) for k, label in enumerate(self._labels)}
		with self._levels_lock:
			self._db = dbs
			for label, v in dbs.items():
				if self._peak_db[label] is None or v > self._peak_db[label]:
					self._peak_db[label] = v
			self._latest = f

		adc = time_info.get('input_buffer_adc_time') if time_info else None
		flagged = bool(status & pyaudio.paInputOverflow)

		# Wall-clock anchor from the first callback of this stream: time_info carries
		# the PortAudio stream time 'current_time' (callback entry) and the buffer's
		# 'input_buffer_adc_time' (first sample), so wall(adc) = now - (current_time - adc)
		# = anchor_wall + (adc - anchor_current). No Stream.get_time() call.
		# Each take re-anchors at its own first buffer (a later wall-clock step must
		# not shift older anchors); the stream-level anchor is only a fallback.
		now_anchor = None
		if time_info:
			cur = time_info.get('current_time')
			if _valid_adc(cur):
				now_anchor = (time.time(), cur)
				if self._anchor is None:
					self._anchor = now_anchor

		warnings = []
		ended = None
		with self._rec_lock:
			sess = self._active
			if sess is not None and self._record_locked(sess, arr, frame_count, adc, flagged, warnings, now_anchor):
				ended = sess
		# unconditional bookkeeping (also when not recording)
		self._prev_adc = adc
		self._prev_frames = frame_count
		self._stream_frames += frame_count

		if ended is not None:
			threading.Thread(target=self._finalize, args=(ended,), daemon=True).start()
		for m in warnings:
			_report(self.excFunc, m)
		self._fire_subscribers(f)

	def _record_locked(self, s, arr, n, adc, flagged, warnings, anchor=None):
		'''Record section; _rec_lock held. Returns True if this call ended the take.'''
		if s.writer_error is not None:
			self._end_locked(s)
			return True
		if not s.began:
			s.began = True
			s.start_frame_index = self._stream_frames
			s.first_adc = adc
			s.anchor = anchor if anchor is not None else self._anchor
		else:
			self._gap_locked(s, adc, flagged, warnings)
		s.last_adc = adc
		s.n_buffers += 1
		s.pos_before_last_data = s.pos

		sel = arr[:, s.cols]   # copy, independent of PortAudio's buffer
		ok = True
		if s.pending_zeros:
			try:
				s.queue.put_nowait(_ZeroRun(s.pending_zeros))
				s.pending_zeros = 0
			except queue.Full:
				ok = False      # zeros must precede this buffer in the file
		if ok:
			try:
				s.queue.put_nowait(sel)
			except queue.Full:
				ok = False
		if ok:
			s.pos += n
		else:
			# Exact drop: the buffer's own frame count becomes a zero run.
			at = s.pos
			s.pending_zeros += n
			s.pos += n
			last = s.gaps[-1] if s.gaps else None
			if last is not None and last['method'] == 'queue_drop' and last['at_frame'] + last['est_lost_frames'] == at:
				last['est_lost_frames'] += n
			else:
				s.gaps.append({'at_frame': at, 'est_lost_frames': n, 'method': 'queue_drop'})
				warnings.append(f'WARNING MicArray: writer queue full, dropped audio at frame {at} replaced by zeros (exact count in manifest)')
		if s.limit_frames is not None and s.pos >= s.limit_frames:
			self._end_locked(s)
			return True
		return False

	def _gap_locked(self, s, adc, flagged, warnings):
		sr = self.samplerate
		prev = self._prev_adc
		prev_frames = self._prev_frames
		ok = _valid_adc(adc) and _valid_adc(prev) and adc > prev
		gap = int(round((adc - prev) * sr)) - prev_frames if ok else 0
		if flagged:
			if not ok:
				s.gaps.append({'at_frame': s.pos, 'est_lost_frames': None, 'method': 'flag_only'})
				if not s.warned_flag_only:
					s.warned_flag_only = True
					warnings.append('WARNING MicArray: overflow flagged but ADC time unusable; gap not filled (flag_only)')
				return
			est = max(0, gap)
			if est > _MAX_GAP_FILL_SECONDS * sr:
				s.gaps.append({'at_frame': s.pos, 'est_lost_frames': None, 'adc_gap_frames': est, 'method': 'flag_only'})
				warnings.append(f'WARNING MicArray: implausible ADC gap of {est} frames at overflow; not filled (flag_only)')
				return
			s.gaps.append({'at_frame': s.pos, 'est_lost_frames': est, 'method': 'adc_time'})
			s.pending_zeros += est
			s.pos += est
		elif ok and gap > _UNFLAGGED_GAP_FRACTION * prev_frames:
			s.gaps.append({'at_frame': s.pos, 'est_lost_frames': None, 'gap_frames': gap, 'method': 'unflagged_gap'})
			warnings.append(f'WARNING MicArray: unflagged ADC-time gap of {gap} frames at frame {s.pos}; not filled')

	def _end_locked(self, s):
		'''End a take on the current buffer boundary; _rec_lock held.'''
		if self._active is s:
			self._active = None
		s.tail_zeros = s.pending_zeros
		s.pending_zeros = 0
		s.ended = True
		if s not in self._unfinalized:
			self._unfinalized.append(s)

	# --------------------------------------------------------------- recording

	def recordStart(self, filepath, basename, labels=None, timeLimitSec=None, postFunc=None):
		'''
		Start recording the chosen labels (None = all) on the next buffer boundary.
		Writes `filepath/<basename>_<label>.wav` per label and
		`filepath/<basename>_manifest.json`. Returns True if armed, False otherwise
		(reported to excFunc; never raises). Existing take files are never overwritten.
		postFunc(take=take) fires once when the take is final.
		'''
		session = None
		try:
			if not isinstance(basename, str) or not basename or os.path.basename(basename) != basename or basename in ('.', '..'):
				raise ValueError(f'invalid basename {basename!r}')
			dirpath = os.fspath(filepath)
			if labels is None:
				sel = list(self._labels)
			else:
				if not isinstance(labels, (list, tuple)) or not labels:
					raise ValueError('labels must be a non-empty list/tuple or None')
				sel = list(labels)
				unknown = [l for l in sel if l not in self._col]
				if unknown:
					raise ValueError(f'unknown labels: {unknown}')
				if len(set(sel)) != len(sel):
					raise ValueError('duplicate labels')
			limit_frames = None
			if timeLimitSec is not None:
				if isinstance(timeLimitSec, bool) or not isinstance(timeLimitSec, (int, float)) \
						or not math.isfinite(timeLimitSec) or timeLimitSec <= 0:
					raise ValueError(f'timeLimitSec must be a finite number > 0, got {timeLimitSec!r}')
				limit_frames = int(math.ceil(timeLimitSec * self.samplerate))
			if not self._accepting:
				raise ValueError('not started (call start() first)')
			if self._active is not None:
				raise ValueError('already recording')

			manifest_path = os.path.join(dirpath, f'{basename}_manifest.json')
			os.makedirs(dirpath, exist_ok=True)
			if os.path.exists(manifest_path):
				raise FileExistsError(manifest_path)
			paths = {l: os.path.join(dirpath, f'{basename}_{l}.wav') for l in sel}
			session = _Session(sel, [self._col[l] for l in sel], dirpath, basename, paths, manifest_path,
							   self.samplerate, self.frames_per_buffer, self.max_queue_buffers,
							   limit_frames, postFunc if postFunc is not None else _passFunction, self._anchor)
			self._open_session_files(session)
			session.writer_thread = threading.Thread(target=self._writer_loop, args=(session,), daemon=True)
			session.writer_thread.start()

			with self._rec_lock:
				if self._accepting and self._active is None:
					self._active = session
					return True
			raise ValueError('array stopped or another take started while arming')
		except Exception as e:
			if session is not None:
				self._abort_session(session)
			_report(self.excFunc, f'ERROR in recordStart: {e}')
			return False

	def _open_session_files(self, s):
		try:
			for label in s.labels:
				f = open(s.paths[label], 'xb')   # exclusive: never truncate an existing file
				s.created.append(s.paths[label])
				s.files[label] = f
				w = wave.open(f, 'wb')
				w.setnchannels(1)
				w.setsampwidth(4)
				w.setframerate(s.samplerate)
				s.wavs[label] = w
		except Exception:
			self._close_files(s, [])
			for p in s.created:
				try:
					os.remove(p)
				except OSError:
					pass
			s.created = []
			raise

	def _abort_session(self, s):
		'''recordStart failed after files/thread were created: undo.'''
		if s.writer_thread is not None:
			self._stop_writer(s)
		self._close_files(s, [])
		for p in s.created:
			try:
				os.remove(p)
			except OSError:
				pass
		s.created = []

	def recordStop(self):
		'''
		End the take on the current buffer boundary, wait until files/manifest are
		final and return the take dict. Returns None (silently, so an excFunc that
		calls recordStop cannot recurse) if there is nothing to stop. If a time-limit
		or writer-error end raced this call, returns the most recently ended take.
		'''
		with self._rec_lock:
			s = self._active
			if s is not None:
				self._end_locked(s)
			elif self._unfinalized:
				s = self._unfinalized[-1]
		if s is None:
			return None
		return self._finalize(s)

	# ------------------------------------------------------------------ writer

	def _writer_loop(self, s):
		chunk = s.frames_per_buffer
		zeros = memoryview(bytes(4 * chunk))
		while True:
			item = s.queue.get()
			if item is _END:
				break
			if s.writer_error is not None:
				continue   # keep draining so the finalizer's put(_END) never wedges
			try:
				if isinstance(item, _ZeroRun):
					self._write_zeros(s, item.n, zeros, chunk)
				else:
					for k, label in enumerate(s.labels):
						s.wavs[label].writeframes(item[:, k].astype('<i4').tobytes())
						s.frames_written[label] += item.shape[0]
			except Exception as e:
				s.writer_error = str(e)   # reported by the finalizer, never from this thread
		if s.writer_error is None and s.tail_zeros:
			try:
				self._write_zeros(s, s.tail_zeros, zeros, chunk)
			except Exception as e:
				s.writer_error = str(e)

	def _write_zeros(self, s, n, zeros, chunk):
		while n > 0:
			c = min(n, chunk)
			for label in s.labels:
				s.wavs[label].writeframes(zeros[:4 * c])
				s.frames_written[label] += c
			n -= c

	def _stop_writer(self, s):
		t = s.writer_thread
		while t.is_alive():
			try:
				s.queue.put(_END, timeout=0.1)
				break
			except queue.Full:
				continue
		t.join()

	def _close_files(self, s, errors):
		for label, w in list(s.wavs.items()):
			try:
				w.close()
			except Exception as e:
				errors.append(f'closing {label}: {e}')
		for label, f in list(s.files.items()):
			try:
				f.close()
			except Exception as e:
				errors.append(f'closing file {label}: {e}')
		s.wavs = {}
		s.files = {}

	# ---------------------------------------------------------------- finalize

	def _finalize(self, s):
		'''
		Idempotent; returns the cached take. Never runs on the session's own writer
		thread. Work under s.finalize_lock; the error report and the take postFunc
		fire after it is released, from the winning caller only.
		'''
		if threading.current_thread() is s.writer_thread:
			return s.take
		winner = False
		with s.finalize_lock:
			if s.take is None:
				winner = True
				errors = []
				manifest = None
				try:
					self._stop_writer(s)
					if s.writer_error is not None:
						errors.append(f'writer: {s.writer_error}')
					self._close_files(s, errors)
					manifest = self._write_manifest(s, errors)
				except Exception as e:
					errors.append(f'finalize: {e}')
				finally:
					frames = min(s.frames_written.values()) if s.frames_written else 0
					s.take = {'dir': s.dir, 'files': dict(s.paths), 'manifest': manifest,
							  'frames': frames, 'gaps': copy.deepcopy(s.gaps),
							  'error': '; '.join(errors) if errors else None}
					self.lastTake = s.take
					with self._rec_lock:
						if s in self._unfinalized:
							self._unfinalized.remove(s)
		take = s.take
		if winner:
			if take['error']:
				_report(self.excFunc, f'ERROR in recording {s.basename}: {take["error"]}')
			try:
				s.postFunc(take=take)
			except Exception as e:
				_report(self.excFunc, f'ERROR in recordStart postFunc: {e}')
		return take

	def _write_manifest(self, s, errors):
		frames = {l: s.frames_written[l] for l in s.labels}
		total = min(frames.values()) if frames else 0
		started = None
		if s.began and s.anchor is not None and _valid_adc(s.first_adc):
			started = s.anchor[0] + (s.first_adc - s.anchor[1])
		measured = None
		contaminated = any(g['method'] in ('unflagged_gap', 'flag_only') for g in s.gaps)
		if s.began and s.n_buffers >= 2 and not contaminated and _valid_adc(s.first_adc) and _valid_adc(s.last_adc):
			span = s.last_adc - s.first_adc
			if span > 0 and math.isfinite(span):
				measured = s.pos_before_last_data / span
		doc = {
			'manifestVersion': 1,
			'samplerate': s.samplerate,
			'frames_per_buffer': s.frames_per_buffer,
			'startFrameIndex': s.start_frame_index if s.began else None,
			'startedAtWall': started,
			'startedAtWallNote': (None if started is not None or not s.began else
								  'unavailable: no usable current_time/input_buffer_adc_time in the callback time_info'),
			'totalFrames': total,
			'measuredFramesPerWallSecond': measured,
			'gaps': s.gaps,
			'gapNote': _GAP_NOTE,
			'labels': {l: {'channel': self._jacks[l], 'meta': self._meta[l],
						   'file': os.path.basename(s.paths[l]), 'frames': frames[l]} for l in s.labels},
			'error': '; '.join(errors) if errors else None,
		}
		tmp = s.manifest_path + '.tmp'
		try:
			with open(tmp, 'w') as fh:
				json.dump(doc, fh, indent=1, allow_nan=False)
			os.replace(tmp, s.manifest_path)
		except Exception as e:
			errors.append(f'manifest: {e}')
			try:
				os.remove(tmp)
			except OSError:
				pass
			return None
		return s.manifest_path
