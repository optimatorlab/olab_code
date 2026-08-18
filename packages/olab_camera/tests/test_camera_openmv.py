"""Tests for CameraOpenMV. The real `openmv.Camera` protocol client needs
hardware to run for real, so it's entirely mocked here via the
`device_class=` seam (mirrors test_camera_realsense.py's `rs_module=`
pattern) -- these tests cover our own logic (standard-args compatibility,
frame validation/conversion, the deferred single-owner shutdown state
machine, and the two distinct start()-failure cleanup paths), not the
client itself. Kept minimal/high-signal per the project's stated
test-thoroughness preference.

Real hardware bring-up is out of scope for this round -- see
docs/plans/olab_camera_openmv_support_plan.md.
"""

import socket
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from olab_camera import CameraOpenMV
import olab_camera.camera_openmv as camera_openmv_module
from olab_camera.openmv_profiles.genx_histogram_preview import FIXED_RESOLUTION


PIXFORMAT_GRAYSCALE = 0x06060000


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # startStream(protocol='mjpeg') lazily auto-generates a self-signed cert
    # under ~/.olab_camera/ssl on first use -- redirect to a throwaway
    # tmp_path rather than touching the real home directory.
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)


@pytest.fixture(autouse=True)
def _reset_fake_device_instances():
    FakeDevice.instances.clear()
    yield
    FakeDevice.instances.clear()


def _make_frame(width=320, height=320, fmt=PIXFORMAT_GRAYSCALE, value=100):
    return {
        'width': width, 'height': height, 'format': fmt, 'depth': 1,
        'data': bytes([value]) * (width * height * 3), 'raw_size': width * height,
    }


class FakeDevice:
    """Records every call; `fail_at` injects a RuntimeError at a named stage
    (one of 'connect', 'stopScript', 'runSource', 'streaming'), and
    `block_event` (if given) makes the *first* readFrame() call block until
    the test sets it -- simulating a wedged protocol call.

    `instances` retains every constructed FakeDevice (cleared per-test via
    the autouse `_reset_fake_device_instances` fixture below) so a test can
    assert on the *actual* instance CameraOpenMV.start() constructed
    internally -- necessary to verify e.g. disconnect() was really called,
    not just that camera-level state looks torn down."""

    instances = []

    def __init__(self, port, timeout=1.0, fail_at=None, frames=None, raw_payloads=None, block_event=None, **kwargs):
        self.port = port
        self.timeout = timeout
        self.fail_at = fail_at
        # frames=None (the common case) means "just keep producing valid
        # frames" -- an explicit list is an exact, exhaustible sequence
        # (used to test specific drop-then-recover orderings).
        self._explicit_frames = frames is not None
        self._frames = list(frames) if frames is not None else []
        self._raw_payloads = list(raw_payloads) if raw_payloads is not None else []
        self._block_event = block_event
        self._blocked_once = False
        self.calls = []
        FakeDevice.instances.append(self)

    def connect(self):
        self.calls.append('connect')
        if self.fail_at == 'connect':
            raise RuntimeError('boom: connect')

    def disconnect(self):
        self.calls.append('disconnect')

    def stopScript(self):
        self.calls.append('stopScript')
        if self.fail_at == 'stopScript':
            raise RuntimeError('boom: stopScript')

    def runSource(self, source):
        self.calls.append('runSource')
        if self.fail_at == 'runSource':
            raise RuntimeError('boom: runSource')

    def streaming(self, enable, raw=False, resolution=None):
        self.calls.append(('streaming', enable))
        if self.fail_at == 'streaming':
            raise RuntimeError('boom: streaming')

    def readFrame(self):
        if self._block_event is not None and not self._blocked_once:
            self._blocked_once = True
            self._block_event.wait()
        if self._explicit_frames:
            return self._frames.pop(0) if self._frames else None
        return _make_frame()

    def readChannelStatus(self):
        return {'raw_events': bool(self._raw_payloads)}

    def channelSize(self, name):
        return len(self._raw_payloads[0]) if name == 'raw_events' and self._raw_payloads else 0

    def readChannel(self, name, size=None):
        return self._raw_payloads.pop(0) if name == 'raw_events' and self._raw_payloads else None


