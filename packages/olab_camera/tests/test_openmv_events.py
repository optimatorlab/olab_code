import threading
import time

import numpy as np
import pytest

from olab_camera.openmv_events import (
	DropOldestQueue, EVT20Decoder, EventDecodeError, EventDispatcher,
	EventRecorder, replay_events,
)
from olab_camera.openmv_profiles import PROFILES
from olab_camera.openmv_profiles.genx_raw_events import GenxRawEventsConfig


def _word(kind, timestamp=0, x=0, y=0):
	return ((kind << 28) | (timestamp << 22) | (x << 11) | y).to_bytes(4, 'little')


def test_evt20_decodes_time_high_and_polarity():
	decoder = EVT20Decoder()
	payload = ((0x8 << 28) | (7 << 6)).to_bytes(4, 'little') + _word(1, 5, 12, 9) + _word(0, 6, 4, 3)
	batch = decoder.decode(payload, sequence=2, host_receipt_time=1.0)
	assert batch.sequence == 2
	assert batch.timestamps_us.tolist() == [(7 << 12) | 5, (7 << 12) | 6]
	assert batch.x.tolist() == [12, 4]
	assert batch.y.tolist() == [9, 3]
	assert batch.polarity.tolist() == [1, 0]


def test_evt20_rejects_truncated_payload():
	with pytest.raises(EventDecodeError):
		EVT20Decoder().decode(b'bad', sequence=1)


def test_raw_profile_is_registered_and_validates_event_buffer():
	assert PROFILES['genx_raw_events'].profile_id == 'genx_raw_events'
	assert "GENX320_MODE_EVENT" in PROFILES['genx_raw_events']().render_script()
	with pytest.raises(ValueError):
		GenxRawEventsConfig(event_buffer_size=1234)


def test_drop_oldest_queue_counts_loss():
	q = DropOldestQueue(1)
	q.put('old')
	q.put('new')
	assert q.get() == 'new'
	assert q.dropped == 1


def test_dispatcher_isolates_raising_callback_and_recorder_replays(tmp_path):
	batch = EVT20Decoder().decode(_word(1, 2, 4, 3), sequence=1, host_receipt_time=1.0)
	seen = []
	def bad_callback(_batch):
		raise RuntimeError('expected')
	def good_callback(received):
		seen.append(received.sequence)
	recorder = EventRecorder(tmp_path, {'test': True})
	dispatcher = EventDispatcher([bad_callback, good_callback], recorder=recorder, queue_size=1)
	dispatcher.start()
	dispatcher.submit(batch)
	deadline = time.monotonic() + 1
	while not seen and time.monotonic() < deadline:
		time.sleep(0.01)
	assert seen == [1]
	assert dispatcher.callback_errors == 1
	assert dispatcher.stop()
	replayed = list(replay_events(tmp_path))
	assert len(replayed) == 1
	assert np.array_equal(replayed[0].x, batch.x)
