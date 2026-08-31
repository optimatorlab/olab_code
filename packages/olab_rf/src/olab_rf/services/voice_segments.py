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
    """PCM frame source backed by ``rtl_fm`` stdout.

    Deliberately exposes no squelch. The carrier gate detects FM *quieting* -- the
    drop in hiss when a signal appears -- so ``rtl_fm -l`` would remove the very
    signal the gate measures. Squelch on this path is the gate's job, where it is
    also live-tunable. ``rtl_fm_command`` still accepts ``squelch_db`` for
    scan-mode callers, which genuinely require it.
    """

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
        fir_size: int | None = None,
        atan_math: str | None = None,
        extra_args: list[str] | None = None,
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
            fir_size=fir_size,
            atan_math=atan_math,
            extra_args=extra_args,
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


class AudioConditioner:
    """DC blocking and de-emphasis for emitted audio, run continuously per frame.

    Runs on *every* frame, including frames the gate discards, so the IIR state
    stays warm. Only the output is selectively retained. A filter applied at
    segment close instead would inject a step transient into the first
    ``pre_roll_ms`` of every segment via the pre-roll splice.

    The carrier detector never sees this output -- it measures raw PCM -- so
    toggling conditioning cannot move a detector threshold. That separation is
    what keeps de-emphasis and detector mode independently tunable.
    """

    def __init__(
        self,
        *,
        sample_rate_hz: int,
        dc_block: bool = True,
        deemphasis_us: float | None = 75.0,
        normalize: bool = False,
        normalize_target_dbfs: float = -20.0,
    ) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.dc_block = dc_block
        self.deemphasis_us = deemphasis_us
        self.normalize = normalize
        self.normalize_target_dbfs = normalize_target_dbfs
        self._dc_prev_in = 0.0
        self._dc_prev_out = 0.0
        self._deemph_prev = 0.0

    @property
    def deemphasis_alpha(self) -> float | None:
        if not self.deemphasis_us:
            return None
        tau = self.deemphasis_us * 1e-6
        return float(1.0 - np.exp(-1.0 / (self.sample_rate_hz * tau)))

    def update(
        self,
        *,
        dc_block: bool | None = None,
        deemphasis_us: float | None = None,
        normalize: bool | None = None,
        normalize_target_dbfs: float | None = None,
        clear_deemphasis: bool = False,
    ) -> None:
        """Change coefficients in place, preserving filter state.

        State is deliberately *not* reset: re-initialising a running IIR produces
        an audible click at exactly the moment an operator is judging quality.
        """
        if dc_block is not None:
            self.dc_block = dc_block
        if clear_deemphasis:
            self.deemphasis_us = None
        elif deemphasis_us is not None:
            if deemphasis_us <= 0:
                raise ValueError("deemphasis_us must be greater than zero")
            self.deemphasis_us = deemphasis_us
        if normalize is not None:
            self.normalize = normalize
        if normalize_target_dbfs is not None:
            if normalize_target_dbfs > 0:
                raise ValueError("normalize_target_dbfs must be at or below 0 dBFS")
            self.normalize_target_dbfs = normalize_target_dbfs

    def process(self, pcm_s16le: bytes) -> bytes:
        samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float64) / 32768.0
        if not len(samples):
            return pcm_s16le
        if self.dc_block:
            samples = self._apply_dc_block(samples)
        alpha = self.deemphasis_alpha
        if alpha is not None:
            samples = self._apply_deemphasis(samples, alpha)
        return _to_pcm_bytes(samples)

    def normalize_segment(self, pcm_s16le: bytes) -> bytes:
        """Apply whole-segment gain, if enabled.

        Normalization is a scalar gain with no filter state, so it is applied once
        over the finished segment rather than per frame -- per-frame gain would
        pump audibly across a transmission.
        """
        if not self.normalize:
            return pcm_s16le
        samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float64) / 32768.0
        if not len(samples):
            return pcm_s16le
        rms = float(np.sqrt(np.mean(np.square(samples))))
        if rms <= 1e-9:
            return pcm_s16le
        target = 10.0 ** (self.normalize_target_dbfs / 20.0)
        return _to_pcm_bytes(samples * (target / rms))

    def _apply_dc_block(self, samples: np.ndarray) -> np.ndarray:
        out = np.empty_like(samples)
        prev_in, prev_out = self._dc_prev_in, self._dc_prev_out
        for index, value in enumerate(samples):
            prev_out = value - prev_in + 0.995 * prev_out
            prev_in = value
            out[index] = prev_out
        self._dc_prev_in, self._dc_prev_out = prev_in, prev_out
        return out

    def _apply_deemphasis(self, samples: np.ndarray, alpha: float) -> np.ndarray:
        out = np.empty_like(samples)
        prev = self._deemph_prev
        for index, value in enumerate(samples):
            prev = prev + alpha * (value - prev)
            out[index] = prev
        self._deemph_prev = prev
        return out


