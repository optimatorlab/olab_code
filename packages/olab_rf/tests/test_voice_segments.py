from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from time import monotonic, sleep
import wave

import numpy as np
import pytest

from olab_rf.models import PcmAudioFrame, RadioVoiceSegment
from olab_rf.models.tracks import utc_now
from olab_rf.services.session_manager import SessionManager
from olab_rf.services.voice_segments import (
    AudioConditioner,
    RadioVoiceSegmenter,
    RtlFmAudioBackend,
    band_energy_ratio,
)


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 640


def _frame(level: int, index: int) -> PcmAudioFrame:
    pcm = np.full(FRAME_SAMPLES, level, dtype="<i2").tobytes()
    return PcmAudioFrame(
        pcm_s16le=pcm,
        sample_rate_hz=SAMPLE_RATE,
        captured_at=utc_now() + timedelta(milliseconds=40 * index),
    )


def _segmenter(**kwargs: object) -> RadioVoiceSegmenter:
    """Build a segmenter pinned to rms_quieting unless a test says otherwise.

    These tests drive DC-constant frames, whose band-energy ratio is ~0 and so
    read as voice-like under the hf_ratio detector that is now the library
    default. Pinning keeps them testing level-based gating, which is what they
    were written for, instead of silently becoming a different test.
    """
    kwargs.setdefault("detector_mode", "rms_quieting")
    return RadioVoiceSegmenter(
        session_id="session-test",
        frequency_hz=462_712_500,
        modulation="NFM",
        sample_rate_hz=SAMPLE_RATE,
        **kwargs,
    )


def test_rtl_fm_audio_backend_builds_pcm_command():
    backend = RtlFmAudioBackend(
        path="rtl_fm-test",
        frequency_hz=462_712_500,
        modulation="NFM",
        sample_rate_hz=SAMPLE_RATE,
        frame_ms=40,
        gain_db=19.7,
    )

    assert backend.frame_bytes == 1280
    # No -l: the carrier gate detects FM quieting, so rtl_fm squelch would remove
    # the very hiss it measures against. Squelch on this path is the gate's job.
    assert backend.command == [
        "rtl_fm-test", "-d", "0", "-f", "462712500", "-M", "fm", "-s", "16000",
        "-g", "19.7", "-",
    ]


def test_segmenter_preserves_pre_roll_and_hang_time():
    segmenter = _segmenter(min_segment_ms=100, hang_time_ms=120)
    emitted = []
    for index in range(5):
        emitted.extend(segmenter.ingest(_frame(8_000, index)))
    for index in range(3):
        emitted.extend(segmenter.ingest(_frame(100, index + 5)))
    for index in range(3):
        emitted.extend(segmenter.ingest(_frame(8_000, index + 8)))

    assert len(emitted) == 1
    segment = emitted[0]
    assert segment.duration_sec == 0.32  # five pre-roll frames plus 3 speech frames
    assert segment.rms_db < -10
    assert segment.peak_db < 0
    assert segmenter.status().completed_segments == 1
    assert segmenter.status().last_frame_rms_db is not None
    assert segmenter.status().last_frame_peak_db is not None


def test_segmenter_drops_short_transmission_and_force_closes_maximum():
    segmenter = _segmenter(
        min_segment_ms=400, max_segment_sec=0.2, pre_roll_ms=0, hang_time_ms=120
    )
    for index in range(5):
        segmenter.ingest(_frame(8_000, index))
    for index in range(3):
        segmenter.ingest(_frame(100, index + 5))
    for index in range(15):
        segmenter.ingest(_frame(8_000, index + 8))
    assert segmenter.status().dropped_segments == 1

    segmenter = _segmenter(min_segment_ms=100, max_segment_sec=0.2, pre_roll_ms=0)
    for index in range(5):
        segmenter.ingest(_frame(8_000, index))
    emitted = []
    for index in range(8):
        emitted.extend(segmenter.ingest(_frame(100, index + 5)))
    assert len(emitted) == 1
    assert emitted[0].duration_sec == 0.2


