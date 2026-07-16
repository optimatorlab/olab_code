from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any, Literal
from uuid import uuid4


AudioSource = Literal["browser", "python_mic", "file", "radio", "unknown"]


@dataclass(slots=True)
class AudioBlob:
    """Complete audio payload, typically from push-to-talk or a file."""

    data: bytes
    format: str
    session_id: str = field(default_factory=lambda: str(uuid4()))
    source: AudioSource = "unknown"
    user_id: int = 0
    asset_id: int = 0
    sample_rate: int | None = None
    channels: int | None = None
    timestamp: float = field(default_factory=time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "format": self.format,
            "session_id": self.session_id,
            "source": self.source,
            "user_id": self.user_id,
            "asset_id": self.asset_id,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AudioBlob":
        return cls(
            data=data["data"],
            format=data["format"],
            session_id=data.get("session_id") or str(uuid4()),
            source=data.get("source", "unknown"),
            user_id=data.get("user_id", 0),
            asset_id=data.get("asset_id", 0),
            sample_rate=data.get("sample_rate"),
            channels=data.get("channels"),
            timestamp=data.get("timestamp", time()),
        )


@dataclass(slots=True)
class AudioFrame:
    """Small live audio frame, typically 20-40 ms of PCM."""

    data: bytes
    format: str = "pcm_s16le"
    sample_rate: int = 16000
    channels: int = 1
    seq: int = 0
    session_id: str = field(default_factory=lambda: str(uuid4()))
    source: AudioSource = "unknown"
    user_id: int = 0
    asset_id: int = 0
    timestamp: float = field(default_factory=time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "format": self.format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "seq": self.seq,
            "session_id": self.session_id,
            "source": self.source,
            "user_id": self.user_id,
            "asset_id": self.asset_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AudioFrame":
        return cls(
            data=data["data"],
            format=data.get("format", "pcm_s16le"),
            sample_rate=data.get("sample_rate", 16000),
            channels=data.get("channels", 1),
            seq=data.get("seq", 0),
            session_id=data.get("session_id") or str(uuid4()),
            source=data.get("source", "unknown"),
            user_id=data.get("user_id", 0),
            asset_id=data.get("asset_id", 0),
            timestamp=data.get("timestamp", time()),
        )
