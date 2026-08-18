"""Internal GENX320 EVT2.0 decoding, preview, and bounded event delivery."""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


class EventDecodeError(ValueError):
	"""Raised when an EVT2.0 payload cannot be decoded."""


@dataclass(frozen=True)
class EventBatch:
	"""One decoded raw-event transport payload.

	Arrays are parallel one-dimensional arrays. `payload` preserves the exact
	wire bytes so exploratory users are not limited by this first decoder.
	"""

	sequence: int
	timestamps_us: np.ndarray
	x: np.ndarray
	y: np.ndarray
	polarity: np.ndarray
	payload: bytes
	host_receipt_time: float
	format: str = 'EVT20'

	@property
	def count(self):
		return len(self.x)

	@property
	def sensor_time_range_us(self):
		if not self.count:
			return None
		return int(self.timestamps_us[0]), int(self.timestamps_us[-1])


class EVT20Decoder:
	"""Stateful decoder for OpenMV's documented default EVT2.0 stream."""

	def __init__(self, width=320, height=320):
		self.width = width
		self.height = height
		self._time_high = 0

	def decode(self, payload, sequence, host_receipt_time=None):
		if not isinstance(payload, (bytes, bytearray, memoryview)):
			raise EventDecodeError('EVT2.0 payload must be bytes-like')
		payload = bytes(payload)
		if len(payload) % 4:
			raise EventDecodeError('EVT2.0 payload length must be a multiple of four')
		xs, ys, pols, timestamps = [], [], [], []
		for word in np.frombuffer(payload, dtype='<u4'):
			word = int(word)
			kind = word >> 28
			if kind == 0x8:
				self._time_high = (word & 0x0FFFFFFF) << 6
			elif kind in (0x0, 0x1):
				x = (word >> 11) & 0x7FF
				y = word & 0x7FF
				if x < self.width and y < self.height:
					xs.append(x)
					ys.append(y)
					pols.append(kind)
					timestamps.append(self._time_high | ((word >> 22) & 0x3F))
		return EventBatch(
			sequence=sequence,
			timestamps_us=np.asarray(timestamps, dtype=np.uint64),
			x=np.asarray(xs, dtype=np.uint16), y=np.asarray(ys, dtype=np.uint16),
			polarity=np.asarray(pols, dtype=np.uint8), payload=payload,
			host_receipt_time=time.monotonic() if host_receipt_time is None else host_receipt_time,
		)


class DropOldestQueue:
	"""A non-blocking bounded queue that accounts for discarded work."""

	def __init__(self, maxsize):
		if isinstance(maxsize, bool) or not isinstance(maxsize, int) or maxsize < 1:
			raise ValueError('maxsize must be a positive integer')
		self._queue = queue.Queue(maxsize=maxsize)
		self.dropped = 0

	def put(self, item):
		try:
			self._queue.put_nowait(item)
			return
		except queue.Full:
			pass
		try:
			self._queue.get_nowait()
			self.dropped += 1
		except queue.Empty:
			pass
		try:
			self._queue.put_nowait(item)
		except queue.Full:
			self.dropped += 1

	def get(self, timeout=0.1):
		return self._queue.get(timeout=timeout)

	def empty(self):
		return self._queue.empty()


class EventPreview:
	"""Decaying semantic ON/OFF rasterizer for raw event batches."""

	def __init__(self, shape=(320, 320), decay=0.85):
		self.height, self.width = shape
		self.decay = decay
		self._on = np.zeros(shape, dtype=np.float32)
		self._off = np.zeros(shape, dtype=np.float32)

	def render(self, batch):
		self._on *= self.decay
		self._off *= self.decay
		if batch.count:
			on = batch.polarity.astype(bool)
			np.add.at(self._on, (batch.y[on], batch.x[on]), 48)
			np.add.at(self._off, (batch.y[~on], batch.x[~on]), 48)
		frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
		frame[:, :, 1] = np.clip(self._on, 0, 255).astype(np.uint8)  # ON: green
		frame[:, :, 2] = np.clip(self._off, 0, 255).astype(np.uint8) # OFF: red
		return frame