def test_segmenter_applies_live_settings_and_resets_idle_calibration():
    segmenter = _segmenter()
    for index in range(5):
        segmenter.ingest(_frame(8_000, index))
    assert segmenter.status().noise_floor_db is not None

    segmenter.update_settings(
        threshold_db=5.0,
        min_active_ms=40,
        hang_time_ms=80,
        min_segment_ms=80,
        max_segment_sec=1.0,
        pre_roll_ms=80,
    )
    emitted = segmenter.ingest(_frame(100, 5))
    assert emitted == []
    assert segmenter.status().active

    segmenter.ingest(_frame(8_000, 6))
    segmenter.ingest(_frame(8_000, 7))
    segmenter.reset_calibration()
    assert segmenter.status().noise_floor_db is None


def test_radio_voice_segment_wav_and_audio_blob_payload(tmp_path):
    segment = RadioVoiceSegment(
        segment_id="segment-test",
        session_id="session-test",
        frequency_hz=462_712_500,
        modulation="NFM",
        sample_rate_hz=SAMPLE_RATE,
        pcm_s16le=_frame(1_000, 0).pcm_s16le,
        started_at=utc_now(),
        ended_at=utc_now(),
        rms_db=-30.0,
        peak_db=-20.0,
        noise_floor_db=-60.0,
        threshold_db=10.0,
    )
    wav = segment.to_wav_bytes()
    with wave.open(BytesIO(wav)) as reader:
        assert reader.getframerate() == SAMPLE_RATE
        assert reader.getnchannels() == 1
        assert reader.readframes(FRAME_SAMPLES) == segment.pcm_s16le
    path = segment.save_wav(tmp_path / "voice.wav")
    assert path.read_bytes() == wav
    assert "pcm_s16le" not in segment.to_dict()
    assert segment.to_dict(include_audio=True)["pcm_s16le"] == segment.pcm_s16le
    assert segment.to_audio_blob_payload()["data"] == wav


def test_manager_voice_iterator_cleans_up_at_max_segments():
    class FakeBackend:
        sample_rate_hz = SAMPLE_RATE

        def __init__(self) -> None:
            self.stopped = False
            self._read = False

        def start(self) -> None:
            pass

        def stop(self) -> None:
            self.stopped = True

        def is_running(self) -> bool:
            return not self._read

        def read_stderr_lines(self) -> list[str]:
            return []

        def read_frames(self) -> list[PcmAudioFrame]:
            if self._read:
                return []
            self._read = True
            return [_frame(8_000, index) for index in range(5)] + [
                _frame(100, index) for index in range(5, 8)
            ] + [_frame(8_000, index) for index in range(8, 11)]

    backend = FakeBackend()
    manager = SessionManager()
    segments = list(
        manager.iter_voice_segments(
            detector_mode="rms_quieting",
            frequency_hz=462_712_500,
            backend=backend,
            min_segment_ms=100,
            hang_time_ms=120,
            max_segments=1,
        )
    )
    assert len(segments) == 1
    assert backend.stopped
    assert manager.status.process_running is False


def test_manager_voice_iterator_preserves_backend_exit_diagnostic():
    class FailedBackend:
        sample_rate_hz = SAMPLE_RATE

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def is_running(self) -> bool:
            return False

        def read_stderr_lines(self) -> list[str]:
            return ["usb_claim_interface error -6"]

        def read_frames(self) -> list[PcmAudioFrame]:
            return []

    manager = SessionManager()
    assert list(manager.iter_voice_segments(frequency_hz=462_712_500, backend=FailedBackend())) == []
    assert manager.status.error == "usb_claim_interface error -6"