PARAM_DICT = {'res_rows': 320, 'res_cols': 320, 'fps_target': 30, 'outputPort': 8000}


def _make_camera(**kwargs):
    kwargs.setdefault('device_class', FakeDevice)
    kwargs.setdefault('paramDict', dict(PARAM_DICT))
    return CameraOpenMV('/dev/ttyFAKE', **kwargs)


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# ---- construction ---------------------------------------------------------

def test_device_port_must_be_non_empty_string():
    with pytest.raises(ValueError):
        CameraOpenMV(devicePort='', device_class=FakeDevice)


def test_unknown_profile_name_raises_value_error():
    with pytest.raises(ValueError):
        _make_camera(profile='not_a_real_profile')


def test_profile_kwargs_forwarded_to_config():
    cam = _make_camera(profile_kwargs={'histogram_rate_hz': 100})
    assert cam._profile.config.histogram_rate_hz == 100


# ---- basic start/stop lifecycle -------------------------------------------

def test_start_stop_basic_lifecycle():
    device_kwargs = {'device_kwargs': {'frames': [_make_frame()]}}
    cam = _make_camera(**device_kwargs)
    cam.start()
    assert _wait_until(lambda: len(cam.frameDeque) > 0)

    frame, meta = cam.getFrameAndMeta()
    assert frame.shape == (320, 320, 3)
    assert frame.dtype == np.uint8
    assert meta['sequence'] == 1
    assert meta['host_receipt_time'] is not None

    cam.stop()
    assert cam.camOn is False
    assert cam._device is None
    assert cam._capture_thread is None


# ---- standard-argument compatibility ---------------------------------------

def test_start_accepts_matching_standard_args_and_streams():
    cam = _make_camera()
    cam.start(res_rows=320, res_cols=320, framerate=50, startStream=True, port=8123,
              protocol='mjpeg', imgTopic='/x', compImgTopic='/y')

    assert _wait_until(lambda: cam._mjpegServer is not None)
    assert cam.activeProtocol == 'mjpeg'

    cam.stop()


@pytest.mark.parametrize('kwargs', [
    {'res_rows': 160}, {'res_cols': 160}, {'framerate': 99},
])
def test_start_rejects_mismatched_standard_args_before_device_io(kwargs):
    fake_frames = []
    cam = _make_camera(device_kwargs={'frames': fake_frames})
    with pytest.raises(ValueError):
        cam.start(**kwargs)
    assert cam._device is None
    assert cam._capture_thread is None


def test_change_zoom_supported():
    cam = _make_camera()
    cam.start()
    assert _wait_until(lambda: len(cam.frameDeque) > 0)
    cam.changeZoom(2.0)
    assert cam.zoomLevel == 2.0
    cam.stop()


def test_raw_profile_preview_uses_normal_frame_queue_and_callback():
	# One EVT2.0 ON event at (12, 9), followed by no further channel data.
	payload = ((1 << 28) | (5 << 22) | (12 << 11) | 9).to_bytes(4, 'little')
	seen = []
	cam = _make_camera(profile='genx_raw_events', device_kwargs={'raw_payloads': [payload]})
	cam.addEventCallback(lambda batch: seen.append(batch.count))
	cam.start()
	assert _wait_until(lambda: len(cam.frameDeque) > 0)
	assert _wait_until(lambda: seen == [1])
	frame, meta = cam.getFrameAndMeta()
	assert frame.shape == (320, 320, 3)
	assert frame[9, 12, 1] > 0
	assert meta['sequence'] == 1
	cam.stop()


