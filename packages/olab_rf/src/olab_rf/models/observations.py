from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from olab_rf.models.tracks import dt_from_iso, dt_to_iso, utc_now


@dataclass(slots=True)
class Observation:
    observation_id: str
    sensor_id: str
    session_id: str
    protocol: str
    domain: str
    timestamp: datetime = field(default_factory=utc_now)
    track_id: str | None = None
    raw: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "sensor_id": self.sensor_id,
            "session_id": self.session_id,
            "protocol": self.protocol,
            "domain": self.domain,
            "timestamp": dt_to_iso(self.timestamp),
            "track_id": self.track_id,
            "raw": self.raw,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Observation:
        return cls(
            observation_id=payload["observation_id"],
            sensor_id=payload["sensor_id"],
            session_id=payload["session_id"],
            protocol=payload["protocol"],
            domain=payload["domain"],
            timestamp=dt_from_iso(payload.get("timestamp")) or utc_now(),
            track_id=payload.get("track_id"),
            raw=payload.get("raw"),
            metadata=dict(payload.get("metadata") or {}),
        )
