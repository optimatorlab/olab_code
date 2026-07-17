from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any, AsyncIterator, Literal, Protocol

from olab_voice.audio.models import AudioBlob, AudioFrame


TranscriptEventType = Literal[
    "transcript.hypothesis",
    "transcript.segment_final",
    "transcript.interval_final",
]


@dataclass(slots=True)
class TranscriptEvent:
    """Transcript result emitted by STT backends or sessions."""

    text: str
    type: TranscriptEventType = "transcript.segment_final"
    session_id: str | None = None
    user_id: int = 0
    asset_id: int = 0
    confidence: float | None = None
    start_time: float | None = None
    end_time: float | None = None
    segment_id: str | None = None
    revision: int = 0
    engine: str | None = None
    is_fallback: bool = False
    capture_start_time: float | None = None
    capture_end_time: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "type": self.type,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "asset_id": self.asset_id,
            "confidence": self.confidence,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "segment_id": self.segment_id,
            "revision": self.revision,
            "engine": self.engine,
            "is_fallback": self.is_fallback,
            "capture_start_time": self.capture_start_time,
            "capture_end_time": self.capture_end_time,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TranscriptEvent":
        return cls(
            text=data["text"],
            type=data.get("type", "transcript.segment_final"),
            session_id=data.get("session_id"),
            user_id=data.get("user_id", 0),
            asset_id=data.get("asset_id", 0),
            confidence=data.get("confidence"),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            segment_id=data.get("segment_id"),
            revision=data.get("revision", 0),
            engine=data.get("engine"),
            is_fallback=data.get("is_fallback", False),
            capture_start_time=data.get("capture_start_time"),
            capture_end_time=data.get("capture_end_time"),
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", time()),
        )


class BatchTranscriber(Protocol):
    """Transcribes a complete utterance into one final transcript event."""

    async def transcribe(self, audio: AudioBlob) -> TranscriptEvent:
        ...


class StreamingTranscriber(Protocol):
    """Consumes live PCM frames and asynchronously emits transcript events."""

    async def start(self) -> None:
        """Initialize the backend before frames are submitted."""
        ...

    async def submit_frame(self, frame: AudioFrame) -> None:
        """Submit one ordered live-audio frame without blocking capture."""
        ...

    def events(self) -> AsyncIterator[TranscriptEvent]:
        """Yield transcript hypotheses and finals until the backend stops."""
        ...

    async def stop(self, *, flush: bool = True) -> None:
        """Stop processing and optionally emit results buffered so far."""
        ...