def test_raw_stop_waits_for_in_progress_callback_before_rearm():
	"""A full queue and stuck callback cannot race a later camera session."""
	payload = ((1 << 28) | (5 << 22) | (12 << 11) | 9).to_bytes(4, 'little')
	callback_started = threading.Event()
	release_callback = threading.Event()

	def slow_callback(_batch):
		callback_started.set()
		release_callback.wait()

	cam = _make_camera(profile='genx_raw_events',
		profile_kwargs={'callback_queue_size': 1},
		device_kwargs={'timeout': 0.02, 'raw_payloads': [payload] * 3})
	cam.addEventCallback(slow_callback)
	cam.start()
	assert callback_started.wait(timeout=1)

	cam.stop()
	assert cam._stopping is True
	with pytest.raises(RuntimeError):
		cam.start()

	release_callback.set()
	assert _wait_until(lambda: not cam._stopping, timeout=2)
	assert cam.eventStats['callback_drops'] >= 1
	cam.start()
	cam.stop()


def test_raw_profile_can_disable_preview_without_disabling_callbacks():
	payload = ((1 << 28) | (5 << 22) | (12 << 11) | 9).to_bytes(4, 'little')
	seen = []
	cam = _make_camera(profile='genx_raw_events', profile_kwargs={'preview_enabled': False},
		device_kwargs={'raw_payloads': [payload]})
	cam.addEventCallback(lambda batch: seen.append(batch.count))
	cam.start()
	assert cam._eventPreviewWorker is None
	assert _wait_until(lambda: seen == [1])
	assert len(cam.frameDeque) == 0
	cam.stop()


def test_raw_malformed_batch_is_counted_and_later_batch_is_delivered():
	"""One corrupt payload is observable loss, not a fatal capture error."""
	good = ((1 << 28) | (5 << 22) | (12 << 11) | 9).to_bytes(4, 'little')
	seen = []
	cam = _make_camera(profile='genx_raw_events',
		device_kwargs={'raw_payloads': [b'bad', good]})
	cam.addEventCallback(lambda batch: seen.append(batch.count))
	cam.start()
	assert _wait_until(lambda: seen == [1])
	assert cam.eventStats['decode_errors'] == 1
	assert cam.camOn is True
	cam.stop()


def test_raw_recorder_failure_is_counted_once_without_stalling_acquisition(monkeypatch, tmp_path):
	"""A failed recorder is isolated from later raw capture batches."""
	class FailingRecorder:
		def __init__(self, _output_dir, _metadata):
			self.closed = False
		def write(self, _batch):
			raise OSError('disk full')
		def close(self):
			self.closed = True

	monkeypatch.setattr(camera_openmv_module, 'EventRecorder', FailingRecorder)
	payload = ((1 << 28) | (5 << 22) | (12 << 11) | 9).to_bytes(4, 'little')
	seen = []
	cam = _make_camera(profile='genx_raw_events',
		device_kwargs={'raw_payloads': [payload] * 3})
	cam.addEventCallback(lambda batch: seen.append(batch.sequence))
	cam.addEventRecorder(tmp_path / 'events')
	cam.start()
	assert _wait_until(lambda: cam.eventStats['recorder_errors'] == 1)
	assert _wait_until(lambda: len(seen) == 3)
	assert cam.eventStats['recorder_errors'] == 1
	assert cam.eventStats['batches'] == 3
	assert cam.camOn is True
	cam.stop()


# ---- capture-loop frame validation -----------------------------------------

@pytest.mark.parametrize('bad_frame', [
    _make_frame(fmt=0xDEADBEEF),               # wrong format
    _make_frame(width=160, height=160),        # wrong size
    {'width': 320, 'height': 320, 'format': PIXFORMAT_GRAYSCALE, 'depth': 1, 'data': None, 'raw_size': 0},
    {'width': 320, 'height': 320, 'format': PIXFORMAT_GRAYSCALE, 'depth': 1, 'data': b'short', 'raw_size': 5},
])
def test_capture_loop_drops_invalid_frames_without_raising(bad_frame):
    good_frame = _make_frame()
    cam = _make_camera(device_kwargs={'frames': [bad_frame, good_frame]})
    cam.start()

    # The bad frame is dropped; the good one that follows still arrives.
    assert _wait_until(lambda: len(cam.frameDeque) > 0)
    frame, meta = cam.getFrameAndMeta()
    assert frame.shape == (320, 320, 3)
    assert meta['sequence'] == 1  # only the good frame incremented the sequence

    cam.stop()
    assert cam.camOn is False


