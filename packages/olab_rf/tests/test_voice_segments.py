from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from time import monotonic, sleep
import wave

import numpy as np

from olab_rf.models import PcmAudioFrame, RadioVoiceSegment
from olab_rf.models.tracks import utc_now
from olab_rf.services.session_manager import SessionManager
from olab_rf.services.voice_segments import RadioVoiceSegmenter, RtlFmAudioBackend


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
        squelch_db=7,
    )

    assert backend.frame_bytes == 1280
    assert backend.command == [
        "rtl_fm-test", "-d", "0", "-f", "462712500", "-M", "fm", "-s", "16000",
        "-g", "19.7", "-l", "7",
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
