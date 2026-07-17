from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def dt_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def dt_from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(slots=True)
class Track:
    track_id: str
    domain: str
    protocol: str
    lat: float
    lon: float
    source_sensor: str
    label: str | None = None
    altitude_m: float | None = None
    speed_mps: float | None = None
    course_deg: float | None = None
    heading_deg: float | None = None
    first_seen: datetime = field(default_factory=utc_now)
    last_seen: datetime = field(default_factory=utc_now)
    stale_after_s: float = 60.0
    expire_after_s: float = 300.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def age_s(self, now: datetime | None = None) -> float:
        return ((now or utc_now()) - self.last_seen).total_seconds()

    def state(self, now: datetime | None = None) -> str:
        age = self.age_s(now)
        if age >= self.expire_after_s:
            return "expired"
        if age >= self.stale_after_s:
            return "stale"
        return "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "domain": self.domain,
            "protocol": self.protocol,
            "label": self.label,
            "lat": self.lat,
            "lon": self.lon,
            "altitude_m": self.altitude_m,
            "speed_mps": self.speed_mps,
            "course_deg": self.course_deg,
            "heading_deg": self.heading_deg,
            "source_sensor": self.source_sensor,
            "first_seen": dt_to_iso(self.first_seen),
            "last_seen": dt_to_iso(self.last_seen),
            "stale_after_s": self.stale_after_s,
            "expire_after_s": self.expire_after_s,
            "metadata": self.metadata,
            "state": self.state(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Track:
        return cls(
            track_id=payload["track_id"],
            domain=payload["domain"],
            protocol=payload["protocol"],
            label=payload.get("label"),
            lat=float(payload["lat"]),
            lon=float(payload["lon"]),
            altitude_m=payload.get("altitude_m"),
            speed_mps=payload.get("speed_mps"),
            course_deg=payload.get("course_deg"),
            heading_deg=payload.get("heading_deg"),
            source_sensor=payload["source_sensor"],
            first_seen=dt_from_iso(payload.get("first_seen")) or utc_now(),
            last_seen=dt_from_iso(payload.get("last_seen")) or utc_now(),
            stale_after_s=float(payload.get("stale_after_s", 60.0)),
            expire_after_s=float(payload.get("expire_after_s", 300.0)),
            metadata=dict(payload.get("metadata") or {}),
        )
