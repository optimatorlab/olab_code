from __future__ import annotations

from collections import deque
from datetime import timedelta
from typing import Deque, Protocol
from uuid import uuid4

import numpy as np

from olab_rf.decoders.process import DecoderProcess
from olab_rf.decoders.rtl_fm import rtl_fm_command
from olab_rf.models.voice import PcmAudioFrame, RadioVoiceSegment, VoiceSegmentStatus


class PcmAudioBackend(Protocol):
    sample_rate_hz: int

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def read_frames(self) -> list[PcmAudioFrame]: ...
    def read_stderr_lines(self) -> list[str]: ...


class RtlFmAudioBackend:
    """PCM frame source backed by ``rtl_fm`` stdout."""

    def __init__(
        self,
        *,
        path: str,
        frequency_hz: int,
        modulation: str,
        sample_rate_hz: int,
        frame_ms: int,
        device_index: int = 0,
        ppm: int | None = None,
        gain_db: float | None = None,
        squelch_db: int | None = None,
    ) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.frame_bytes = sample_rate_hz * frame_ms // 1000 * 2
        self.command = rtl_fm_command(
            path=path,
            frequency_hz=frequency_hz,
            modulation=modulation,
            device_index=device_index,
            ppm=ppm,
            gain_db=gain_db,
            sample_rate_hz=sample_rate_hz,
            squelch_db=squelch_db,
        )
        self._process = DecoderProcess(command=self.command, binary_stdout=True)
        self._buffer = bytearray()

    def start(self) -> None:
        self._process.start()

    def stop(self) -> None:
        self._process.stop()

    def is_running(self) -> bool:
        return self._process.is_running()

    def read_stderr_lines(self) -> list[str]:
        return self._process.read_stderr_lines()

    def read_frames(self) -> list[PcmAudioFrame]:
        self._buffer.extend(self._process.read_stdout_bytes())
        frames: list[PcmAudioFrame] = []
        while len(self._buffer) >= self.frame_bytes:
            pcm = bytes(self._buffer[: self.frame_bytes])
            del self._buffer[: self.frame_bytes]
            frames.append(PcmAudioFrame(pcm_s16le=pcm, sample_rate_hz=self.sample_rate_hz))
        return frames


