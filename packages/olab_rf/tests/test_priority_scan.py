from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from olab_rf.models import FrequencyCatalogRange, FrequencyChannel, PcmAudioFrame
from olab_rf.models.tracks import utc_now
from olab_rf.services.frequency_catalog import FrequencyCatalog
from olab_rf.services.session_manager import SessionManager

SAMPLE_RATE = 24_000  # rtl_fm_audio_rate_hz("NFM")
FRAME_MS = 40
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

AM_SAMPLE_RATE = 12_000  # rtl_fm_audio_rate_hz("am")
AM_FRAME_SAMPLES = AM_SAMPLE_RATE * FRAME_MS // 1000


def _silence_frame(index: int, *, sample_rate_hz: int = SAMPLE_RATE) -> PcmAudioFrame:
    samples = sample_rate_hz * FRAME_MS // 1000
    return PcmAudioFrame(
        pcm_s16le=np.zeros(samples, dtype="<i2").tobytes(),
        sample_rate_hz=sample_rate_hz,
        captured_at=utc_now() + timedelta(milliseconds=FRAME_MS * index),
    )


def _voice_frame(
    index: int, amplitude: float = 0.4, *, sample_rate_hz: int = SAMPLE_RATE
) -> PcmAudioFrame:
    """A 350/700 Hz mix, matching this repo's own hf_ratio-voice-like reference frame."""
    samples_per_frame = sample_rate_hz * FRAME_MS // 1000
    n = np.arange(samples_per_frame) + index * samples_per_frame
    samples = amplitude * (
        np.sin(2 * np.pi * 350 * n / sample_rate_hz)
        + 0.6 * np.sin(2 * np.pi * 700 * n / sample_rate_hz)
    ) / 1.6
    return PcmAudioFrame(
        pcm_s16le=(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes(),
        sample_rate_hz=sample_rate_hz,
        captured_at=utc_now() + timedelta(milliseconds=FRAME_MS * index),
    )


class FakeBackend:
    """Stands in for RtlFmAudioBackend: serves scripted frame batches, no subprocess."""

    def __init__(self, sample_rate_hz: int, script: list[list[PcmAudioFrame]], *, raise_on_start: bool = False):
        self.sample_rate_hz = sample_rate_hz
        self._script = script
        self._raise_on_start = raise_on_start
        self.started = False
        self.stopped = False
        self._crashed = False

    def start(self) -> None:
        if self._raise_on_start:
            raise FileNotFoundError("rtl_fm not found")
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_running(self) -> bool:
        return not self.stopped and not self._crashed

    def read_stderr_lines(self) -> list[str]:
        return ["fake backend crashed"] if self._crashed else []

    def read_frames(self) -> list[PcmAudioFrame]:
        if self._script:
            return self._script.pop(0)
        return []

    def crash(self) -> None:
        self._crashed = True


class FakeBackendFactory:
    """Replaces RtlFmAudioBackend in session_manager for the duration of a test.

    Scripts are keyed by frequency_hz and shared across every backend built for
    that frequency, so a scripted sequence spanning multiple visits to the same
    channel drains progressively across backend rebuilds.
    """

    def __init__(
        self,
        scripts_by_frequency: dict[int, list[list[PcmAudioFrame]]],
        *,
        raise_on_start_for: set[int] | None = None,
    ) -> None:
        self._scripts = scripts_by_frequency
        self._raise_on_start_for = raise_on_start_for or set()
        self.instances: list[FakeBackend] = []

    def __call__(
        self,
        *,
        path: str,
        frequency_hz: int,
        modulation: str,
        sample_rate_hz: int,
        frame_ms: int,
        ppm: int | None = None,
        gain_db: float | None = None,
        fir_size: int | None = None,
        atan_math: str | None = None,
        extra_args: list[str] | None = None,
    ) -> FakeBackend:
        backend = FakeBackend(
            sample_rate_hz,
            self._scripts.get(frequency_hz, []),
            raise_on_start=frequency_hz in self._raise_on_start_for,
        )
        self.instances.append(backend)
        return backend


def _install_fake_backend(monkeypatch: pytest.MonkeyPatch, factory: FakeBackendFactory) -> None:
    monkeypatch.setattr("olab_rf.services.session_manager.RtlFmAudioBackend", factory)


def _catalog() -> FrequencyCatalog:
    # Listed first and wide enough to swallow buffalo_fd_f1's frequency, the way
    # the base catalog's murs/frs_gmrs/aviation_am ranges do in production --
    # a regression guard against re-deriving identity via match_frequency().
    wide_shadow_range = FrequencyCatalogRange(
        id="wide_shadow",
        label="Wide Shadow Range",
        min_freq_hz=151_000_000,
        max_freq_hz=155_000_000,
        default_modulation="NFM",
    )
    fire_range = FrequencyCatalogRange(
        id="erie_fire_vhf",
        label="Erie County Fire VHF",
        min_freq_hz=154_000_000,
        max_freq_hz=154_500_000,
        default_modulation="NFM",
        channels=[
            FrequencyChannel(
                id="buffalo_fd_f1", label="Buffalo FD F1", frequency_hz=154_190_000, modulation="NFM"
            ),
            FrequencyChannel(
                id="buffalo_fd_f2", label="Buffalo FD F2", frequency_hz=154_430_000, modulation="NFM"
            ),
        ],
    )
    weather_range = FrequencyCatalogRange(
        id="noaa_weather",
        label="NOAA Weather",
        min_freq_hz=162_400_000,
        max_freq_hz=162_550_000,
        default_modulation="NFM",
        channels=[
            FrequencyChannel(id="noaa_wx_1", label="NOAA WX1", frequency_hz=162_400_000, modulation="NFM"),
            # No explicit modulation: exercises the channel -> range -> "NFM" fallback chain.
            FrequencyChannel(id="noaa_wx_2", label="NOAA WX2", frequency_hz=162_425_000, modulation=None),
        ],
    )
    airport_range = FrequencyCatalogRange(
        id="local_airports",
        label="Local Airports",
        min_freq_hz=118_000_000,
        max_freq_hz=137_000_000,
        default_modulation="am",
        channels=[
            FrequencyChannel(id="kbuf_tower", label="KBUF Tower", frequency_hz=118_275_000, modulation="am"),
            FrequencyChannel(
                id="test_wbfm", label="Test WBFM", frequency_hz=100_000_000, modulation="wfm"
            ),
        ],
    )
    return FrequencyCatalog(ranges=[wide_shadow_range, fire_range, weather_range, airport_range])


def _manager() -> SessionManager:
    return SessionManager(frequency_catalog=_catalog())


# --- state machine -----------------------------------------------------------


def test_single_channel_state_transitions_and_backend_reuse(monkeypatch):
    freq = 154_190_000
    script = [
        [_silence_frame(i) for i in range(50)],  # times out the dwell
        [_voice_frame(i) for i in range(3)],  # locks immediately
        [_silence_frame(i) for i in range(15)],  # closes the segment (hang_time_ms=600 -> 15 frames)
    ]
    factory = FakeBackendFactory({freq: script})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1"])
    assert manager.current_priority_scan_status().state == "scanning"

    manager.poll()  # consumes the 50 silent frames -> dwell times out -> re-arms same channel
    assert manager.current_priority_scan_status().state == "scanning"
    assert len(factory.instances) == 1, "single-channel scan must not respawn the backend"

    manager.poll()  # consumes the 3 voice frames -> locks
    assert manager.current_priority_scan_status().state == "locked"

    manager.poll()  # consumes the 15 silent frames -> segment closes -> back to scanning
    segments = manager.pop_voice_segments()
    assert len(segments) == 1
    assert manager.current_priority_scan_status().state == "scanning"
    assert len(factory.instances) == 1, "single-channel scan must keep reusing one backend"

    manager.stop()
    assert manager.current_priority_scan_status() is None
    assert manager.status.mode == "idle"
    assert factory.instances[0].stopped


def test_round_robin_cycles_through_full_list_and_wraps(monkeypatch):
    freqs = [154_190_000, 154_430_000, 162_400_000]
    factory = FakeBackendFactory({freq: [[_silence_frame(i) for i in range(50)]] for freq in freqs})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1", "buffalo_fd_f2", "noaa_wx_1"], dwell_ms=2_000)
    assert manager.current_priority_scan_status().current_channel_id == "buffalo_fd_f1"

    manager.poll()
    assert manager.current_priority_scan_status().current_channel_id == "buffalo_fd_f2"
    manager.poll()
    assert manager.current_priority_scan_status().current_channel_id == "noaa_wx_1"
    manager.poll()
    status = manager.current_priority_scan_status()
    assert status.current_channel_id == "buffalo_fd_f1"
    assert status.cycle_count == 1


def test_immediate_lock_when_channel_already_active(monkeypatch):
    """No suppressed startup calibration window: detection is live from frame 0."""
    freq = 154_190_000
    factory = FakeBackendFactory({freq: [[_voice_frame(i) for i in range(3)]]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1"])
    manager.poll()

    assert manager.current_priority_scan_status().state == "locked"


def test_dwell_does_not_expire_while_backend_has_not_yet_produced_frames(monkeypatch):
    freq = 154_190_000
    # Several polls with nothing at all (simulating rtl_fm spawn/USB retune
    # latency), well past what 2_000ms/40ms would allow if counted from
    # backend.start() instead of first ingested frame.
    script = [[] for _ in range(80)] + [[_silence_frame(i) for i in range(10)]]
    factory = FakeBackendFactory({freq: script})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1"], dwell_ms=2_000)
    for _ in range(80):
        manager.poll()
    # Only 10 real frames ingested so far (400ms) -- must not have expired.
    manager.poll()
    assert manager.current_priority_scan_status().state == "scanning"
    assert len(factory.instances) == 1


def test_short_transmission_within_one_batch_is_not_missed(monkeypatch):
    freq = 154_190_000
    batch = (
        [_silence_frame(i) for i in range(3)]
        + [_voice_frame(i) for i in range(3, 6)]
        + [_silence_frame(i) for i in range(6, 21)]  # 15 frames of hang time
    )
    factory = FakeBackendFactory({freq: [batch]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1"])
    manager.poll()

    assert len(manager.pop_voice_segments()) == 1


# --- max_lock_ms ---------------------------------------------------------------


def test_max_lock_ms_forces_a_switch_off_a_continuous_carrier(monkeypatch):
    freqs = [154_190_000, 154_430_000]
    # max_segment_sec is set high enough that the cap never fires inside this
    # test, isolating max_lock_ms as the only possible trigger.
    continuous = [_voice_frame(i) for i in range(120)]
    factory = FakeBackendFactory({freqs[0]: [continuous], freqs[1]: [[]]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(
        ["buffalo_fd_f1", "buffalo_fd_f2"], max_segment_sec=1_000.0, max_lock_ms=2_000
    )
    manager.poll()

    status = manager.current_priority_scan_status()
    assert status.current_channel_id == "buffalo_fd_f2"


def test_max_lock_ms_derived_default_lets_a_default_length_segment_complete(monkeypatch):
    freq = 154_190_000
    continuous = [_voice_frame(i) for i in range(520)]
    factory = FakeBackendFactory({freq: [continuous]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1"])  # max_lock_ms derived, not explicit
    manager.poll()

    segments = manager.pop_voice_segments()
    assert len(segments) == 1
    assert 18.0 < segments[0].duration_sec <= 20.1
    assert manager.current_priority_scan_status().capped_closes == 1


# --- attribution and modulation resolution --------------------------------------


def test_segment_metadata_attributes_to_the_resolved_channel_not_a_shadowing_range(monkeypatch):
    freq = 154_190_000
    batch = (
        [_voice_frame(i) for i in range(3)] + [_silence_frame(i) for i in range(3, 18)]
    )
    factory = FakeBackendFactory({freq: [batch]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["erie_fire_vhf.buffalo_fd_f1"])
    manager.poll()

    segments = manager.pop_voice_segments()
    assert len(segments) == 1
    metadata = segments[0].metadata
    assert metadata["channel_id"] == "buffalo_fd_f1"
    assert metadata["range_id"] == "erie_fire_vhf"  # not "wide_shadow"


def test_modulation_none_falls_back_through_range_default_to_nfm(monkeypatch):
    freq = 162_425_000  # noaa_wx_2: modulation=None, range default_modulation="NFM"
    factory = FakeBackendFactory({freq: [[]]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["noaa_wx_2"])

    channel = manager._priority_scan_state.channels[0]
    assert channel.modulation == "NFM"
    assert channel.sample_rate_hz == 24_000


def test_wbfm_channel_is_rejected_before_any_backend_starts(monkeypatch):
    factory = FakeBackendFactory({})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    with pytest.raises(ValueError, match="wbfm"):
        manager.start_priority_scan(["test_wbfm"])

    assert factory.instances == []


# --- validation and failure handling --------------------------------------------


def test_empty_channel_ids_raises_value_error(monkeypatch):
    manager = _manager()
    with pytest.raises(ValueError, match="channel_ids"):
        manager.start_priority_scan([])


def test_ambiguous_bare_channel_id_raises_value_error(monkeypatch):
    catalog = FrequencyCatalog(
        ranges=[
            FrequencyCatalogRange(
                id="range_a",
                label="Range A",
                min_freq_hz=1,
                max_freq_hz=2,
                channels=[FrequencyChannel(id="dup", label="Dup A", frequency_hz=100_000_000, modulation="NFM")],
            ),
            FrequencyCatalogRange(
                id="range_b",
                label="Range B",
                min_freq_hz=1,
                max_freq_hz=2,
                channels=[FrequencyChannel(id="dup", label="Dup B", frequency_hz=200_000_000, modulation="NFM")],
            ),
        ]
    )
    manager = SessionManager(frequency_catalog=catalog)
    with pytest.raises(ValueError, match="ambiguous"):
        manager.start_priority_scan(["dup"])


def test_detector_overrides_unknown_key_raises_before_any_backend_starts(monkeypatch):
    factory = FakeBackendFactory({})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    with pytest.raises(ValueError, match="not present in channel_ids"):
        manager.start_priority_scan(
            ["buffalo_fd_f1"], detector_overrides={"noaa_wx_1": {"hf_ratio_threshold": 2.0}}
        )

    assert factory.instances == []


@pytest.mark.parametrize(
    "override,match",
    [
        pytest.param({"detector_mode": "hf_ratio_typo"}, "detector_mode", id="bad-detector-mode"),
        pytest.param({"hf_ratio_threshold": -1.0}, "hf_ratio_threshold", id="negative-threshold"),
        pytest.param({"hf_ratio_threshold": "loud"}, "hf_ratio_threshold", id="non-numeric-threshold"),
        pytest.param({"hf_ratio_thresold": 9.9}, "unrecognised", id="typo-d-inner-key"),
    ],
)
def test_detector_overrides_bad_value_raises_before_any_backend_starts(monkeypatch, override, match):
    """A bad override *value* must fail the same way a bad *key* does.

    Deferring this to RadioVoiceSegmenter.__init__ (which validates
    detector_mode eagerly) would raise wherever the affected channel's visit
    happens to land -- possibly from inside poll(), which every other mode
    treats as non-raising, and possibly after an earlier channel's backend is
    already live.
    """
    factory = FakeBackendFactory({})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    with pytest.raises(ValueError, match=match):
        manager.start_priority_scan(["buffalo_fd_f1"], detector_overrides={"buffalo_fd_f1": override})

    assert factory.instances == []


def test_mid_scan_backend_failure_aborts_the_whole_scan_to_idle(monkeypatch):
    freqs = [154_190_000, 154_430_000]
    factory = FakeBackendFactory(
        {freqs[0]: [[_silence_frame(i) for i in range(50)]], freqs[1]: []},
        raise_on_start_for={freqs[1]},
    )
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1", "buffalo_fd_f2"])
    manager.poll()  # times out channel 0's dwell, advances to channel 1, which fails to start

    assert manager.current_priority_scan_status() is None
    assert manager.status.mode == "idle"
    assert manager.status.error


def test_backend_not_found_error_message_names_the_path(monkeypatch):
    class RaisingFactory(FakeBackendFactory):
        def __call__(self, *, path, **kwargs):
            raise FileNotFoundError(path)

    manager = _manager()
    monkeypatch.setattr(
        "olab_rf.services.session_manager.RtlFmAudioBackend", RaisingFactory({})
    )

    with pytest.raises(RuntimeError, match="rtl_fm"):
        manager.start_priority_scan(["buffalo_fd_f1"])
    assert "rtl_fm" in manager.status.error


def test_construction_failure_on_the_first_channel_does_not_leak_a_live_backend(monkeypatch):
    """A RadioVoiceSegmenter construction failure must not strand a started backend."""
    factory = FakeBackendFactory({154_190_000: [[]]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    # A ValueError from segmenter construction is normally prevented for
    # detector_overrides by eager validation; simulate a different
    # construction failure that isn't (defense in depth) by monkeypatching
    # RadioVoiceSegmenter itself to raise.
    def _raise(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("olab_rf.services.session_manager.RadioVoiceSegmenter", _raise)

    with pytest.raises(ValueError, match="boom"):
        manager.start_priority_scan(["buffalo_fd_f1"])

    assert len(factory.instances) == 1
    assert factory.instances[0].stopped, "the backend started for the failed visit must be stopped"
    assert manager._voice_backend is None
    assert manager._priority_scan_state is None
    assert manager.session.status == "error"


def test_mid_scan_construction_failure_on_a_later_channel_aborts_to_idle_without_raising(monkeypatch):
    """A construction failure on a non-first channel must not escape poll()."""
    freq_a, freq_b = 154_190_000, 154_430_000
    factory = FakeBackendFactory({freq_a: [[_silence_frame(i) for i in range(50)]], freq_b: [[]]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()
    manager.start_priority_scan(["buffalo_fd_f1", "buffalo_fd_f2"])

    import olab_rf.services.session_manager as session_manager_module

    original = session_manager_module.RadioVoiceSegmenter
    calls = {"count": 0}

    def _flaky(*args, **kwargs):
        # start_priority_scan() already built channel 0's segmenter with the
        # real class before this monkeypatch was installed, so the *first*
        # call this patched function sees is channel 1's, on the advance
        # triggered by manager.poll() below.
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(session_manager_module, "RadioVoiceSegmenter", _flaky)

    manager.poll()  # times out channel 0's dwell -> advances to channel 1 -> construction fails

    assert manager.current_priority_scan_status() is None
    assert manager.status.mode == "idle"
    assert "boom" in manager.status.error


def test_duplicate_channel_ids_visit_the_same_channel_twice_when_spelled_differently(monkeypatch):
    """Documents current, accepted behavior: dedup is by raw string, not resolved id."""
    factory = FakeBackendFactory({154_190_000: [[]]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1", "erie_fire_vhf.buffalo_fd_f1"])

    assert [c.channel_id for c in manager._priority_scan_state.channels] == [
        "buffalo_fd_f1",
        "buffalo_fd_f1",
    ]


def test_backend_process_exit_mid_scan_aborts_to_idle(monkeypatch):
    freq = 154_190_000
    backend_holder: dict[str, FakeBackend] = {}

    class CrashingFactory(FakeBackendFactory):
        def __call__(self, **kwargs):
            backend = super().__call__(**kwargs)
            backend_holder["backend"] = backend
            return backend

    factory = CrashingFactory({freq: [[]]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1"])
    backend_holder["backend"].crash()
    manager.poll()

    assert manager.current_priority_scan_status() is None
    assert manager.status.mode == "idle"
    assert manager.status.error


# --- counters and AM conditioning -----------------------------------------------


def test_completed_segments_are_scan_lifetime_not_per_visit(monkeypatch):
    freqs = [154_190_000, 154_430_000]
    channel_a_script = [
        [_voice_frame(i) for i in range(3)] + [_silence_frame(i) for i in range(3, 18)],
    ]
    channel_b_script = [[_silence_frame(i) for i in range(50)]]
    factory = FakeBackendFactory({freqs[0]: channel_a_script, freqs[1]: channel_b_script})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["buffalo_fd_f1", "buffalo_fd_f2"])
    manager.poll()  # channel A: locks and closes one segment -> advances to channel B
    assert manager.current_priority_scan_status().current_channel_id == "buffalo_fd_f2"
    manager.poll()  # channel B: times out -> wraps back to channel A (fresh segmenter)

    status = manager.current_priority_scan_status()
    assert status.current_channel_id == "buffalo_fd_f1"
    assert status.cycle_count == 1
    assert status.completed_segments == 1, "must not have reset to 0 across two segmenter rebuilds"


def test_am_deemphasis_defaults_to_none_unless_explicitly_overridden(monkeypatch):
    factory = FakeBackendFactory({118_275_000: [[]], 154_190_000: [[]]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_priority_scan(["kbuf_tower", "buffalo_fd_f1"])
    am_channel = next(c for c in manager._priority_scan_state.channels if c.channel_id == "kbuf_tower")
    nfm_channel = next(
        c for c in manager._priority_scan_state.channels if c.channel_id == "buffalo_fd_f1"
    )
    assert am_channel.deemphasis_us is None
    assert nfm_channel.deemphasis_us == 75.0
    manager.stop()

    manager.start_priority_scan(["kbuf_tower"], deemphasis_us=75.0)
    explicit_am_channel = manager._priority_scan_state.channels[0]
    assert explicit_am_channel.deemphasis_us == 75.0, "an explicit value always wins over the AM default"


def test_priority_scan_generator_shares_the_am_deemphasis_sentinel(monkeypatch):
    freq = 118_275_000  # kbuf_tower, AM -> 12kHz
    batch = [_voice_frame(i, sample_rate_hz=AM_SAMPLE_RATE) for i in range(3)] + [
        _silence_frame(i, sample_rate_hz=AM_SAMPLE_RATE) for i in range(3, 18)
    ]
    factory = FakeBackendFactory({freq: [batch]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    generator = manager.priority_scan(channel_ids=["kbuf_tower"], max_segments=1)
    try:
        segment = next(generator)
        assert segment.metadata["channel_id"] == "kbuf_tower"
        state = manager._priority_scan_state
        assert state is not None
        assert state.channels[0].deemphasis_us is None
    finally:
        generator.close()

    assert manager.current_priority_scan_status() is None, "close() must stop the scan"


# --- mode exclusivity and generator teardown ------------------------------------


def test_starting_a_priority_scan_stops_another_active_mode(monkeypatch):
    freq = 154_190_000
    factory = FakeBackendFactory({freq: [[]]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    manager.start_replay()
    assert manager.session is not None and manager.session.mode == "replay"

    manager.start_priority_scan(["buffalo_fd_f1"])
    assert manager.session.mode == "priority_scan"


def test_priority_scan_generator_stops_on_early_break(monkeypatch):
    freq = 154_190_000
    batch = [_voice_frame(i) for i in range(3)] + [_silence_frame(i) for i in range(3, 18)]
    factory = FakeBackendFactory({freq: [batch]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    for _segment in manager.priority_scan(channel_ids=["buffalo_fd_f1"]):
        break

    assert manager.current_priority_scan_status() is None
    assert factory.instances[0].stopped


def test_priority_scan_generator_stops_on_keyboard_interrupt(monkeypatch):
    freq = 154_190_000
    batch = [_voice_frame(i) for i in range(3)] + [_silence_frame(i) for i in range(3, 18)]
    factory = FakeBackendFactory({freq: [batch]})
    _install_fake_backend(monkeypatch, factory)
    manager = _manager()

    generator = manager.priority_scan(channel_ids=["buffalo_fd_f1"])
    next(generator)  # advance past start_priority_scan(), suspended at the first yield
    assert manager.current_priority_scan_status() is not None

    with pytest.raises(KeyboardInterrupt):
        generator.throw(KeyboardInterrupt)

    assert manager.current_priority_scan_status() is None
    assert factory.instances[0].stopped