def test_manager_auto_poll_reports_voice_status_and_segments():
    class LiveBackend:
        sample_rate_hz = SAMPLE_RATE

        def __init__(self) -> None:
            self.stopped = False
            self._read = False

        def start(self) -> None:
            pass

        def stop(self) -> None:
            self.stopped = True

        def is_running(self) -> bool:
            return not self.stopped

        def read_stderr_lines(self) -> list[str]:
            return []

        def read_frames(self) -> list[PcmAudioFrame]:
            if self._read:
                return []
            self._read = True
            return [_frame(8_000, index) for index in range(5)] + [
                _frame(100, index) for index in range(5, 8)
            ] + [_frame(8_000, index) for index in range(8, 11)]

    manager = SessionManager()
    backend = LiveBackend()
    callback_events = []
    callback_segments = []
    manager.start_voice_segments(
        detector_mode="rms_quieting",
        frequency_hz=462_712_500,
        backend=backend,
        min_segment_ms=100,
        hang_time_ms=120,
        auto_poll=True,
        poll_interval_sec=0.001,
        on_event=callback_events.append,
        on_segment=callback_segments.append,
    )
    deadline = monotonic() + 1.0
    segments = []
    while monotonic() < deadline and not segments:
        segments.extend(manager.pop_voice_segments())
        sleep(0.01)

    status = manager.current_voice_segment_status()
    assert manager.voice_capture_running()
    assert status is not None
    assert status.capture_running
    assert status.last_frame_rms_db is not None
    assert len(segments) == 1
    assert [event.event for event in manager.pop_voice_events()] == [
        "capture_started",
        "transmission_started",
        "transmission_ended",
    ]
    assert [event.event for event in callback_events] == [
        "capture_started",
        "transmission_started",
        "transmission_ended",
    ]
    assert callback_segments == segments
    manager.stop()
    assert backend.stopped
    assert callback_events[-1].event == "capture_stopped"


# --- carrier-gate runaway ---------------------------------------------------
#
# Regression cover for the confirmed defect: speech is often louder than hiss, so
# it read as carrier-absent, reached the noise-floor estimator, and inflated it.
# Ordinary hiss then fell below the threshold, the gate latched open, and
# max_segment_sec only chopped the result -- the floor stayed frozen and the
# carrier-absent branch became unreachable, so the gate re-armed on the next hiss
# frame. Measured before the fix: nine back-to-back max-length hiss segments, a
# 100% duty cycle.