class RadioVoiceSegmenter:
    """Turn generic PCM frames into conservative, STT-ready voice segments."""

    def __init__(
        self,
        *,
        session_id: str,
        frequency_hz: int,
        modulation: str,
        sample_rate_hz: int = 16_000,
        frame_ms: int = 40,
        threshold_db: float = 10.0,
        min_active_ms: int = 120,
        hang_time_ms: int = 600,
        min_segment_ms: int = 400,
        max_segment_sec: float = 20.0,
        pre_roll_ms: int = 200,
    ) -> None:
        self.session_id = session_id
        self.frequency_hz = frequency_hz
        self.modulation = modulation
        self.sample_rate_hz = sample_rate_hz
        self.frame_ms = frame_ms
        self.threshold_db = threshold_db
        self.min_active_frames = max(1, -(-min_active_ms // frame_ms))
        self.hang_frames = max(1, -(-hang_time_ms // frame_ms))
        self.min_segment_bytes = sample_rate_hz * 2 * min_segment_ms // 1000
        self.max_segment_bytes = int(sample_rate_hz * 2 * max_segment_sec)
        self._pre_roll: Deque[PcmAudioFrame] = deque(maxlen=max(1, -(-pre_roll_ms // frame_ms)))
        self._candidate: list[PcmAudioFrame] = []
        self._active_frames: list[PcmAudioFrame] = []
        self._below_frames = 0
        self._noise_floor_db: float | None = None
        self._last_frame_rms_db: float | None = None
        self._last_frame_peak_db: float | None = None
        self._last_frame_at = None
        self._completed = 0
        self._dropped = 0

    def ingest(self, frame: PcmAudioFrame) -> list[RadioVoiceSegment]:
        if frame.sample_rate_hz != self.sample_rate_hz:
            raise ValueError("PCM frame sample rate does not match segmenter")
        rms_db, peak_db = _pcm_levels(frame.pcm_s16le)
        self._last_frame_rms_db = rms_db
        self._last_frame_peak_db = peak_db
        self._last_frame_at = frame.captured_at
        threshold = (self._noise_floor_db if self._noise_floor_db is not None else rms_db) - self.threshold_db
        carrier_present = rms_db < threshold
        emitted: list[RadioVoiceSegment] = []
        if not self._active_frames:
            self._pre_roll.append(frame)
            if carrier_present:
                self._candidate.append(frame)
                if len(self._candidate) >= self.min_active_frames:
                    self._active_frames = list(self._pre_roll)
                    self._candidate.clear()
                    self._below_frames = 0
            else:
                self._update_noise_floor(rms_db)
                self._candidate.clear()
            return emitted

        self._active_frames.append(frame)
        if carrier_present:
            self._below_frames = 0
        else:
            self._below_frames += 1
        if self._below_frames >= self.hang_frames or self._active_byte_count >= self.max_segment_bytes:
            segment = self._close()
            if segment:
                emitted.append(segment)
        return emitted

    @property
    def _active_byte_count(self) -> int:
        return sum(len(frame.pcm_s16le) for frame in self._active_frames)

    def status(self, *, error: str | None = None) -> VoiceSegmentStatus:
        return VoiceSegmentStatus(
            session_id=self.session_id,
            active=bool(self._active_frames),
            sample_rate_hz=self.sample_rate_hz,
            noise_floor_db=self._noise_floor_db,
            threshold_db=(self._noise_floor_db - self.threshold_db) if self._noise_floor_db is not None else None,
            last_frame_rms_db=self._last_frame_rms_db,
            last_frame_peak_db=self._last_frame_peak_db,
            last_frame_at=self._last_frame_at,
            active_duration_sec=self._active_byte_count / (self.sample_rate_hz * 2),
            completed_segments=self._completed,
            dropped_segments=self._dropped,
            error=error,
        )

    def update_settings(
        self,
        *,
        threshold_db: float | None = None,
        min_active_ms: int | None = None,
        hang_time_ms: int | None = None,
        min_segment_ms: int | None = None,
        max_segment_sec: float | None = None,
        pre_roll_ms: int | None = None,
    ) -> None:
        """Apply safe gate changes to frames received after this call."""
        if threshold_db is not None:
            if threshold_db < 0:
                raise ValueError("threshold_db must be non-negative")
            self.threshold_db = threshold_db
        if min_active_ms is not None:
            self.min_active_frames = self._frames_for_ms(min_active_ms, "min_active_ms")
        if hang_time_ms is not None:
            self.hang_frames = self._frames_for_ms(hang_time_ms, "hang_time_ms")
        if min_segment_ms is not None:
            if min_segment_ms <= 0:
                raise ValueError("min_segment_ms must be greater than zero")
            self.min_segment_bytes = self.sample_rate_hz * 2 * min_segment_ms // 1000
        if max_segment_sec is not None:
            if max_segment_sec <= 0:
                raise ValueError("max_segment_sec must be greater than zero")
            self.max_segment_bytes = int(self.sample_rate_hz * 2 * max_segment_sec)
        if pre_roll_ms is not None:
            maxlen = self._frames_for_ms(pre_roll_ms, "pre_roll_ms")
            self._pre_roll = deque(self._pre_roll, maxlen=maxlen)

    def reset_calibration(self) -> None:
        """Discard the inactive-level estimate before a materially new RF environment."""
        if self._active_frames:
            raise RuntimeError("cannot reset carrier calibration during an active segment")
        self._noise_floor_db = None
        self._candidate.clear()
        self._pre_roll.clear()

    def _frames_for_ms(self, value: int, name: str) -> int:
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")
        return max(1, -(-value // self.frame_ms))

    def _update_noise_floor(self, value: float) -> None:
        self._noise_floor_db = value if self._noise_floor_db is None else self._noise_floor_db * 0.95 + value * 0.05

    def _close(self) -> RadioVoiceSegment | None:
        frames, self._active_frames = self._active_frames, []
        self._below_frames = 0
        self._pre_roll.clear()
        if not frames:
            return None
        pcm = b"".join(frame.pcm_s16le for frame in frames)
        if len(pcm) < self.min_segment_bytes:
            self._dropped += 1
            return None
        rms_db, peak_db = _pcm_levels(pcm)
        self._completed += 1
        started_at = frames[0].captured_at
        return RadioVoiceSegment(
            segment_id=f"segment-{uuid4()}",
            session_id=self.session_id,
            frequency_hz=self.frequency_hz,
            modulation=self.modulation,
            sample_rate_hz=self.sample_rate_hz,
            pcm_s16le=pcm,
            started_at=started_at,
            ended_at=frames[-1].captured_at + timedelta(milliseconds=self.frame_ms),
            rms_db=rms_db,
            peak_db=peak_db,
            noise_floor_db=self._noise_floor_db if self._noise_floor_db is not None else rms_db,
            threshold_db=self.threshold_db,
        )


def _pcm_levels(pcm_s16le: bytes) -> tuple[float, float]:
    samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0
    if not len(samples):
        return -120.0, -120.0
    rms = max(float(np.sqrt(np.mean(np.square(samples)))), 1e-6)
    peak = max(float(np.max(np.abs(samples))), 1e-6)
    return float(20 * np.log10(rms)), float(20 * np.log10(peak))