DETECTOR_MODES = ("rms_quieting", "hf_ratio", "hybrid")


def band_energy_ratio(pcm_s16le: bytes, sample_rate_hz: int) -> float:
    """Return 2-6 kHz energy over 300-2000 Hz energy.

    Hiss is high-frequency; voice is not. Measured over the project's fifteen
    reference captures this separates the populations (0.27-0.85 speech,
    2.07-2.99 hiss) where broadband RMS does not.
    """
    samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float64) / 32768.0
    if len(samples) < 8:
        return 0.0
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), 1.0 / sample_rate_hz)
    high = float(spectrum[(freqs >= 2000) & (freqs < 6000)].sum())
    low = float(spectrum[(freqs >= 300) & (freqs < 2000)].sum())
    return high / max(low, 1e-9)


def _to_pcm_bytes(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


class RadioVoiceSegmenter:
    """Turn generic PCM frames into conservative, STT-ready voice segments.

    Chain order is load-bearing: the carrier detector measures **raw** frames,
    while the audio that is emitted is **conditioned**. Under any other ordering,
    toggling de-emphasis live would move the ``hf_ratio`` decision boundary and
    output normalization would flatten ``rms_quieting`` into a coin flip.
    """

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
        detector_mode: str = "hf_ratio",
        hf_ratio_threshold: float = 1.2,
        max_floor_drift_db_per_sec: float = 6.0,
        recalibration_ms: int = 1_000,
        silence_floor_db: float = -60.0,
        conditioner: "AudioConditioner | None" = None,
    ) -> None:
        if detector_mode not in DETECTOR_MODES:
            raise ValueError(f"detector_mode must be one of {list(DETECTOR_MODES)}")
        self.session_id = session_id
        self.frequency_hz = frequency_hz
        self.modulation = modulation
        self.sample_rate_hz = sample_rate_hz
        self.frame_ms = frame_ms
        self.threshold_db = threshold_db
        self.detector_mode = detector_mode
        self.hf_ratio_threshold = hf_ratio_threshold
        self.max_floor_drift_db_per_sec = max_floor_drift_db_per_sec
        self.silence_floor_db = silence_floor_db
        self.min_active_frames = max(1, -(-min_active_ms // frame_ms))
        self.hang_frames = max(1, -(-hang_time_ms // frame_ms))
        self.min_segment_bytes = sample_rate_hz * 2 * min_segment_ms // 1000
        self.max_segment_bytes = int(sample_rate_hz * 2 * max_segment_sec)
        self.recalibration_frames = max(1, -(-recalibration_ms // frame_ms))
        self.conditioner = conditioner or AudioConditioner(sample_rate_hz=sample_rate_hz)
        self._pre_roll: Deque[tuple[PcmAudioFrame, bytes]] = deque(
            maxlen=max(1, -(-pre_roll_ms // frame_ms))
        )
        self._candidate: list[tuple[PcmAudioFrame, bytes]] = []
        self._active_frames: list[tuple[PcmAudioFrame, bytes]] = []
        self._below_frames = 0
        self._noise_floor_db: float | None = None
        self._last_frame_rms_db: float | None = None
        self._last_frame_peak_db: float | None = None
        self._last_frame_band_ratio: float | None = None
        self._last_frame_at = None
        self._completed = 0
        self._dropped = 0
        self._capped_closes = 0
        self._recalibrating = False
        self._calibration_frames = 0

    def ingest(self, frame: PcmAudioFrame) -> list[RadioVoiceSegment]:
        if frame.sample_rate_hz != self.sample_rate_hz:
            raise ValueError("PCM frame sample rate does not match segmenter")
        rms_db, peak_db = _pcm_levels(frame.pcm_s16le)
        band_ratio = band_energy_ratio(frame.pcm_s16le, self.sample_rate_hz)
        self._last_frame_rms_db = rms_db
        self._last_frame_peak_db = peak_db
        self._last_frame_band_ratio = band_ratio
        self._last_frame_at = frame.captured_at

        # Conditioning runs on every frame, including discarded ones, so IIR state
        # stays warm and no step transient enters the pre-roll splice.
        entry = (frame, self.conditioner.process(frame.pcm_s16le))

        # Floor updates come only from hiss-like frames, and happen whether or not
        # the gate is open. Both halves matter: the first stops speech (which is
        # often louder than hiss) from inflating the estimate, and the second gives
        # the floor a recovery path while the gate is held open. Without recovery,
        # an inflated floor makes the carrier-absent branch unreachable by
        # construction and the gate re-arms on hiss forever.
        if self._noise_floor_db is None:
            # Bootstrap: with no floor at all there is nothing to protect yet, and
            # requiring hiss-like audio to establish the first estimate would leave
            # a receiver whose idle output is not high-frequency unable to
            # calibrate at all.
            self._update_noise_floor(rms_db)
        elif band_ratio >= self.hf_ratio_threshold:
            self._update_noise_floor(rms_db)

        emitted: list[RadioVoiceSegment] = []

        if self._recalibrating:
            self._calibration_frames += 1
            if (
                self._calibration_frames >= self.recalibration_frames
                and self._noise_floor_db is not None
            ):
                self._recalibrating = False
            self._pre_roll.append(entry)
            return emitted

        carrier_present = self._carrier_present(rms_db, band_ratio)

        if not self._active_frames:
            self._pre_roll.append(entry)
            if carrier_present:
                self._candidate.append(entry)
                if len(self._candidate) >= self.min_active_frames:
                    self._active_frames = list(self._pre_roll)
                    self._candidate.clear()
                    self._below_frames = 0
            else:
                self._candidate.clear()
            return emitted

        self._active_frames.append(entry)
        if carrier_present:
            self._below_frames = 0
        else:
            self._below_frames += 1
        capped = self._active_byte_count >= self.max_segment_bytes
        if self._below_frames >= self.hang_frames or capped:
            segment = self._close(capped=capped)
            if segment:
                emitted.append(segment)
        return emitted

    def _carrier_present(self, rms_db: float, band_ratio: float) -> bool:
        quiet = (
            self._noise_floor_db is not None
            and rms_db < self._noise_floor_db - self.threshold_db
        )
        # Band ratio alone is a "not hissy" test, not a "signal present" one:
        # digital silence scores 0.0 and would read as voice. Require audible
        # content alongside it.
        audible = rms_db > self.silence_floor_db
        voice_like = audible and band_ratio < self.hf_ratio_threshold
        if self.detector_mode == "rms_quieting":
            return quiet
        if self.detector_mode == "hf_ratio":
            return voice_like
        # `hybrid` is OR, not AND. AND is only useful when both detectors work
        # independently, and rms_quieting cannot fire at all on a radio whose
        # voice audio is louder than the idle hiss -- measured on live hardware,
        # where AND inherited that failure and captured nothing. OR is the
        # union: either detector may open the gate.
        return quiet or voice_like

    @property
    def _active_byte_count(self) -> int:
        return sum(len(entry[0].pcm_s16le) for entry in self._active_frames)

    def status(self, *, error: str | None = None) -> VoiceSegmentStatus:
        return VoiceSegmentStatus(
            session_id=self.session_id,
            active=bool(self._active_frames),
            sample_rate_hz=self.sample_rate_hz,
            noise_floor_db=self._noise_floor_db,
            threshold_db=(
                self._noise_floor_db - self.threshold_db
                if self._noise_floor_db is not None
                else None
            ),
            last_frame_rms_db=self._last_frame_rms_db,
            last_frame_peak_db=self._last_frame_peak_db,
            last_frame_at=self._last_frame_at,
            active_duration_sec=self._active_byte_count / (self.sample_rate_hz * 2),
            completed_segments=self._completed,
            dropped_segments=self._dropped,
            error=error,
            detector_mode=self.detector_mode,
            # Raw-domain: this is what the detector sees, not the emitted audio.
            last_frame_band_ratio=self._last_frame_band_ratio,
            recalibrating=self._recalibrating,
            capped_closes=self._capped_closes,
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
        detector_mode: str | None = None,
        hf_ratio_threshold: float | None = None,
        max_floor_drift_db_per_sec: float | None = None,
        silence_floor_db: float | None = None,
        dc_block: bool | None = None,
        deemphasis_us: float | None = None,
        disable_deemphasis: bool = False,
        normalize: bool | None = None,
        normalize_target_dbfs: float | None = None,
    ) -> None:
        """Apply safe gate and conditioning changes to frames received after this call."""
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
        if detector_mode is not None:
            if detector_mode not in DETECTOR_MODES:
                raise ValueError(f"detector_mode must be one of {list(DETECTOR_MODES)}")
            self.detector_mode = detector_mode
        if hf_ratio_threshold is not None:
            if hf_ratio_threshold <= 0:
                raise ValueError("hf_ratio_threshold must be greater than zero")
            self.hf_ratio_threshold = hf_ratio_threshold
        if max_floor_drift_db_per_sec is not None:
            if max_floor_drift_db_per_sec <= 0:
                raise ValueError("max_floor_drift_db_per_sec must be greater than zero")
            self.max_floor_drift_db_per_sec = max_floor_drift_db_per_sec
        if silence_floor_db is not None:
            if silence_floor_db > 0:
                raise ValueError("silence_floor_db must be at or below 0 dBFS")
            self.silence_floor_db = silence_floor_db
        self.conditioner.update(
            dc_block=dc_block,
            deemphasis_us=deemphasis_us,
            normalize=normalize,
            normalize_target_dbfs=normalize_target_dbfs,
            clear_deemphasis=disable_deemphasis,
        )

    def carry_counters(self, *, completed: int, dropped: int, capped_closes: int) -> None:
        """Seed run totals from a previous segmenter, so a respawn does not zero them."""
        self._completed = completed
        self._dropped = dropped
        self._capped_closes = capped_closes

    def reset_calibration(self) -> None:
        """Discard the inactive-level estimate before a materially new RF environment."""
        if self._active_frames:
            raise RuntimeError("cannot reset carrier calibration during an active segment")
        self._noise_floor_db = None
        self._candidate.clear()
        self._pre_roll.clear()
        self._recalibrating = False
        self._calibration_frames = 0

    def _frames_for_ms(self, value: int, name: str) -> int:
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")
        return max(1, -(-value // self.frame_ms))

    def _update_noise_floor(self, value: float) -> None:
        """Track the idle level, rate-limited rather than hard-clamped.

        An earlier version clamped drift to a window around the *bootstrap* value.
        That bounded the destination, not the speed, so a capture started during a
        transmission anchored to the wrong level and could never climb back --
        permanently deaf, with no error. Limiting dB-per-second instead keeps a
        sustained misread slow (the hiss gate is the primary defence) while
        leaving every legitimate level reachable.
        """
        if self._noise_floor_db is None:
            self._noise_floor_db = value
            return
        updated = self._noise_floor_db * 0.95 + value * 0.05
        max_step = self.max_floor_drift_db_per_sec * (self.frame_ms / 1000.0)
        delta = max(-max_step, min(max_step, updated - self._noise_floor_db))
        self._noise_floor_db += delta

    def _close(self, *, capped: bool = False) -> RadioVoiceSegment | None:
        entries, self._active_frames = self._active_frames, []
        self._below_frames = 0
        self._pre_roll.clear()
        if not entries:
            return None
        raw_pcm = b"".join(entry[0].pcm_s16le for entry in entries)
        # Captured before any recalibration reset below, so the segment reports the
        # floor it was actually gated against rather than its own RMS.
        gated_floor_db = self._noise_floor_db
        if capped:
            self._capped_closes += 1
            # Distinguish the two reasons a segment can hit the cap. Hiss-like
            # content means the gate opened on noise, so the floor is not to be
            # trusted: drop it and hold the gate shut until a fresh one exists.
            # Without that, the gate reopens on the next hiss frame and emits
            # max-length noise segments back to back at a 100% duty cycle.
            # Voice-like content is just a transmission longer than the cap, which
            # is legitimate -- recalibrating there would punish a long call with a
            # gate lockout and discard a floor that was correct all along.
            if band_energy_ratio(raw_pcm, self.sample_rate_hz) >= self.hf_ratio_threshold:
                self._noise_floor_db = None
                self._recalibrating = True
                self._calibration_frames = 0
        if len(raw_pcm) < self.min_segment_bytes:
            self._dropped += 1
            return None
        conditioned_pcm = self.conditioner.normalize_segment(
            b"".join(entry[1] for entry in entries)
        )
        rms_db, peak_db = _pcm_levels(raw_pcm)
        conditioned_rms_db, conditioned_peak_db = _pcm_levels(conditioned_pcm)
        self._completed += 1
        return RadioVoiceSegment(
            segment_id=f"segment-{uuid4()}",
            session_id=self.session_id,
            frequency_hz=self.frequency_hz,
            modulation=self.modulation,
            sample_rate_hz=self.sample_rate_hz,
            pcm_s16le=conditioned_pcm,
            started_at=entries[0][0].captured_at,
            ended_at=entries[-1][0].captured_at + timedelta(milliseconds=self.frame_ms),
            rms_db=rms_db,
            peak_db=peak_db,
            noise_floor_db=(gated_floor_db if gated_floor_db is not None else rms_db),
            threshold_db=self.threshold_db,
            conditioned_rms_db=conditioned_rms_db,
            conditioned_peak_db=conditioned_peak_db,
            conditioned_band_ratio=band_energy_ratio(conditioned_pcm, self.sample_rate_hz),
        )


def _pcm_levels(pcm_s16le: bytes) -> tuple[float, float]:
    samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0
    if not len(samples):
        return -120.0, -120.0
    rms = max(float(np.sqrt(np.mean(np.square(samples)))), 1e-6)
    peak = max(float(np.max(np.abs(samples))), 1e-6)
    return float(20 * np.log10(rms)), float(20 * np.log10(peak))