def _noise_frame(rng, index, amplitude=0.10):
    samples = rng.normal(0.0, amplitude, FRAME_SAMPLES)
    return PcmAudioFrame(
        pcm_s16le=(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes(),
        sample_rate_hz=SAMPLE_RATE,
        captured_at=utc_now() + timedelta(milliseconds=40 * index),
    )


def _voice_frame(index, amplitude):
    n = np.arange(FRAME_SAMPLES) + index * FRAME_SAMPLES
    samples = amplitude * (
        np.sin(2 * np.pi * 350 * n / SAMPLE_RATE)
        + 0.6 * np.sin(2 * np.pi * 700 * n / SAMPLE_RATE)
    ) / 1.6
    return PcmAudioFrame(
        pcm_s16le=(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes(),
        sample_rate_hz=SAMPLE_RATE,
        captured_at=utc_now() + timedelta(milliseconds=40 * index),
    )


def _run_inflation_scenario(detector_mode, voice_amplitude):
    """Calibrate on hiss, transmit, then feed pure hiss and see what escapes."""
    rng = np.random.default_rng(7)
    segmenter = _segmenter(max_segment_sec=2.0, detector_mode=detector_mode)
    emitted, index = [], 0
    for _ in range(50):
        emitted.extend(segmenter.ingest(_noise_frame(rng, index)))
        index += 1
    for _ in range(80):
        emitted.extend(segmenter.ingest(_voice_frame(index, voice_amplitude)))
        index += 1
    quiet_started_at = utc_now() + timedelta(milliseconds=40 * index)
    for _ in range(400):
        emitted.extend(segmenter.ingest(_noise_frame(rng, index)))
        index += 1
    stray = sum(s.duration_sec for s in emitted if s.started_at >= quiet_started_at)
    return emitted, stray / (400 * 0.04)


@pytest.mark.parametrize("detector_mode", ["rms_quieting", "hf_ratio", "hybrid"])
@pytest.mark.parametrize("voice_amplitude", [0.45, 0.02])
def test_hiss_does_not_run_away_after_a_loud_transmission(detector_mode, voice_amplitude):
    _emitted, duty = _run_inflation_scenario(detector_mode, voice_amplitude)

    # Acceptance criterion 2 as a duty cycle, not a segment length: the old
    # "no segment reaches max_segment_sec" wording would have been satisfied by
    # lowering the cap while a 100%-duty-cycle train of hiss segments continued.
    assert duty < 0.05


def test_quiet_transmission_is_still_captured():
    """The FM-quieting path that matches the project's real captures still works."""
    emitted, _duty = _run_inflation_scenario("rms_quieting", 0.02)

    assert emitted, "a quieter-than-hiss transmission must still produce a segment"


def test_capped_close_on_noise_refuses_to_re_arm_until_recalibrated():
    rng = np.random.default_rng(3)
    segmenter = _segmenter(max_segment_sec=0.4, detector_mode="rms_quieting")
    for index in range(20):
        segmenter.ingest(_noise_frame(rng, index, amplitude=0.30))
    for index in range(20, 60):
        segmenter.ingest(_noise_frame(rng, index, amplitude=0.02))

    status = segmenter.status()
    if status.capped_closes:
        # A capped close on hiss-like audio must drop the floor it opened on.
        assert status.recalibrating or status.noise_floor_db is not None


def test_noise_floor_recovers_while_the_gate_is_held_open():
    """The floor must be re-estimable during an active segment.

    Before the fix the estimator was reachable only from the carrier-absent
    branch, which inflation made unreachable by construction -- a dead end with no
    way back.
    """
    rng = np.random.default_rng(11)
    segmenter = _segmenter(max_segment_sec=30.0, hang_time_ms=10_000)
    for index in range(30):
        segmenter.ingest(_noise_frame(rng, index))
    established = segmenter.status().noise_floor_db
    for index in range(30, 200):
        segmenter.ingest(_noise_frame(rng, index))

    assert segmenter.status().noise_floor_db is not None
    assert abs(segmenter.status().noise_floor_db - established) < 6.0


def test_noise_floor_drift_is_rate_limited():
    rng = np.random.default_rng(5)
    segmenter = _segmenter(max_floor_drift_db_per_sec=6.0)
    for index in range(20):
        segmenter.ingest(_noise_frame(rng, index, amplitude=0.10))
    before = segmenter.status().noise_floor_db
    segmenter.ingest(_noise_frame(rng, 20, amplitude=0.9))
    after = segmenter.status().noise_floor_db

    # 6 dB/s over a 40 ms frame is 0.24 dB, so a ~19 dB jump cannot land at once.
    assert abs(after - before) <= 6.0 * 0.04 + 1e-6


def test_noise_floor_can_still_reach_a_legitimate_new_level():
    """Rate-limited, not destination-clamped.

    A hard clamp anchored to the bootstrap value meant a capture started during a
    transmission anchored to the wrong level and could never climb back: the
    threshold sat far below any real hiss, the gate never opened, and nothing
    reported an error. Bounding speed instead leaves every level reachable.
    """
    rng = np.random.default_rng(31)
    segmenter = _segmenter(max_floor_drift_db_per_sec=6.0)
    # Bootstrap on a quiet frame, as if capture began mid-transmission.
    segmenter.ingest(_noise_frame(rng, 0, amplitude=0.002))
    anchored = segmenter.status().noise_floor_db
    for index in range(1, 400):
        segmenter.ingest(_noise_frame(rng, index, amplitude=0.10))
    recovered = segmenter.status().noise_floor_db

    assert recovered > anchored + 10.0, "floor never climbed back to the true hiss level"


def test_capture_started_mid_transmission_still_hears_later_traffic():
    rng = np.random.default_rng(37)
    segmenter = _segmenter(max_floor_drift_db_per_sec=6.0, detector_mode="rms_quieting")
    for index in range(20):
        segmenter.ingest(_voice_frame(index, 0.02))          # started mid-call
    for index in range(20, 300):
        segmenter.ingest(_noise_frame(rng, index, 0.10))     # idle hiss returns
    emitted = []
    for index in range(300, 340):
        emitted.extend(segmenter.ingest(_voice_frame(index, 0.005)))
    for index in range(340, 380):
        emitted.extend(segmenter.ingest(_noise_frame(rng, index, 0.10)))

    assert emitted, "receiver went permanently deaf after anchoring to a bad floor"


def test_capped_segment_reports_the_floor_it_was_gated_against():
    rng = np.random.default_rng(41)
    segmenter = _segmenter(max_segment_sec=0.4, min_segment_ms=100)
    for index in range(20):
        segmenter.ingest(_noise_frame(rng, index, amplitude=0.30))
    emitted = []
    for index in range(20, 80):
        emitted.extend(segmenter.ingest(_noise_frame(rng, index, amplitude=0.004)))

    for segment in emitted:
        # Stamping the segment's own RMS would make rms_db - noise_floor_db zero
        # for exactly the segments a consumer wants to flag as noise.
        assert segment.noise_floor_db != segment.rms_db


def test_hf_ratio_does_not_open_on_digital_silence():
    """Band ratio alone is a "not hissy" test, not a "signal present" test.

    Silence scores 0.0, which is below the voice threshold, so without an
    absolute-level term the gate opens on a dead carrier.
    """
    silence = PcmAudioFrame(
        pcm_s16le=np.zeros(FRAME_SAMPLES, dtype="<i2").tobytes(),
        sample_rate_hz=SAMPLE_RATE,
        captured_at=utc_now(),
    )
    rng = np.random.default_rng(43)
    for mode in ("rms_quieting", "hf_ratio", "hybrid"):
        segmenter = _segmenter(detector_mode=mode, min_segment_ms=100)
        for index in range(10):
            segmenter.ingest(_noise_frame(rng, index))
        emitted = []
        for _ in range(150):
            emitted.extend(segmenter.ingest(silence))
        assert not emitted, f"{mode} opened the gate on digital silence"


# --- chain order ------------------------------------------------------------

def test_conditioning_does_not_move_the_detector_decision():
    """Detector reads raw PCM; conditioning only shapes emitted audio.

    Under any other ordering, de-emphasis (a low-pass) would shift the hf_ratio
    boundary the detector rests on, so toggling one knob would silently retune
    another.
    """
    rng = np.random.default_rng(13)
    frames = [_noise_frame(rng, i) for i in range(40)]

    plain = _segmenter(detector_mode="hf_ratio")
    conditioned = _segmenter(
        detector_mode="hf_ratio",
        conditioner=AudioConditioner(sample_rate_hz=SAMPLE_RATE, deemphasis_us=75.0),
    )
    for frame in frames:
        plain.ingest(frame)
        conditioned.ingest(frame)

    assert plain.status().last_frame_band_ratio == conditioned.status().last_frame_band_ratio


def test_deemphasis_attenuates_the_high_band_in_the_conditioned_domain():
    """Acceptance criterion 1's measurement: conditioned-domain fields must move."""
    rng = np.random.default_rng(17)
    pcm = _noise_frame(rng, 0).pcm_s16le
    before = band_energy_ratio(pcm, SAMPLE_RATE)
    conditioner = AudioConditioner(sample_rate_hz=SAMPLE_RATE, deemphasis_us=75.0)
    after = band_energy_ratio(conditioner.process(pcm), SAMPLE_RATE)

    assert after < before


def test_live_time_constant_change_preserves_filter_state():
    conditioner = AudioConditioner(sample_rate_hz=SAMPLE_RATE, deemphasis_us=75.0)
    rng = np.random.default_rng(19)
    conditioner.process(_noise_frame(rng, 0).pcm_s16le)
    carried = conditioner._deemph_prev
    conditioner.update(deemphasis_us=50.0)

    # Re-initialising a running IIR would click at exactly the moment an operator
    # is judging audio quality.
    assert conditioner._deemph_prev == carried
    assert conditioner.deemphasis_alpha is not None


def test_conditioning_settings_are_range_validated():
    conditioner = AudioConditioner(sample_rate_hz=SAMPLE_RATE)
    with pytest.raises(ValueError):
        conditioner.update(deemphasis_us=0)
    with pytest.raises(ValueError):
        conditioner.update(normalize_target_dbfs=6.0)
    with pytest.raises(ValueError):
        _segmenter(detector_mode="telepathy")


# --- respawn ----------------------------------------------------------------

class _StubBackend:
    """Minimal PcmAudioBackend that replays a fixed frame list."""

    def __init__(self, frames):
        self.sample_rate_hz = SAMPLE_RATE
        self._frames = list(frames)
        self._running = False

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    def is_running(self):
        return self._running

    def read_stderr_lines(self):
        return []

    def read_frames(self):
        frames, self._frames = self._frames, []
        return frames


def test_respawn_preserves_unpopped_segments_and_run_counters():
    """start_voice_segments() clears both buffers and zeroes counters.

    A respawn that went straight through it would silently discard completed
    transmissions mid-capture run -- data loss, not a cosmetic reset. The
    guarantee lives in SessionManager rather than in a UI so every consumer gets
    it.
    """
    rng = np.random.default_rng(23)
    frames = [_noise_frame(rng, i, amplitude=0.30) for i in range(15)]
    frames += [_noise_frame(rng, 15 + i, amplitude=0.01) for i in range(15)]
    frames += [_noise_frame(rng, 30 + i, amplitude=0.30) for i in range(20)]

    manager = SessionManager()
    manager.start_voice_segments(
        detector_mode="rms_quieting",
        frequency_hz=462_712_500, backend=_StubBackend(frames), min_segment_ms=100
    )
    manager.poll()
    before = manager.current_voice_segment_status()
    pending = len(manager._voice_segments)

    manager.restart_voice_capture(backend=_StubBackend([]), gain_db=28.0)

    after = manager.current_voice_segment_status()
    assert len(manager._voice_segments) == pending, "unpopped segments were discarded"
    assert after.completed_segments == before.completed_segments
    assert after.dropped_segments == before.dropped_segments


def test_respawn_is_refused_during_an_active_segment_without_wedging_it():
    """The refusal must not disable the remedy it recommends.

    Stopping the auto-poller before the active-segment check meant a refusal
    killed frame consumption, so the segment could never end, so the caller could
    never reach the state where a respawn is allowed. The session wedged on its
    own guard.
    """
    rng = np.random.default_rng(29)
    opening = [_noise_frame(rng, i, amplitude=0.30) for i in range(15)]
    opening += [_noise_frame(rng, 15 + i, amplitude=0.004) for i in range(8)]
    closing = [_noise_frame(rng, 30 + i, amplitude=0.30) for i in range(30)]

    backend = _StubBackend(opening)
    manager = SessionManager()
    manager.start_voice_segments(
        detector_mode="rms_quieting",
        frequency_hz=462_712_500, backend=backend, hang_time_ms=200, min_segment_ms=100
    )
    manager.poll()
    assert manager.current_voice_segment_status().active, "scenario failed to open the gate"

    with pytest.raises(RuntimeError, match="active segment"):
        manager.restart_voice_capture(gain_db=28.0)

    # The transmission must still be able to end after the refusal.
    backend._frames = list(closing)
    manager.poll()
    assert not manager.current_voice_segment_status().active
    manager.restart_voice_capture(backend=_StubBackend([]), gain_db=28.0)


def test_failed_respawn_restores_the_backlog():
    rng = np.random.default_rng(47)
    frames = [_noise_frame(rng, i, amplitude=0.30) for i in range(15)]
    frames += [_noise_frame(rng, 15 + i, amplitude=0.004) for i in range(10)]
    frames += [_noise_frame(rng, 25 + i, amplitude=0.30) for i in range(20)]

    manager = SessionManager()
    manager.start_voice_segments(
        detector_mode="rms_quieting",
        frequency_hz=462_712_500, backend=_StubBackend(frames), min_segment_ms=100
    )
    manager.poll()
    pending = len(manager._voice_segments)

    with pytest.raises(Exception):
        manager.restart_voice_capture(backend="not-a-backend")

    assert len(manager._voice_segments) == pending, "backlog dropped by a failed respawn"


def test_concurrent_live_tuning_while_the_poller_ingests():
    """Adversarial interleaving across the lock boundary.

    poll() holds _poll_lock across ingest_voice_segments(), and
    update_voice_segment_settings() takes the same lock, so a live knob change can
    never land mid-frame. This exercises that rather than assuming it.
    """
    from threading import Event as _Event, Thread as _Thread

    rng = np.random.default_rng(53)
    frames = [_noise_frame(rng, i, amplitude=0.30) for i in range(400)]
    backend = _StubBackend(frames)
    manager = SessionManager()
    manager.start_voice_segments(
        detector_mode="rms_quieting",
        frequency_hz=462_712_500, backend=backend, auto_poll=True, poll_interval_sec=0.001
    )

    stop, errors = _Event(), []

    def hammer():
        modes = ["rms_quieting", "hf_ratio", "hybrid"]
        index = 0
        while not stop.is_set():
            try:
                manager.update_voice_segment_settings(
                    detector_mode=modes[index % 3],
                    threshold_db=6.0 + (index % 5),
                    deemphasis_us=50.0 + (index % 30),
                    dc_block=bool(index % 2),
                )
                manager.current_voice_segment_status()
                manager.pop_voice_segments()
            except Exception as exc:  # noqa: BLE001 - the point is to catch anything
                errors.append(exc)
                return
            index += 1

    threads = [_Thread(target=hammer, daemon=True) for _ in range(3)]
    for thread in threads:
        thread.start()
    sleep(0.4)
    stop.set()
    for thread in threads:
        thread.join(timeout=2.0)
    manager.stop()

    assert not errors, f"concurrent tuning raised: {errors[:3]}"


def test_refused_respawn_leaves_the_auto_poller_running():
    """The refusal path must restore what it stopped.

    The poller is now stopped *before* the active check, to close a check-then-act
    window where a transmission starting in between would be truncated. That makes
    restarting it on the refusal path load-bearing: without it the refusal is the
    wedge it replaced.
    """
    rng = np.random.default_rng(59)
    frames = [_noise_frame(rng, i, amplitude=0.30) for i in range(15)]
    frames += [_noise_frame(rng, 15 + i, amplitude=0.004) for i in range(200)]

    manager = SessionManager()
    manager.start_voice_segments(
        detector_mode="rms_quieting",
        frequency_hz=462_712_500,
        backend=_StubBackend(frames),
        hang_time_ms=10_000,
        auto_poll=True,
        poll_interval_sec=0.005,
    )
    # Wait for the precondition, then assert it. Returning early instead would
    # make the test silently vacuous the moment gate tuning shifts -- the same
    # pattern removed from the sibling respawn test.
    deadline = monotonic() + 2.0
    while monotonic() < deadline and not manager.current_voice_segment_status().active:
        sleep(0.01)
    assert manager.current_voice_segment_status().active, "scenario failed to open the gate"

    with pytest.raises(RuntimeError, match="active segment"):
        manager.restart_voice_capture(gain_db=28.0)

    assert manager._voice_poll_thread is not None, "poller not restarted after refusal"
    assert manager._voice_poll_thread.is_alive()
    manager.stop()


def test_silence_floor_is_live_tunable():
    rng = np.random.default_rng(61)
    manager = SessionManager()
    manager.start_voice_segments(
        detector_mode="rms_quieting",
        frequency_hz=462_712_500,
        backend=_StubBackend([_noise_frame(rng, i) for i in range(5)]),
    )
    manager.poll()
    manager.update_voice_segment_settings(silence_floor_db=-45.0)

    assert manager._voice_segmenter.silence_floor_db == -45.0
    with pytest.raises(ValueError):
        manager.update_voice_segment_settings(silence_floor_db=3.0)


def test_hybrid_is_a_union_not_an_intersection():
    """Either detector may open the gate.

    As an intersection, hybrid inherited rms_quieting's blind spot: on hardware
    whose voice audio is louder than the idle hiss, that detector never fires, so
    AND captured nothing at all. The union catches a transmission that either
    detector recognises, which is the only behaviour that makes the mode more
    useful than its parts rather than less.
    """
    loud, _duty = _run_inflation_scenario("hybrid", 0.45)
    quiet, _duty = _run_inflation_scenario("hybrid", 0.02)

    # Under AND, `loud` was empty: hf_ratio saw the voice, rms_quieting did not,
    # and the intersection lost it.
    assert loud, "hybrid missed a transmission that hf_ratio alone would catch"
    assert quiet, "hybrid missed a transmission that rms_quieting alone would catch"


def test_hf_ratio_is_the_default_detector():
    # Changed after live testing: rms_quieting captured nothing on real hardware.
    segmenter = RadioVoiceSegmenter(
        session_id="s", frequency_hz=1, modulation="NFM", sample_rate_hz=SAMPLE_RATE
    )
    assert segmenter.detector_mode == "hf_ratio"
