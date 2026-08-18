import os
import sys

import numpy as np
import pytest

from olab_audio import device
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
    def __init__(self, default_rate=32000.0, fail_open=False, device_count=8):
        self.default_rate = default_rate
        self.fail_open = fail_open
        self.device_count = device_count
        self.opened_with = None
        self.pipewire_props_at_open = None

    def get_device_count(self):
        return self.device_count

    def get_device_info_by_host_api_device_index(self, host_api_index, device_index):
        return {'defaultSampleRate': self.default_rate}

    def open(self, **kwargs):
        if self.fail_open:
            raise OSError("[Errno -9997] Invalid sample rate")
        self.opened_with = kwargs
        self.pipewire_props_at_open = os.environ.get('PIPEWIRE_PROPS')
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


def test_mic_start_uses_unique_temporary_pipewire_identity(monkeypatch):
    """The PipeWire ALSA stream must not inherit the generic interpreter
    identity that WirePlumber can use to replay another process's source move."""
    fake_audio = _FakePyAudio(default_rate=32000.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    original_props = '{ media.role = "Communication" }'
    monkeypatch.setenv('PIPEWIRE_PROPS', original_props)

    first = Mic(deviceID=3)
    first.start()
    first_props = fake_audio.pipewire_props_at_open

    second = Mic(deviceID=3)
    second.start()
    second_props = fake_audio.pipewire_props_at_open

    assert 'media.role = "Communication"' in first_props
    assert 'node.name = "olab_audio.Mic.pid' in first_props
    assert 'application.name = "olab_audio.Mic.pid' in first_props
    assert first_props != second_props
    assert os.environ['PIPEWIRE_PROPS'] == original_props


def test_mic_start_removes_temporary_pipewire_identity_when_unset(monkeypatch):
    fake_audio = _FakePyAudio(default_rate=32000.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    monkeypatch.delenv('PIPEWIRE_PROPS', raising=False)

    Mic(deviceID=3).start()

    assert fake_audio.pipewire_props_at_open is not None
    assert 'olab_audio.Mic.pid' in fake_audio.pipewire_props_at_open
    assert 'PIPEWIRE_PROPS' not in os.environ


def test_mic_start_restores_pipewire_props_when_open_fails(monkeypatch):
    fake_audio = _FakePyAudio(fail_open=True)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    monkeypatch.setenv('PIPEWIRE_PROPS', '{ media.role = "Communication" }')

    Mic(deviceID=3).start()

    assert os.environ['PIPEWIRE_PROPS'] == '{ media.role = "Communication" }'


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


def test_mic_start_rejects_out_of_range_device_id_without_opening(monkeypatch):
    """Real-hardware finding (2026-07-17): PortAudio's ALSA host API
    segfaults -- a C-level crash Python's try/except cannot catch -- when
    audio.open() is given a device index at or past get_device_count().
    Mic.start() must validate the index itself and raise before ever
    reaching audio.open(), turning the crash into an ordinary catchable
    failure like any other start() error."""
    fake_audio = _FakePyAudio(device_count=8)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    errors = []
    mic = Mic(deviceID=99999, excFunc=lambda msg: errors.append(msg))
    mic.start()

    assert mic.micOn is False
    assert mic.stream is None
    assert fake_audio.opened_with is None  # audio.open() must never be reached
    assert any('Invalid deviceID' in m for m in errors)

    mic.stop()  # must not raise
    assert mic.stream is None


def test_mic_start_rejects_negative_device_id_without_opening(monkeypatch):
    """A negative deviceID (e.g. -1) isn't caught by an upper-bound-only
    check and silently opens PortAudio's ambient default device instead of
    raising -- confirmed via real hardware. Must be rejected the same way
    as an out-of-range positive index."""
    fake_audio = _FakePyAudio(device_count=8)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    errors = []
    mic = Mic(deviceID=-1, excFunc=lambda msg: errors.append(msg))
    mic.start()

    assert mic.micOn is False
    assert mic.stream is None
    assert fake_audio.opened_with is None
    assert any('Invalid deviceID' in m for m in errors)


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


def test_mic_treats_shared_loopback_deviceid_like_any_other_valid_device(monkeypatch, tmp_path):
    """get_loopback_input_devices() returns entries sharing one deviceID
    (see test_device.py) -- confirms Mic doesn't care that the device is
    Pulse-routed rather than a physical mic. This does NOT and cannot prove
    which sink's audio arrives; that's start_loopback_capture()'s job,
    verified separately (fake-Pulse tests below) and on real hardware
    (manual matrix in the design docs)."""
    fake_audio = _FakePyAudio(default_rate=48000.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    loopback_device_id = 2  # stands in for the shared 'pipewire'/'pulse'/'default' deviceID
    mic = Mic(deviceID=loopback_device_id)
    mic.start()

    assert mic.micOn is True
    assert fake_audio.opened_with['input_device_index'] == loopback_device_id

    assert mic.recordStart(filepath=str(tmp_path), filename="loopback.wav") is True
    assert mic.isRecording is True

    # Actually drive the callback PortAudio would invoke, with real PCM
    # data, rather than only exercising the start/stop bookkeeping around
    # an empty recording -- an empty recording would pass even if the
    # callback/recording wiring were broken.
    frame_count = 480
    pcm = np.full(frame_count, 1000, dtype=np.int16).tobytes()
    stream_callback = fake_audio.opened_with['stream_callback']
    stream_callback(pcm, frame_count, {}, 0)

    mic.recordStop()
    mic.stop()
    assert mic.stream is None
    mic.stop()  # idempotent

    from wave import open as open_wave
    with open_wave(str(tmp_path / "loopback.wav"), "rb") as wf:
        assert wf.getnchannels() == mic.channels
        assert wf.getframerate() == 48000
        assert wf.getnframes() == frame_count
        # Recording_np round-trips through a float32-normalized ->
        # int16-PCM conversion (see saveAudio()), so the written samples
        # are close to but not byte-identical to the original int16 input
        # -- assert they're nonzero and near the injected value rather
        # than bit-exact.
        written = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        assert len(written) == frame_count
        assert np.all(written != 0)
        assert np.allclose(written, 1000, atol=2)


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


# --- start_loopback_capture() fixtures -------------------------------------
# Self-contained fake-Pulse source-output fixtures, not shared with
# test_device.py's sink/source fixtures (different Pulse object, no
# existing conftest.py to share them through -- see the pairwork plan).

class _FakePulseSourceOutput:
    def __init__(self, index, client=None, source=0, proplist=None):
        self.index = index
        self.client = client
        self.source = source
        self.proplist = proplist or {}


class _FakePulseClientInfo:
    def __init__(self, proplist=None):
        self.proplist = proplist or {}


class _FakePulseTargetSource:
    def __init__(self, index, name):
        self.index = index
        self.name = name


class _FakeSourceOutputPulseClient:
    """Stands in for a `pulsectl.Pulse(...)` instance, modeling only the
    source-output-list/move/client-lookup surface `start_loopback_capture()`
    actually uses."""

    def __init__(self, initial_source_outputs=(), target_source_index=42,
                 client_proplists=None, move_updates_state=True,
                 unmatched_source_names=(), raise_on_move=None):
        self._source_outputs = list(initial_source_outputs)
        self._target_source_index = target_source_index
        self._client_proplists = client_proplists or {}
        self._move_updates_state = move_updates_state
        self._unmatched_source_names = set(unmatched_source_names)
        self._raise_on_move = raise_on_move
        self.move_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get_source_by_name(self, name):
        if name in self._unmatched_source_names:
            raise Exception(f'no such source: {name}')
        return _FakePulseTargetSource(self._target_source_index, name)

    def source_output_list(self):
        return list(self._source_outputs)

    def source_output_move(self, index, target_source_index):
        self.move_calls.append((index, target_source_index))
        if self._raise_on_move is not None:
            raise self._raise_on_move
        if self._move_updates_state:
            for so in self._source_outputs:
                if so.index == index:
                    so.source = target_source_index

    def client_info(self, client_index):
        if client_index not in self._client_proplists:
            raise Exception(f'no such client: {client_index}')
        return _FakePulseClientInfo(self._client_proplists[client_index])


class _FakePulsectlModuleForSourceOutputs:
    def __init__(self, client):
        self._client = client
        self.pulse_client_names = []

    def Pulse(self, client_name):
        self.pulse_client_names.append(client_name)
        return self._client


def _install_fake_pulsectl(monkeypatch, client):
    fake_module = _FakePulsectlModuleForSourceOutputs(client)
    monkeypatch.setitem(sys.modules, 'pulsectl', fake_module)
    return fake_module


class _FakePyAudioTriggeringSourceOutput(_FakePyAudio):
    """Models "opening the PortAudio stream creates a new PulseAudio
    source-output" deterministically: `open()` appends the given fake
    source-outputs to the shared fake Pulse client's list at the moment
    it's called, so a snapshot taken before `mic.start()` never sees them,
    the same way a snapshot-before-start would on real hardware."""

    def __init__(self, client, new_source_outputs, **kwargs):
        super().__init__(**kwargs)
        self._client = client
        self._new_source_outputs = new_source_outputs

    def open(self, **kwargs):
        stream = super().open(**kwargs)
        self._client._source_outputs.extend(self._new_source_outputs)
        return stream


_LOOPBACK_SOURCE = {'sourceName': 'alsa_output.pci-1.monitor'}


def test_start_loopback_capture_happy_path_own_proplist_pid(monkeypatch):
    this_pid = str(os.getpid())
    new_so = _FakePulseSourceOutput(index=5, client=1, source=0,
                                     proplist={'application.process.id': this_pid})
    client = _FakeSourceOutputPulseClient(target_source_index=42)
    fake_audio = _FakePyAudioTriggeringSourceOutput(client, [new_so], default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.2, poll_interval=0.01)

    assert mic.micOn is True
    assert client.move_calls == [(5, 42)]


def test_start_loopback_capture_happy_path_client_proplist_pid_fallback(monkeypatch):
    """A source-output whose own proplist omits the PID, but whose
    `.client` resolves to a client proplist that has it, is still
    identified -- proves the client-proplist fallback lookup actually
    runs, not just the source-output's own proplist."""
    this_pid = str(os.getpid())
    new_so = _FakePulseSourceOutput(index=5, client=7, source=0, proplist={})
    client = _FakeSourceOutputPulseClient(
        target_source_index=42,
        client_proplists={7: {'application.process.id': this_pid}},
    )
    fake_audio = _FakePyAudioTriggeringSourceOutput(client, [new_so], default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.2, poll_interval=0.01)

    assert mic.micOn is True
    assert client.move_calls == [(5, 42)]


def test_start_loopback_capture_timeout_when_nothing_new_appears(monkeypatch):
    client = _FakeSourceOutputPulseClient(target_source_index=42)
    fake_audio = _FakePyAudio(default_rate=44100.0)  # no trigger -- nothing new ever appears
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    with pytest.raises(RuntimeError):
        device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.05, poll_interval=0.01)

    assert mic.stream is None
    assert client.move_calls == []


def test_start_loopback_capture_ambiguous_when_two_own_candidates_appear(monkeypatch):
    """Simulates a second Mic/app started concurrently in this same
    process -- two identity-confirmed new source-outputs is ambiguity, not
    a pick-either free choice."""
    this_pid = str(os.getpid())
    new_so_1 = _FakePulseSourceOutput(index=5, client=1, proplist={'application.process.id': this_pid})
    new_so_2 = _FakePulseSourceOutput(index=6, client=2, proplist={'application.process.id': this_pid})
    client = _FakeSourceOutputPulseClient(target_source_index=42)
    fake_audio = _FakePyAudioTriggeringSourceOutput(client, [new_so_1, new_so_2], default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    with pytest.raises(RuntimeError):
        device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.05, poll_interval=0.01)

    assert mic.stream is None
    assert client.move_calls == []


def test_start_loopback_capture_excludes_foreign_pid_entry(monkeypatch):
    """A new source-output whose own and client proplists both resolve to
    a different process's PID must never be selected by elimination just
    because it's the only new entry."""
    foreign_so = _FakePulseSourceOutput(index=5, client=1,
                                         proplist={'application.process.id': '999999999'})
    client = _FakeSourceOutputPulseClient(target_source_index=42)
    fake_audio = _FakePyAudioTriggeringSourceOutput(client, [foreign_so], default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    with pytest.raises(RuntimeError):
        device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.05, poll_interval=0.01)

    assert mic.stream is None
    assert client.move_calls == []


def test_start_loopback_capture_excludes_entry_with_no_identity_available(monkeypatch):
    """A new source-output with no PID on its own proplist, and whose
    `.client` lookup itself fails (no client-side proplist either), must
    be excluded as unconfirmed -- not treated as ours by default."""
    unidentified_so = _FakePulseSourceOutput(index=5, client=1, proplist={})
    client = _FakeSourceOutputPulseClient(target_source_index=42, client_proplists={})
    fake_audio = _FakePyAudioTriggeringSourceOutput(client, [unidentified_so], default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    with pytest.raises(RuntimeError):
        device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.05, poll_interval=0.01)

    assert mic.stream is None
    assert client.move_calls == []


def test_start_loopback_capture_never_selects_a_presnapshot_entry(monkeypatch):
    """A source-output already present *before* mic.start() -- even one
    whose identity matches this process's PID (e.g. a second, already
    running Mic) -- must never be selected. Proves the snapshot-diff, not
    identity alone, scopes "ours": with no new entry ever appearing, this
    must fail exactly like the plain timeout case."""
    this_pid = str(os.getpid())
    preexisting_so = _FakePulseSourceOutput(index=5, client=1,
                                             proplist={'application.process.id': this_pid})
    client = _FakeSourceOutputPulseClient(initial_source_outputs=[preexisting_so], target_source_index=42)
    fake_audio = _FakePyAudio(default_rate=44100.0)  # no trigger -- nothing NEW ever appears
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    with pytest.raises(RuntimeError):
        device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.05, poll_interval=0.01)

    assert mic.stream is None
    assert client.move_calls == []


def test_start_loopback_capture_succeeds_even_when_fake_source_field_does_not_update(monkeypatch):
    """Round-6 finding: real-hardware testing proved a source-output's
    reported `.source`/attached-source field can stay unchanged even after
    a demonstrably successful move on some `pipewire-pulse` versions. A
    non-raising `source_output_move()` call is therefore trusted as
    success on its own -- no post-move re-check exists anymore. This fake
    deliberately does NOT update its recorded state after the move (mirrors
    the unreliable-field behavior seen on real hardware), and
    start_loopback_capture() must still complete successfully."""
    this_pid = str(os.getpid())
    new_so = _FakePulseSourceOutput(index=5, client=1, proplist={'application.process.id': this_pid})
    client = _FakeSourceOutputPulseClient(target_source_index=42, move_updates_state=False)
    fake_audio = _FakePyAudioTriggeringSourceOutput(client, [new_so], default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.2, poll_interval=0.01)

    assert mic.micOn is True
    assert client.move_calls == [(5, 42)]


def test_start_loopback_capture_stops_mic_when_move_raises(monkeypatch):
    """Reviewer-flagged gap: a pulsectl-side exception from
    source_output_move() (server disconnect, rejected move, etc.) after
    mic.start() succeeded must not escape uncaught and leave mic running
    and silently capturing the physical mic -- it must be caught, mic
    stopped, and a clear RuntimeError raised, with no default/source
    routing changed."""
    this_pid = str(os.getpid())
    new_so = _FakePulseSourceOutput(index=5, client=1, proplist={'application.process.id': this_pid})
    client = _FakeSourceOutputPulseClient(
        target_source_index=42,
        raise_on_move=Exception('PulseOperationFailed: server disconnected'),
    )
    fake_audio = _FakePyAudioTriggeringSourceOutput(client, [new_so], default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    with pytest.raises(RuntimeError):
        device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.2, poll_interval=0.01)

    assert mic.micOn is False
    assert mic.stream is None
    assert client.move_calls == [(5, 42)]  # the move was attempted, and only once


def test_start_loopback_capture_rejects_an_already_started_mic(monkeypatch):
    fake_audio = _FakePyAudio(default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    mic = Mic(deviceID=3)
    mic.start()
    assert mic.micOn is True

    client = _FakeSourceOutputPulseClient(target_source_index=42)
    fake_pulsectl = _install_fake_pulsectl(monkeypatch, client)

    with pytest.raises(RuntimeError):
        device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.2, poll_interval=0.01)

    assert fake_pulsectl.pulse_client_names == []  # never opened


def test_start_loopback_capture_propagates_mic_start_failure_without_moving(monkeypatch):
    fake_audio = _FakePyAudio(fail_open=True)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)

    client = _FakeSourceOutputPulseClient(target_source_index=42)
    fake_pulsectl = _install_fake_pulsectl(monkeypatch, client)

    mic = Mic(deviceID=3)
    with pytest.raises(RuntimeError):
        device.start_loopback_capture(mic, _LOOPBACK_SOURCE, timeout=0.2, poll_interval=0.01)

    assert mic.micOn is False
    assert mic.stream is None
    # The read-only target-source lookup and snapshot DO open a Pulse
    # client before mic.start() is attempted -- that's expected, not a
    # violation (see the pairwork Log's round-4 correction). What must
    # never happen is an actual move.
    assert fake_pulsectl.pulse_client_names == ['olab-audio-loopback-capture']
    assert client.move_calls == []


def test_start_loopback_capture_forwards_mic_start_kwargs(monkeypatch, tmp_path):
    """A custom reachbackFunc passed through start_loopback_capture()
    actually reaches Mic.start() and receives PCM after routing -- proves
    forwarding isn't just accepted syntactically."""
    this_pid = str(os.getpid())
    new_so = _FakePulseSourceOutput(index=5, client=1, proplist={'application.process.id': this_pid})
    client = _FakeSourceOutputPulseClient(target_source_index=42)
    fake_audio = _FakePyAudioTriggeringSourceOutput(client, [new_so], default_rate=44100.0)
    monkeypatch.setattr("olab_audio.mic.audio", fake_audio)
    _install_fake_pulsectl(monkeypatch, client)

    received = []
    mic = Mic(deviceID=3)
    device.start_loopback_capture(
        mic, _LOOPBACK_SOURCE, timeout=0.2, poll_interval=0.01,
        reachbackFunc=lambda deviceID, data: received.append(data),
    )

    frame_count = 240
    pcm = np.full(frame_count, 500, dtype=np.int16).tobytes()
    fake_audio.opened_with['stream_callback'](pcm, frame_count, {}, 0)

    assert len(received) == 1
    assert len(received[0]) == frame_count
