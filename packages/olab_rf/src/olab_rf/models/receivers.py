from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ReceiverConfig:
    id: str
    type: str = "rtlsdr"
    serial: str | None = None
    ppm: int = 0
    gain: str | float = "auto"
    antenna: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "serial": self.serial,
            "ppm": self.ppm,
            "gain": self.gain,
            "antenna": self.antenna,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReceiverConfig:
        return cls(
            id=payload["id"],
            type=payload.get("type", "rtlsdr"),
            serial=payload.get("serial"),
            ppm=int(payload.get("ppm", 0)),
            gain=payload.get("gain", "auto"),
            antenna=payload.get("antenna"),
            metadata=dict(payload.get("metadata") or {}),
        )
