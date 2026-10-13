from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from olab_rf.decoders.base import DecodedMessage
from olab_rf.decoders.remoteid_base import RemoteIdBackend, RemoteIdBroadcast
from olab_rf.history import SqliteHistory
from olab_rf.models import Observation, RemoteIdTransportEvent, Track
from olab_rf.models.tracks import utc_now
from olab_rf.services.track_store import TrackStore

_POSITION_EPSILON_DEG = 1e-6
_ALTITUDE_EPSILON_M = 0.5

_Key = tuple[str, str, str]  # (backend.name, backend.transport, mac)


class RemoteIdManager:
    """Receives Remote ID broadcasts from independent WiFi/BLE backends and
    merges them into `olab_rf`'s `Track` model.

    Standalone, independent of `SessionManager`'s one-RTL-SDR exclusive
    lifecycle -- composed alongside it by a consumer that wants "one shared
    map", not routed through it. `track_store` is a required, externally
    owned dependency for exactly that reason: a `cuas_practice` consumer
    hands this the same `TrackStore` instance `SessionManager` already
    exposes. `history` is optional, mirroring `SessionManager`'s own field.

    Track identity: one merged `Track` per drone
    (`track_id = f"remoteid-{serial}"`), regardless of which transport(s)
    currently see it. Per-transport contribution is recorded in
    `track.metadata["transports"]`. Explicit `transport_acquired`/
    `transport_lost`/`identity_resolved` lifecycle events are emitted per
    `(backend, transport, mac)` key -- independent of the `Track`'s own
    `state()`, which only moves to `stale`/`expired` once *both* transports
    go quiet (achieved by always refreshing `track.last_seen` from either
    transport's activity, with no special-casing needed in `Track` itself).
    """

    def __init__(
        self,
        track_store: TrackStore,
        backends: Sequence[RemoteIdBackend],
        *,
        session_id: str,
        sensor_id: str,
        transport_timeout_s: float = 8.0,
        stale_after_s: float = 60.0,
        expire_after_s: float = 300.0,
        history: SqliteHistory | None = None,
    ) -> None:
        transports_seen: set[str] = set()
        for backend in backends:
            if backend.transport in transports_seen:
                raise ValueError(
                    f"Duplicate Remote ID transport {backend.transport!r}: "
                    "at most one backend per transport is supported"
                )
            transports_seen.add(backend.transport)

        self.track_store = track_store
        self.backends: list[RemoteIdBackend] = list(backends)
        self.session_id = session_id
        self.sensor_id = sensor_id
        self.transport_timeout_s = transport_timeout_s
        self.stale_after_s = stale_after_s
        self.expire_after_s = expire_after_s
        self.history = history

        self._mac_to_serial: dict[_Key, str] = {}
        self._pending: dict[_Key, RemoteIdBroadcast] = {}
        self._last_activity: dict[_Key, datetime] = {}
        self._live_keys: set[_Key] = set()
        self._last_history_upsert: dict[str, datetime] = {}
        self._transport_events: list[RemoteIdTransportEvent] = []

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Start every backend. Fail-fast with rollback: if a backend's
        `start()` raises, every backend already started this call is
        stopped (in reverse order) before re-raising, so a partial failure
        never leaves some backends running with no way for the caller to
        know without checking each one individually."""
        started: list[RemoteIdBackend] = []
        try:
            for backend in self.backends:
                backend.start()
                started.append(backend)
        except Exception:
            for backend in reversed(started):
                backend.stop()
            raise

    def stop(self) -> None:
        """Idempotent: stopping when nothing is running, or twice in a row,
        is a no-op (each backend's own `stop()` already guards that)."""
        for backend in self.backends:
            backend.stop()

    def is_running(self) -> bool:
        """True if any backend is running. See `backend_status()` for a
        per-backend breakdown -- this coarse check can't tell you *which*
        backend is unhealthy if only one of two is down."""
        return any(backend.is_running() for backend in self.backends)

    def backend_status(self) -> list[dict[str, object]]:
        """Current per-backend health: `{"name", "transport", "running",
        "error"}` for each configured backend. A bare `dict`, matching the
        existing `TrackStore.trail_for(...) -> list[dict[...]]` precedent --
        each entry is a small, fixed, read-only snapshot with no behavior of
        its own."""
        return [
            {
                "name": backend.name,
                "transport": backend.transport,
                "running": backend.is_running(),
                "error": getattr(backend, "error", None),
            }
            for backend in self.backends
        ]

    def pop_transport_events(self) -> list[RemoteIdTransportEvent]:
        events, self._transport_events = self._transport_events, []
        return events

    # -- polling ---------------------------------------------------------

    def poll(self, *, now: datetime | None = None) -> list[DecodedMessage]:
        """Drain every backend and merge broadcasts into `Track`s.

        `now` is an optional injected clock (same parameter shape as
        `Track.age_s(now)`/`Track.state(now)`) so tests can control elapsed
        time deterministically instead of sleeping real seconds to exercise
        `transport_timeout_s`/`stale_after_s`.
        """
        now = now or utc_now()
        messages: list[DecodedMessage] = []
        for backend in self.backends:
            for broadcast in backend.poll():
                messages.append(self._handle_broadcast(backend, broadcast, now))
        self._sweep(now)
        return messages

    def _handle_broadcast(
        self, backend: RemoteIdBackend, broadcast: RemoteIdBroadcast, now: datetime
    ) -> DecodedMessage:
        # Liveness key: normally the transmitter's MAC, but a broadcast can
        # carry a serial with no MAC at all (droneid_go._parse_item only
        # rejects a message when *both* are absent). Falling back to `key =
        # None` in that case would let _apply_track_update still write
        # metadata["transports"][...] (it doesn't need a key) while skipping
        # the entire acquired/lost/evict machinery -- a permanently-stale
        # "contributing transport" entry with no way to ever mark it lost.
        # A serial-keyed surrogate lets the normal machinery apply uniformly.
        if broadcast.mac:
            key: _Key | None = (backend.name, backend.transport, broadcast.mac)
        elif broadcast.serial:
            key = (backend.name, backend.transport, f"serial:{broadcast.serial}")
        else:
            key = None
        # Whether this key had any prior activity at all -- used below to
        # tell "a brand-new key whose very first broadcast already carries a
        # serial" (just a normal acquisition, no separate resolution event)
        # apart from "a previously-pending key getting its serial resolved
        # for the first time" (the actual identity_resolved case).
        existed_before = key is not None and key in self._last_activity

        if key is not None:
            self._last_activity[key] = now
            if key not in self._live_keys:
                self._live_keys.add(key)
                self._emit_event(
                    "transport_acquired",
                    backend_name=backend.name,
                    transport=backend.transport,
                    mac=broadcast.mac,
                    serial=broadcast.serial,
                    now=now,
                )

        serial = broadcast.serial
        lat, lon = broadcast.lat, broadcast.lon
        altitude_m, speed_mps, course_deg = (
            broadcast.altitude_m,
            broadcast.speed_mps,
            broadcast.course_deg,
        )

        resolved_now = False
        if key is not None:
            if serial is None:
                serial = self._mac_to_serial.get(key)
            elif key not in self._mac_to_serial and existed_before:
                resolved_now = True

            if serial is not None:
                self._mac_to_serial[key] = serial
                pending = self._pending.pop(key, None)
                if pending is not None:
                    if lat is None and lon is None:
                        lat, lon = pending.lat, pending.lon
                    if altitude_m is None:
                        altitude_m = pending.altitude_m
                    if speed_mps is None:
                        speed_mps = pending.speed_mps
                    if course_deg is None:
                        course_deg = pending.course_deg
            else:
                # Not yet attributable to any serial -- buffer it (replacing
                # any previous entry for this key) so a later serial-bearing
                # frame for the same mac can fold its position in, instead of
                # silently dropping the information.
                self._pending[key] = broadcast

        if resolved_now:
            self._emit_event(
                "identity_resolved",
                backend_name=backend.name,
                transport=backend.transport,
                mac=broadcast.mac,
                serial=serial,
                now=now,
            )

        if serial is None:
            return self._track_less_message(backend, broadcast, now, serial=None)

        if lat is None or lon is None:
            # Identity known, but no position has been seen yet from either
            # this broadcast or a previously buffered one for this key --
            # don't fabricate a Track position (Track.lat/lon have no valid
            # "unknown" sentinel of their own).
            return self._track_less_message(backend, broadcast, now, serial=serial)

        return self._apply_track_update(
            backend, now, serial, lat, lon, altitude_m, speed_mps, course_deg
        )

    def _apply_track_update(
        self,
        backend: RemoteIdBackend,
        now: datetime,
        serial: str,
        lat: float,
        lon: float,
        altitude_m: float | None,
        speed_mps: float | None,
        course_deg: float | None,
    ) -> DecodedMessage:
        track_id = f"remoteid-{serial}"
        # Re-resolved from the store every time, never cached across polls:
        # a consumer sharing this TrackStore with SessionManager can call
        # TrackStore.clear() at any time, and re-resolving means the very
        # next broadcast for this serial self-heals (recreates the Track)
        # instead of mutating an orphaned object nobody can see.
        track = self.track_store.get(track_id)
        created = track is None
        if created:
            track = Track(
                track_id=track_id,
                domain="air",
                protocol="remoteid",
                lat=lat,
                lon=lon,
                source_sensor=self.sensor_id,
                label=serial,
                altitude_m=altitude_m,
                speed_mps=speed_mps,
                course_deg=course_deg,
                first_seen=now,
                last_seen=now,
                stale_after_s=self.stale_after_s,
                expire_after_s=self.expire_after_s,
                metadata={"serial": serial, "transports": {}},
            )
            material = False
        else:
            material = self._is_material_change(track, lat, lon, altitude_m)
            track.lat = lat
            track.lon = lon
            if altitude_m is not None:
                track.altitude_m = altitude_m
            if speed_mps is not None:
                track.speed_mps = speed_mps
            if course_deg is not None:
                track.course_deg = course_deg

        # Always refresh last_seen, regardless of which transport reported --
        # this is what makes Track.state() only degrade once *both*
        # transports go quiet, with no changes needed to Track itself.
        track.last_seen = now
        track.metadata.setdefault("transports", {})[backend.transport] = {
            "backend": backend.name,
            "last_seen": now.isoformat(),
        }

        if created or material:
            # TrackStore.upsert() both stores the (possibly new) entry and
            # appends a trail point -- exactly what a genuine position
            # change should do. A liveness-only update (neither created nor
            # material) skips this: the object mutation above is already
            # visible through TrackStore.get()/list() since it's the same
            # reference, and we deliberately don't want a fabricated trail
            # point at an unchanged position.
            self.track_store.upsert(track)

        observation = Observation(
            observation_id=f"obs-{uuid4()}",
            sensor_id=self.sensor_id,
            session_id=self.session_id,
            protocol="remoteid",
            domain="air",
            timestamp=now,
            track_id=track_id,
            metadata={"backend": backend.name, "transport": backend.transport},
        )
        if self.history is not None:
            if created or material or self._should_refresh_history(serial, now):
                self.history.upsert_track(track)
                self._last_history_upsert[serial] = now
            self.history.add_observation(observation)

        return DecodedMessage(observation=observation, track=track)

    def _track_less_message(
        self,
        backend: RemoteIdBackend,
        broadcast: RemoteIdBroadcast,
        now: datetime,
        *,
        serial: str | None,
    ) -> DecodedMessage:
        return DecodedMessage(
            observation=Observation(
                observation_id=f"obs-{uuid4()}",
                sensor_id=self.sensor_id,
                session_id=self.session_id,
                protocol="remoteid",
                domain="air",
                timestamp=now,
                track_id=(f"remoteid-{serial}" if serial else None),
                metadata={
                    "backend": backend.name,
                    "transport": backend.transport,
                    "mac": broadcast.mac,
                    "serial": serial,
                },
            ),
            track=None,
        )

    def _is_material_change(
        self, track: Track, lat: float, lon: float, altitude_m: float | None
    ) -> bool:
        if (
            abs(lat - track.lat) > _POSITION_EPSILON_DEG
            or abs(lon - track.lon) > _POSITION_EPSILON_DEG
        ):
            return True
        if altitude_m is not None:
            if track.altitude_m is None:
                return True
            if abs(altitude_m - track.altitude_m) > _ALTITUDE_EPSILON_M:
                return True
        return False

    def _should_refresh_history(self, serial: str, now: datetime) -> bool:
        last = self._last_history_upsert.get(serial)
        if last is None:
            return True
        return (now - last).total_seconds() >= self.stale_after_s

    def _emit_event(
        self,
        event: str,
        *,
        backend_name: str,
        transport: str,
        mac: str | None,
        serial: str | None,
        now: datetime,
        message: str | None = None,
    ) -> None:
        self._transport_events.append(
            RemoteIdTransportEvent(
                event=event,
                mac=mac,
                transport=transport,
                backend=backend_name,
                serial=serial,
                occurred_at=now,
                message=message,
            )
        )

    def _sweep(self, now: datetime) -> None:
        # transport_lost: fires once per (backend, transport, mac) key, the
        # first poll where its last activity exceeds transport_timeout_s.
        # A non-positive elapsed delta (now at or before a previous call's
        # now) is clamped to zero, not negative, so it never spuriously
        # exceeds transport_timeout_s.
        for key in list(self._live_keys):
            last_seen = self._last_activity.get(key)
            elapsed = max((now - last_seen).total_seconds(), 0.0) if last_seen else float("inf")
            if elapsed > self.transport_timeout_s:
                name, transport, mac = key
                self._emit_event(
                    "transport_lost",
                    backend_name=name,
                    transport=transport,
                    mac=mac,
                    serial=self._mac_to_serial.get(key),
                    now=now,
                )
                self._live_keys.discard(key)

        # Eviction: once transport_lost has already fired for a key (it's no
        # longer live) *and* its last activity additionally exceeds
        # expire_after_s, forget it entirely -- otherwise every MAC ever
        # seen (including rotated/randomized addresses) leaves a permanent
        # entry in these dicts, and _last_activity is scanned every poll().
        for key in list(self._last_activity):
            if key in self._live_keys:
                continue
            elapsed = max((now - self._last_activity[key]).total_seconds(), 0.0)
            if elapsed > self.expire_after_s:
                del self._last_activity[key]
                self._mac_to_serial.pop(key, None)
                self._pending.pop(key, None)

        # History-freshness timer eviction: tied to the serial's own Track
        # lifecycle (expired, or the store was cleared out from under it),
        # not to any individual MAC key timing out -- one serial can map to
        # several MAC keys (one per transport, more as MACs rotate), so
        # evicting on the first MAC key to expire would break the "at most
        # once per stale_after_s" bound the moment a drone is seen on two
        # transports and one goes quiet before the other.
        for serial in list(self._last_history_upsert):
            track = self.track_store.get(f"remoteid-{serial}")
            if track is None or track.state(now) == "expired":
                del self._last_history_upsert[serial]