def test_no_frame_diagnostic_is_rate_limited_and_capture_recovers(monkeypatch):
    """Immediate empty stream reads warn once, then a valid frame recovers."""
    messages = []
    monkeypatch.setattr(camera_openmv_module, '_NO_FRAME_WARNING_THRESHOLD', 3)
    monkeypatch.setattr(camera_openmv_module, '_NO_FRAME_STARTUP_GRACE_SEC', 0)
    cam = _make_camera(device_kwargs={'frames': [None] * 5 + [_make_frame()]})
    real_log = cam.logger.log

    def record_log(message, **kwargs):
        messages.append(message)
        return real_log(message, **kwargs)

    cam.logger.log = record_log
    cam.start()
    assert _wait_until(lambda: len(cam.frameDeque) == 1)
    assert len([message for message in messages if 'no frames from profile' in message]) == 1
    assert cam.camOn is True

    cam.stop()
    device = FakeDevice.instances[0]
    assert device.calls.count('disconnect') == 1


def test_stop_during_immediate_no_frame_reads_is_bounded_and_cleans_up_once():
    cam = _make_camera(device_kwargs={'frames': [], 'timeout': 0.05})
    cam.start()
    assert _wait_until(lambda: cam._capture_thread is not None and cam._capture_thread.is_alive())

    started = time.monotonic()
    cam.stop()
    assert time.monotonic() - started < 1.0
    device = FakeDevice.instances[0]
    assert device.calls.count('stopScript') == 2  # startup + capture-owner cleanup
    assert device.calls.count('disconnect') == 1
    assert cam._device is None


# ---- getFrameAndMeta() race-freedom ----------------------------------------

def test_get_frame_and_meta_is_race_free_under_concurrent_writes():
    # Exercises only getFrameAndMeta()'s own atomicity guarantee against a
    # controlled writer -- deliberately not started (no live capture
    # thread), so there is exactly one writer and no risk of it and the
    # real capture loop racing over unrelated sequence numbers.
    cam = _make_camera()
    with cam.condition:
        cam.frameDeque.append(np.zeros((320, 320, 3), dtype=np.uint8))
        cam._latestFrameMeta = {'host_receipt_time': 0.0, 'host_receipt_wall_time': 0.0, 'sequence': 0}

    stop_writer = threading.Event()
    mismatches = []

    def _writer():
        seq = 0
        while not stop_writer.is_set():
            seq += 1
            value = seq % 256
            frame = np.full((320, 320, 3), value, dtype=np.uint8)
            with cam.condition:
                cam.frameDeque.append(frame)
                cam._latestFrameMeta = {
                    'host_receipt_time': time.monotonic(),
                    'host_receipt_wall_time': time.time(),
                    'sequence': seq,
                }
                cam.condition.notify_all()

    writer_thread = threading.Thread(target=_writer, daemon=True)
    writer_thread.start()
    try:
        for _ in range(500):
            frame, meta = cam.getFrameAndMeta()
            expected_value = meta['sequence'] % 256
            if not np.all(frame == expected_value):
                mismatches.append(meta['sequence'])
    finally:
        stop_writer.set()
        writer_thread.join(timeout=2.0)

    assert mismatches == []
    cam.stop()


# ---- deferred single-owner stop() cleanup ----------------------------------

