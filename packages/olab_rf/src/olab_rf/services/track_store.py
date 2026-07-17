from __future__ import annotations

from dataclasses import dataclass, field

from olab_rf.models import Track


@dataclass(slots=True)
class TrackStore:
    tracks: dict[str, Track] = field(default_factory=dict)
    trails: dict[str, list[dict[str, object]]] = field(default_factory=dict)

    def upsert(self, track: Track) -> Track:
        existing = self.tracks.get(track.track_id)
        if existing:
            track.first_seen = existing.first_seen
        self.tracks[track.track_id] = track
        self.trails.setdefault(track.track_id, []).append(
            {
                "timestamp": track.last_seen.isoformat(),
                "lat": track.lat,
                "lon": track.lon,
                "altitude_m": track.altitude_m,
            }
        )
        return track

    def list(self, include_expired: bool = False) -> list[Track]:
        if include_expired:
            return list(self.tracks.values())
        return [track for track in self.tracks.values() if track.state() != "expired"]

    def get(self, track_id: str) -> Track | None:
        return self.tracks.get(track_id)

    def trail_for(self, track_id: str) -> list[dict[str, object]]:
        return list(self.trails.get(track_id, []))

    def clear(self) -> None:
        self.tracks.clear()
        self.trails.clear()
