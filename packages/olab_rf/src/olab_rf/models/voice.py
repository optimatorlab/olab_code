from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
import wave

from olab_rf.models.tracks import dt_to_iso, utc_now


@dataclass(frozen=True, slots=True)
class PcmAudioFrame:
    """One mono signed-16-bit little-endian PCM frame from a voice backend."""

    pcm_s16le: bytes
    sample_rate_hz: int
    captured_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class RadioVoiceSegment:
    """A completed radio transmission, retained as PCM for efficient handoff."""

    segment_id: str
    session_id: str
    frequency_hz: int
    modulation: str
    sample_rate_hz: int
    pcm_s16le: bytes
    started_at: datetime
    ended_at: datetime
    rms_db: float
    peak_db: float
    noise_floor_db: float
    threshold_db: float
    channels: int = 1
    wav_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        return len(self.pcm_s16le) / (self.sample_rate_hz * self.channels * 2)

    def to_wav_bytes(self) -> bytes:
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate_hz)
            wav.writeframes(self.pcm_s16le)
        return output.getvalue()

    def save_wav(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.to_wav_bytes())
        return destination

    def to_dict(self, *, include_audio: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "segment_id": self.segment_id,
            "session_id": self.session_id,
            "frequency_hz": self.frequency_hz,
            "modulation": self.modulation,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "started_at": dt_to_iso(self.started_at),
            "ended_at": dt_to_iso(self.ended_at),
            "duration_sec": self.duration_sec,
            "rms_db": self.rms_db,
            "peak_db": self.peak_db,
            "noise_floor_db": self.noise_floor_db,
            "threshold_db": self.threshold_db,
            "wav_path": self.wav_path,
            "metadata": self.metadata,
        }
        if include_audio:
            payload["pcm_s16le"] = self.pcm_s16le
        return payload

    def to_audio_blob_payload(self) -> dict[str, Any]:
        """Return the current ``olab_voice.AudioBlob.from_dict`` input shape."""
        return {
            "data": self.to_wav_bytes(),
            "format": "audio/wav",
            "session_id": self.session_id,
            "source": "radio",
            "sample_rate": self.sample_rate_hz,
            "channels": self.channels,
            "timestamp": self.started_at.timestamp(),
            "user_id": 0,
            "asset_id": 0,
        }


@dataclass(frozen=True, slots=True)
class VoiceSegmentStatus:
    session_id: str
    active: bool
    sample_rate_hz: int
    capture_running: bool = False
    state: str = "stopped"
    noise_floor_db: float | None = None
    threshold_db: float | None = None
    last_frame_rms_db: float | None = None
    last_frame_peak_db: float | None = None
    last_frame_at: datetime | None = None
    active_duration_sec: float = 0.0
    completed_segments: int = 0
    dropped_segments: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "active": self.active,
            "capture_running": self.capture_running,
            "state": self.state,
            "sample_rate_hz": self.sample_rate_hz,
            "noise_floor_db": self.noise_floor_db,
            "threshold_db": self.threshold_db,
            "last_frame_rms_db": self.last_frame_rms_db,
            "last_frame_peak_db": self.last_frame_peak_db,
            "last_frame_at": dt_to_iso(self.last_frame_at),
            "active_duration_sec": self.active_duration_sec,
            "completed_segments": self.completed_segments,
            "dropped_segments": self.dropped_segments,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class VoiceCaptureEvent:
    """A lifecycle transition emitted by a voice-segment capture session."""

    event: str
    session_id: str
    state: str
    occurred_at: datetime = field(default_factory=utc_now)
    segment_id: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "session_id": self.session_id,
            "state": self.state,
            "occurred_at": dt_to_iso(self.occurred_at),
            "segment_id": self.segment_id,
            "message": self.message,
        }