def test_stop_with_blocked_read_frame_times_out_then_completes_cleanup_async():
    block_event = threading.Event()
    cam = _make_camera(device_kwargs={'timeout': 0.05, 'block_event': block_event})
    cam.start()

    fake = cam._device
    # Give the capture thread a moment to enter its (now-blocked) readFrame() call.
    time.sleep(0.05)

    cam.stop()
    # Bounded join (4 * 0.05s) should have elapsed and timed out -- the
    # thread is still blocked, so stop() must not have touched the device.
    assert 'disconnect' not in fake.calls
    assert cam._stopping is True

    # Release the blocked call; the capture thread's own cleanup should now
    # complete asynchronously, exactly once.
    block_event.set()
    assert _wait_until(lambda: cam._captureThreadDone.is_set(), timeout=2.0)
    assert fake.calls.count('disconnect') == 1
    assert fake.calls.count('stopScript') == 2  # once at start(), once at cleanup
    assert cam._stopping is False
    assert cam.camOn is False


def test_start_raises_runtime_error_while_still_stopping():
    block_event = threading.Event()
    cam = _make_camera(device_kwargs={'timeout': 0.05, 'block_event': block_event})
    cam.start()
    time.sleep(0.05)
    cam.stop()
    assert cam._stopping is True

    with pytest.raises(RuntimeError):
        cam.start()

    block_event.set()
    assert _wait_until(lambda: cam._captureThreadDone.is_set(), timeout=2.0)


# ---- pre- vs. post-capture-thread start() failure cleanup ------------------

@pytest.mark.parametrize('fail_at', ['connect', 'stopScript', 'runSource', 'streaming'])
def test_start_pre_thread_failure_cleans_up_synchronously(fail_at):
    cam = _make_camera(device_kwargs={'fail_at': fail_at})
    cam.start()

    assert cam._capture_thread is None
    assert cam._device is None
    assert cam.camOn is False

    # Review round 2 (Stage 2) caught that camera-level None checks alone
    # don't prove disconnect() was actually called on the real device
    # instance -- self._device could simply have never been assigned.
    # Retain and assert on the actual constructed fake instance instead.
    assert len(FakeDevice.instances) == 1
    assert FakeDevice.instances[0].calls.count('disconnect') == 1


def test_start_post_thread_failure_uses_deferred_cleanup():
    # outputPort=None + startStream=True forces start()'s own "cannot stream
    # when port is None" failure *after* _startCaptureThread() has already
    # run -- a genuine post-thread failure, not a fake-device injected one.
    cam = _make_camera(paramDict={'res_rows': 320, 'res_cols': 320, 'fps_target': 30, 'outputPort': None})
    cam.start(startStream=True, port=None)

    assert _wait_until(lambda: cam._captureThreadDone.is_set(), timeout=2.0)
    assert cam._device is None
    assert cam._capture_thread is None
    assert cam.camOn is False


def test_stop_stream_false_preserves_running_stream():
    cam = _make_camera()
    cam.start(startStream=True, port=8124)
    assert _wait_until(lambda: cam._mjpegServer is not None)

    cam.stop(stopStream=False)
    assert cam._mjpegServer is not None  # server left running

    cam.stopStream()
    assert cam.camOn is False


def test_shutdown():
    cam = _make_camera()
    cam.start()
    assert _wait_until(lambda: len(cam.frameDeque) > 0)
    cam.shutdown()
    assert cam.camOn is False


# ---- changeResolutionFramerate ---------------------------------------------

def test_change_resolution_framerate_restarts_with_new_rate():
    cam = _make_camera()
    cam.start()
    assert _wait_until(lambda: len(cam.frameDeque) > 0)

    cam.changeResolutionFramerate(framerate=45)
    assert cam._profile.config.histogram_rate_hz == 45
    assert cam.framerate == 45

    cam.stop()


def test_change_resolution_framerate_rejects_mismatched_resolution():
    cam = _make_camera()
    cam.start()
    with pytest.raises(ValueError):
        cam.changeResolutionFramerate(res_rows=160)
    assert cam._profile.config.resolution == FIXED_RESOLUTION
    cam.stop()
