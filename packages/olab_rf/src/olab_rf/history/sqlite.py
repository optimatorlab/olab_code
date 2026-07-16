from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from olab_rf.models import Observation, Track
from olab_rf.models.scanning import FrequencyScanStatus
from olab_rf.models.spectrum import SpectrumEvent


class SqliteHistory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            create table if not exists tracks (
                track_id text primary key,
                payload text not null,
                last_seen text not null
            );
            create table if not exists trail_points (
                id integer primary key autoincrement,
                track_id text not null,
                timestamp text not null,
                lat real not null,
                lon real not null,
                altitude_m real
            );
            create table if not exists observations (
                observation_id text primary key,
                payload text not null,
                timestamp text not null
            );
            create table if not exists spectrum_events (
                id integer primary key autoincrement,
                payload text not null,
                timestamp text not null,
                frequency_hz integer not null,
                preset_id text not null
            );
            create table if not exists frequency_favorites (
                frequency_hz integer primary key,
                label text,
                modulation text not null,
                created_at text not null
            );
            create table if not exists frequency_scans (
                scan_id text primary key,
                payload text not null,
                status text not null,
                started_at text not null,
                stopped_at text,
                min_freq_hz integer not null,
                max_freq_hz integer not null,
                best_frequency_hz integer
            );
            """
        )
        self.connection.commit()

    def upsert_track(self, track: Track) -> None:
        payload = json.dumps(track.to_dict(), sort_keys=True)
        self.connection.execute(
            """
            insert into tracks (track_id, payload, last_seen)
            values (?, ?, ?)
            on conflict(track_id) do update set
                payload = excluded.payload,
                last_seen = excluded.last_seen
            """,
            (track.track_id, payload, track.last_seen.isoformat()),
        )
        self.connection.execute(
            """
            insert into trail_points (track_id, timestamp, lat, lon, altitude_m)
            values (?, ?, ?, ?, ?)
            """,
            (track.track_id, track.last_seen.isoformat(), track.lat, track.lon, track.altitude_m),
        )
        self.connection.commit()

    def add_observation(self, observation: Observation) -> None:
        self.connection.execute(
            """
            insert or replace into observations (observation_id, payload, timestamp)
            values (?, ?, ?)
            """,
            (
                observation.observation_id,
                json.dumps(observation.to_dict(), sort_keys=True),
                observation.timestamp.isoformat(),
            ),
        )
        self.connection.commit()

    def list_tracks(self) -> list[Track]:
        rows = self.connection.execute("select payload from tracks order by last_seen desc").fetchall()
        return [Track.from_dict(json.loads(row["payload"])) for row in rows]

    def trail_for(self, track_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            select timestamp, lat, lon, altitude_m
            from trail_points
            where track_id = ?
            order by timestamp
            """,
            (track_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_track_dicts(self, limit: int = 200) -> list[dict[str, object]]:
        rows = self.connection.execute(
            "select payload from tracks order by last_seen desc limit ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def add_spectrum_event(self, event: SpectrumEvent) -> None:
        self.connection.execute(
            """
            insert into spectrum_events (payload, timestamp, frequency_hz, preset_id)
            values (?, ?, ?, ?)
            """,
            (
                json.dumps(event.to_dict(), sort_keys=True),
                event.captured_at.isoformat(),
                event.center_hz,
                event.preset_id,
            ),
        )
        self.connection.commit()

    def list_spectrum_events(self, limit: int = 200) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            select payload
            from spectrum_events
            order by timestamp desc, id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def add_frequency_scan(self, scan: FrequencyScanStatus) -> None:
        payload = scan.to_dict()
        request = scan.request
        best = scan.best_candidate
        self.connection.execute(
            """
            insert into frequency_scans (
                scan_id, payload, status, started_at, stopped_at,
                min_freq_hz, max_freq_hz, best_frequency_hz
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(scan_id) do update set
                payload = excluded.payload,
                status = excluded.status,
                stopped_at = excluded.stopped_at,
                best_frequency_hz = excluded.best_frequency_hz
            """,
            (
                scan.scan_id,
                json.dumps(payload, sort_keys=True),
                scan.status,
                scan.started_at.isoformat(),
                scan.stopped_at.isoformat() if scan.stopped_at else None,
                request.min_freq_hz,
                request.max_freq_hz,
                best.frequency_hz if best else None,
            ),
        )
        self.connection.commit()

    def list_frequency_scans(self, limit: int = 200) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            select payload
            from frequency_scans
            order by started_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_frequency_scan(self, scan_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "select payload from frequency_scans where scan_id = ?",
            (scan_id,),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def upsert_frequency_favorite(
        self,
        *,
        frequency_hz: int,
        modulation: str,
        label: str | None = None,
    ) -> None:
        from olab_rf.models.tracks import utc_now

        self.connection.execute(
            """
            insert into frequency_favorites (frequency_hz, label, modulation, created_at)
            values (?, ?, ?, ?)
            on conflict(frequency_hz) do update set
                label = excluded.label,
                modulation = excluded.modulation
            """,
            (frequency_hz, label, modulation, utc_now().isoformat()),
        )
        self.connection.commit()

    def list_frequency_favorites(self) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            select frequency_hz, label, modulation, created_at
            from frequency_favorites
            order by created_at desc
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_frequency_favorite(self, frequency_hz: int) -> None:
        self.connection.execute(
            "delete from frequency_favorites where frequency_hz = ?",
            (frequency_hz,),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
