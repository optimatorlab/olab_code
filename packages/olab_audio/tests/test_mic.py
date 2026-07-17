import pytest

from olab_audio.mic import Mic


class _FakeStream:
    def __init__(self):
        self.stopped = False
        self.closed = False

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _FakePyAudio:
    def __init__(self, default_rate=32000.0, fail_open=False):
        self.default_rate = default_rate
        self.fail_open = fail_open
        self.opened_with = None

    def get_device_info_by_host_api_device_index(self, host_api_index, device_index):
        return {'defaultSampleRate': self.default_rate}

    def open(self, **kwargs):
        if self.fail_open:
            raise OSError("[Errno -9997] Invalid sample rate")
        self.opened_with = kwargs
        return _FakeStream()


def test_mic_requires_device_id():
    with pytest.raises(Exception):
        Mic(deviceID=None)


def test_mic_start_queries_device_default_samplerate_when_unspecified(monkeypatch):
    """Real-hardware finding: some devices (e.g. a USB webcam mic) only
    support a non-44100Hz rate. Mic must query the device's own default
    rather than assume 44100Hz universally."""
    fake_audio = _FakePyAudio(default_rate=32000.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    mic = Mic(deviceID=3)
    mic.start()

    assert mic.samplerate == 32000
    assert fake_audio.opened_with['rate'] == 32000
    assert mic.micOn is True


def test_mic_start_uses_explicit_samplerate_when_given(monkeypatch):
    fake_audio = _FakePyAudio(default_rate=32000.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    mic = Mic(deviceID=3, samplerate=48000)
    mic.start()

    assert mic.samplerate == 48000
    assert fake_audio.opened_with['rate'] == 48000


def test_mic_stop_after_failed_start_does_not_raise(monkeypatch):
    """Reproduces the real-hardware-confirmed bug: a failed audio.open()
    left self.stream unset, so a later .stop() crashed with
    AttributeError: 'Mic' object has no attribute 'stream'. Mic.__init__
    now initializes self.stream = None and _stop_stream() guards on it."""
    fake_audio = _FakePyAudio(fail_open=True)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    errors = []
    mic = Mic(deviceID=3, excFunc=lambda msg: errors.append(msg))
    mic.start()

    assert mic.micOn is False
    assert any('ERROR in start' in m for m in errors)

    mic.stop()  # must not raise

    assert mic.stream is None


def test_mic_stop_is_idempotent(monkeypatch):
    fake_audio = _FakePyAudio(default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    mic = Mic(deviceID=3)
    mic.start()
    assert mic.stream is not None

    mic.stop()
    assert mic.stream is None

    mic.stop()  # calling stop() again must not raise
    assert mic.stream is None


def test_mic_record_start_returns_true_on_success(monkeypatch):
    fake_audio = _FakePyAudio(default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    mic = Mic(deviceID=3)
    mic.start()

    assert mic.recordStart(filename="out.wav") is True
    assert mic.isRecording is True
    assert mic.recording is not None


def test_mic_record_start_returns_false_and_leaves_clean_state_on_failure(monkeypatch, tmp_path):
    """Exercises the reviewer-flagged gap through the actual public
    Mic.recordStart() path (not just Recording_np directly) -- a cross-rate
    recording with the resample extra missing must be unambiguously
    detectable via the return value, not just inferred from excFunc having
    been called."""
    fake_audio = _FakePyAudio(default_rate=32000.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    import olab_audio._resample as resample_module

    def _require_soxr_missing():
        raise RuntimeError("olab-audio needs the 'resample' extra")

    monkeypatch.setattr(resample_module, "_require_soxr", _require_soxr_missing)

    errors = []
    mic = Mic(deviceID=3, excFunc=lambda msg: errors.append(msg))
    mic.start()  # samplerate becomes 32000 (device default)

    result = mic.recordStart(samplerateRec=16000, filepath=str(tmp_path), filename="out.wav")

    assert result is False
    assert mic.isRecording is False
    assert mic.recording is None
    assert any('ERROR in recordStart' in m for m in errors)