class EventRecorder:
	"""Append-only, small native event-session recorder."""

	def __init__(self, outputDir, metadata=None):
		self.output_dir = Path(outputDir)
		self.output_dir.mkdir(parents=True, exist_ok=True)
		self._file = (self.output_dir / 'events.jsonl').open('ab')
		manifest = {'schema_version': 1, 'format': 'EVT20', 'metadata': metadata or {}}
		(self.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

	def write(self, batch):
		record = {
			'sequence': batch.sequence, 'host_receipt_time': batch.host_receipt_time,
			'timestamps_us': batch.timestamps_us.tolist(), 'x': batch.x.tolist(),
			'y': batch.y.tolist(), 'polarity': batch.polarity.tolist(),
			'payload_hex': batch.payload.hex(),
		}
		self._file.write(json.dumps(record).encode('utf-8') + b'\n')
		self._file.flush()

	def close(self):
		self._file.close()


def replay_events(outputDir):
	"""Yield recorded `EventBatch` instances from an `EventRecorder` session."""
	with (Path(outputDir) / 'events.jsonl').open('rb') as f:
		for line in f:
			r = json.loads(line)
			yield EventBatch(
				sequence=r['sequence'], timestamps_us=np.asarray(r['timestamps_us'], dtype=np.uint64),
				x=np.asarray(r['x'], dtype=np.uint16), y=np.asarray(r['y'], dtype=np.uint16),
				polarity=np.asarray(r['polarity'], dtype=np.uint8), payload=bytes.fromhex(r['payload_hex']),
				host_receipt_time=r['host_receipt_time'])


class EventDispatcher:
	"""Runs callbacks/recording away from acquisition; safe to stop boundedly."""

	def __init__(self, callbacks, recorder=None, queue_size=8, on_error=None):
		self.queue = DropOldestQueue(queue_size)
		self.callbacks = list(callbacks)
		self.recorder = recorder
		self.on_error = on_error or (lambda _kind, _exc: None)
		self.callback_errors = 0
		self.recorder_errors = 0
		self._stopping = threading.Event()
		self.thread = threading.Thread(target=self._run, daemon=True)

	def start(self):
		self.thread.start()

	def submit(self, batch):
		self.queue.put(batch)

	def _run(self):
		while not self._stopping.is_set() or not self.queue.empty():
			try:
				batch = self.queue.get()
			except queue.Empty:
				continue
			for callback in self.callbacks:
				try:
					callback(batch)
				except Exception as exc:
					self.callback_errors += 1
					self.on_error('callback', exc)
			if self.recorder is not None:
				try:
					self.recorder.write(batch)
				except Exception as exc:
					self.recorder_errors += 1
					self.on_error('recorder', exc)
					try:
						self.recorder.close()
					except Exception:
						pass
					self.recorder = None
		if self.recorder is not None:
			self.recorder.close()

	def stop(self, timeout=1.0):
		self._stopping.set()
		self.thread.join(timeout=timeout)
		return not self.thread.is_alive()


class EventPreviewWorker:
	"""Renders batches outside acquisition and publishes newest preview frames."""

	def __init__(self, publish_frame, queue_size=8, preview_rate_hz=30):
		self.queue = DropOldestQueue(queue_size)
		self.publish_frame = publish_frame
		self.interval = 1.0 / preview_rate_hz
		self.rasterizer = EventPreview()
		self._stopping = threading.Event()
		self.thread = threading.Thread(target=self._run, daemon=True)

	def start(self):
		self.thread.start()

	def submit(self, batch):
		self.queue.put(batch)

	def _run(self):
		last_render = 0.0
		while not self._stopping.is_set() or not self.queue.empty():
			try:
				batch = self.queue.get()
			except queue.Empty:
				continue
			now = time.monotonic()
			if now - last_render >= self.interval:
				self.publish_frame(self.rasterizer.render(batch))
				last_render = now

	def stop(self, timeout=1.0):
		self._stopping.set()
		self.thread.join(timeout=timeout)
		return not self.thread.is_alive()


class EventRecorderWorker:
	"""Dedicated bounded recorder delivery; independent of callback speed."""

	def __init__(self, recorder, queue_size=8, on_error=None):
		self.queue = DropOldestQueue(queue_size)
		self.recorder = recorder
		self.on_error = on_error or (lambda _exc: None)
		self.errors = 0
		self._stopping = threading.Event()
		self.thread = threading.Thread(target=self._run, daemon=True)

	def start(self):
		self.thread.start()

	def submit(self, batch):
		self.queue.put(batch)

	def _run(self):
		while not self._stopping.is_set() or not self.queue.empty():
			try:
				batch = self.queue.get()
			except queue.Empty:
				continue
			try:
				self.recorder.write(batch)
			except Exception as exc:
				self.errors += 1
				self.on_error(exc)
				break
		self.recorder.close()

	def stop(self, timeout=1.0):
		self._stopping.set()
		self.thread.join(timeout=timeout)
		return not self.thread.is_alive()
