from __future__ import annotations

from typing import Any

from olab_rf.models import Observation, SensorStatus, Track

SCHEMA_VERSION = 1


def envelope(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "payload": payload}


def track_to_payload(track: Track) -> dict[str, Any]:
    return envelope("track", track.to_dict())


def track_from_payload(payload: dict[str, Any]) -> Track:
    return Track.from_dict(payload["payload"])


def observation_to_payload(observation: Observation) -> dict[str, Any]:
    return envelope("observation", observation.to_dict())


def status_to_payload(status: SensorStatus) -> dict[str, Any]:
    return envelope("status", status.to_dict())
