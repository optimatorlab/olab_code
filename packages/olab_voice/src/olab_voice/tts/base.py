from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


TtsOutputMode = Literal["server_playback", "browser_playback", "publish_only"]


@dataclass(slots=True)
class TtsRequest:
    text: str
    session_id: str | None = None
    user_id: int = 0
    asset_id: int = 0
    voice: str | None = None
    output: TtsOutputMode = "browser_playback"
    format: str = "wav"
    preempt: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "asset_id": self.asset_id,
            "voice": self.voice,
            "output": self.output,
            "format": self.format,
            "preempt": self.preempt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TtsRequest":
        return cls(
            text=data["text"],
            session_id=data.get("session_id"),
            user_id=data.get("user_id", 0),
            asset_id=data.get("asset_id", 0),
            voice=data.get("voice"),
            output=data.get("output", "browser_playback"),
            format=data.get("format", "wav"),
            preempt=data.get("preempt", False),
        )


@dataclass(slots=True)
class TtsAudio:
    data: bytes
    format: str
    sample_rate: int | None = None
    channels: int | None = None
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "format": self.format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TtsAudio":
        return cls(
            data=data["data"],
            format=data["format"],
            sample_rate=data.get("sample_rate"),
            channels=data.get("channels"),
            session_id=data.get("session_id"),
        )


class SpeechSynthesizer(Protocol):
    async def synthesize(self, request: TtsRequest) -> TtsAudio:
        ...
