from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from olab_rf.decoders.remoteid_base import RemoteIdBroadcast
from olab_rf.history import SqliteHistory
from olab_rf.services.remoteid_manager import RemoteIdManager
from olab_rf.services.track_store import TrackStore


class FakeBackend:
    """A minimal, test-controlled `RemoteIdBackend`."""

    def __init__(self, name: str, transport: str):
        self.name = name
        self.transport = transport
        self.running = False
        self.error: str | None = None
        self.raise_on_start = False
        self.start_calls = 0
        self.stop_calls = 0
        self._queued: list[RemoteIdBroadcast] = []

    def start(self) -> None:
        self.start_calls += 1
        if self.raise_on_start:
            raise RuntimeError("boom")
        self.running = True

    def stop(self) -> None:
        self.stop_calls += 1
        self.running = False

    def is_running(self) -> bool:
        return self.running

    def queue(self, broadcast: RemoteIdBroadcast) -> None:
        self._queued.append(broadcast)

    def poll(self) -> list[RemoteIdBroadcast]:
        items, self._queued = self._queued, []
        return items


def _now() -> datetime:
    # A real "now" anchor (not a fixed past date) so Track.state()'s default
    # real-wall-clock expiry check in TrackStore.list() doesn't mistake
    # every test track for already-expired.
    return datetime.now(UTC)


def _broadcast(
    *,
    backend: str,
    transport: str,
    serial: str | None = None,
    mac: str | None = None,
    lat: float | None = 45.5,
    lon: float | None = -122.6,
    altitude_m: float | None = 100.0,
) -> RemoteIdBroadcast:
    return RemoteIdBroadcast(
        backend=backend,
        transport=transport,
        serial=serial,
        mac=mac,
        lat=lat,
        lon=lon,
        altitude_m=altitude_m,
    )


def test_duplicate_transport_construction_guard_raises():
    wifi_a = FakeBackend("droneid-go", "wifi")
    wifi_b = FakeBackend("another-wifi-tool", "wifi")

    with pytest.raises(ValueError, match="wifi"):
        RemoteIdManager(
            TrackStore(), [wifi_a, wifi_b], session_id="s1", sensor_id="sensor-1"
        )


def test_single_transport_creates_one_track():
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    manager = RemoteIdManager(store, [wifi], session_id="s1", sensor_id="sensor-1")

    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    messages = manager.poll(now=_now())

    assert len(messages) == 1
    track = store.get("remoteid-DRONE-1")
    assert track is not None
    assert track.domain == "air"
    assert track.protocol == "remoteid"
    assert track.metadata["transports"]["wifi"]["backend"] == "droneid-go"


def test_two_transports_merge_into_same_track():
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    ble = FakeBackend("unix_rid_capture", "ble")
    manager = RemoteIdManager(store, [wifi, ble], session_id="s1", sensor_id="sensor-1")

    t0 = _now()
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t0)

    t1 = t0 + timedelta(seconds=1)
    ble.queue(_broadcast(backend="unix_rid_capture", transport="ble", serial="DRONE-1", mac="BB:BB:BB:BB:BB:BB"))
    manager.poll(now=t1)

    assert len(store.list()) == 1
    track = store.get("remoteid-DRONE-1")
    assert set(track.metadata["transports"]) == {"wifi", "ble"}
    assert track.last_seen == t1


def test_track_last_seen_advances_from_either_transport():
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    ble = FakeBackend("unix_rid_capture", "ble")
    manager = RemoteIdManager(store, [wifi, ble], session_id="s1", sensor_id="sensor-1")

    t0 = _now()
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t0)

    t1 = t0 + timedelta(seconds=30)
    ble.queue(_broadcast(backend="unix_rid_capture", transport="ble", serial="DRONE-1", mac="BB:BB:BB:BB:BB:BB"))
    manager.poll(now=t1)

    track = store.get("remoteid-DRONE-1")
    assert track.state(t1) == "active"  # would be "stale" at 30s if only wifi's last_seen counted


def test_transport_acquired_and_lost_fire_once_per_mac_transport():
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    manager = RemoteIdManager(
        store, [wifi], session_id="s1", sensor_id="sensor-1", transport_timeout_s=5.0
    )

    t0 = _now()
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t0)
    events = manager.pop_transport_events()
    assert [e.event for e in events] == ["transport_acquired"]

    # A second broadcast for the same key does not re-fire acquired.
    t1 = t0 + timedelta(seconds=1)
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t1)
    assert manager.pop_transport_events() == []

    # No activity past transport_timeout_s -> transport_lost fires exactly once.
    t2 = t1 + timedelta(seconds=10)
    manager.poll(now=t2)
    events = manager.pop_transport_events()
    assert [e.event for e in events] == ["transport_lost"]

    t3 = t2 + timedelta(seconds=1)
    manager.poll(now=t3)
    assert manager.pop_transport_events() == []

    # R11-B1b: re-acquisition after a loss is a distinct transition from the
    # loss itself -- a new broadcast for the same key must re-fire
    # transport_acquired (the manager keys this on _live_keys membership,
    # which the loss above removed the key from). identity_resolved must
    # NOT re-fire, since this serial was already resolved before the loss.
    t4 = t3 + timedelta(seconds=1)
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t4)
    events = manager.pop_transport_events()
    assert [e.event for e in events] == ["transport_acquired"]


def test_identity_association_buffers_then_resolves_via_identity_resolved_event():
    store = TrackStore()
    ble = FakeBackend("unix_rid_capture", "ble")
    manager = RemoteIdManager(store, [ble], session_id="s1", sensor_id="sensor-1")

    t0 = _now()
    ble.queue(_broadcast(backend="unix_rid_capture", transport="ble", serial=None, mac="CC:CC:CC:CC:CC:CC"))
    messages = manager.poll(now=t0)

    assert len(messages) == 1
    assert messages[0].track is None
    assert messages[0].observation.track_id is None
    events = manager.pop_transport_events()
    assert [e.event for e in events] == ["transport_acquired"]
    assert events[0].serial is None
    assert store.list() == []

    # A later broadcast for the same mac that does carry a serial both
    # creates the Track (folding in the buffered position) and fires
    # identity_resolved rather than a second transport_acquired.
    t1 = t0 + timedelta(seconds=1)
    ble.queue(
        _broadcast(
            backend="unix_rid_capture",
            transport="ble",
            serial="DRONE-9",
            mac="CC:CC:CC:CC:CC:CC",
            lat=None,
            lon=None,
        )
    )
    messages = manager.poll(now=t1)
    assert messages[0].track is not None
    assert messages[0].track.track_id == "remoteid-DRONE-9"
    # Position folded in from the buffered broadcast.
    assert messages[0].track.lat == 45.5
    events = manager.pop_transport_events()
    assert [e.event for e in events] == ["identity_resolved"]
    assert events[0].serial == "DRONE-9"

    # A subsequent mac-only broadcast for the now-resolved mac updates the
    # existing Track without needing another serial-bearing frame.
    t2 = t1 + timedelta(seconds=1)
    ble.queue(
        _broadcast(
            backend="unix_rid_capture", transport="ble", serial=None, mac="CC:CC:CC:CC:CC:CC", lat=46.0, lon=-123.0
        )
    )
    messages = manager.poll(now=t2)
    assert messages[0].track.track_id == "remoteid-DRONE-9"
    assert messages[0].track.lat == 46.0
    assert len(store.list()) == 1


def test_eviction_of_mac_keyed_dicts_after_expire_after_s():
    store = TrackStore()
    ble = FakeBackend("unix_rid_capture", "ble")
    manager = RemoteIdManager(
        store,
        [ble],
        session_id="s1",
        sensor_id="sensor-1",
        transport_timeout_s=5.0,
        expire_after_s=20.0,
    )

    t0 = _now()
    ble.queue(_broadcast(backend="unix_rid_capture", transport="ble", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t0)
    key = ("unix_rid_capture", "ble", "AA:AA:AA:AA:AA:AA")
    assert key in manager._mac_to_serial

    # Past transport_timeout_s but not expire_after_s: transport_lost fires,
    # but the key is still remembered (not evicted).
    t1 = t0 + timedelta(seconds=10)
    manager.poll(now=t1)
    assert key in manager._mac_to_serial

    # Past expire_after_s too: now evicted from all three dicts.
    t2 = t0 + timedelta(seconds=25)
    manager.poll(now=t2)
    assert key not in manager._mac_to_serial
    assert key not in manager._last_activity
    assert key not in manager._pending

    # Resuming activity before expiry does not lose the resolved serial:
    # covered by the "still remembered" assertion above (t1).


def test_manager_start_called_twice_does_not_roll_back_or_error():
    # R11-B1a, manager level: calling start() twice must not raise and must
    # not trigger the fail-fast rollback path on the second call -- real
    # double-spawn prevention lives in each backend's own start() (pinned at
    # the DroneidGoBackend level by
    # test_droneid_go_backend_double_start_replaces_rather_than_duplicates);
    # this test is the manager-level half the plan named separately.
    wifi = FakeBackend("droneid-go", "wifi")
    ble = FakeBackend("unix_rid_capture", "ble")
    manager = RemoteIdManager(TrackStore(), [wifi, ble], session_id="s1", sensor_id="sensor-1")

    manager.start()
    manager.start()

    assert wifi.start_calls == 2
    assert ble.start_calls == 2
    assert wifi.stop_calls == 0  # no accidental rollback on the second call
    assert ble.stop_calls == 0
    assert wifi.running is True
    assert ble.running is True


def test_serial_without_mac_still_gets_a_liveness_key_and_can_go_lost():
    # R11-I3: a serial-bearing broadcast with no MAC at all (droneid_go's
    # parser only rejects a message when *both* are absent) must still
    # register a liveness key -- otherwise metadata["transports"] carries a
    # permanently-stale entry that can never fire transport_lost or be
    # evicted.
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    manager = RemoteIdManager(
        store, [wifi], session_id="s1", sensor_id="sensor-1", transport_timeout_s=5.0
    )

    t0 = _now()
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac=None))
    manager.poll(now=t0)

    events = manager.pop_transport_events()
    assert [e.event for e in events] == ["transport_acquired"]
    track = store.get("remoteid-DRONE-1")
    assert track.metadata["transports"]["wifi"]["backend"] == "droneid-go"

    # No further broadcasts -> this key must be able to go lost like any
    # mac-keyed one, not linger forever with no way to mark it stale.
    t1 = t0 + timedelta(seconds=10)
    manager.poll(now=t1)
    events = manager.pop_transport_events()
    assert [e.event for e in events] == ["transport_lost"]


def test_start_fails_fast_and_rolls_back_already_started_backends():
    wifi = FakeBackend("droneid-go", "wifi")
    ble = FakeBackend("unix_rid_capture", "ble")
    ble.raise_on_start = True
    manager = RemoteIdManager(TrackStore(), [wifi, ble], session_id="s1", sensor_id="sensor-1")

    with pytest.raises(RuntimeError):
        manager.start()

    assert wifi.start_calls == 1
    assert wifi.stop_calls == 1  # rolled back
    assert wifi.running is False


def test_stop_is_idempotent_before_and_after_start():
    wifi = FakeBackend("droneid-go", "wifi")
    manager = RemoteIdManager(TrackStore(), [wifi], session_id="s1", sensor_id="sensor-1")

    manager.stop()  # before any start(): no-op, no exception
    assert wifi.stop_calls == 1

    manager.start()
    manager.stop()
    manager.stop()  # twice in a row: no-op
    assert wifi.stop_calls == 3


def test_backend_status_distinguishes_unhealthy_backend_from_is_running():
    wifi = FakeBackend("droneid-go", "wifi")
    ble = FakeBackend("unix_rid_capture", "ble")
    manager = RemoteIdManager(TrackStore(), [wifi, ble], session_id="s1", sensor_id="sensor-1")

    wifi.running = False
    wifi.error = "connection refused"
    ble.running = True

    assert manager.is_running() is True  # masks the dead wifi backend
    status = {entry["transport"]: entry for entry in manager.backend_status()}
    assert status["wifi"]["running"] is False
    assert status["wifi"]["error"] == "connection refused"
    assert status["ble"]["running"] is True
    assert status["ble"]["error"] is None


def test_write_policy_liveness_only_does_not_append_trail_point():
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    manager = RemoteIdManager(store, [wifi], session_id="s1", sensor_id="sensor-1")

    t0 = _now()
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t0)
    assert len(store.trail_for("remoteid-DRONE-1")) == 1

    # Same position again: liveness-only, no new trail point.
    t1 = t0 + timedelta(seconds=1)
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t1)
    assert len(store.trail_for("remoteid-DRONE-1")) == 1

    # Materially different position: a new trail point does get appended.
    t2 = t1 + timedelta(seconds=1)
    wifi.queue(
        _broadcast(
            backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA", lat=46.0, lon=-123.0
        )
    )
    manager.poll(now=t2)
    assert len(store.trail_for("remoteid-DRONE-1")) == 2


def test_history_freshness_timer_upserts_at_most_once_per_stale_after_s(tmp_path):
    history = SqliteHistory(tmp_path / "history.sqlite")
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    manager = RemoteIdManager(
        store, [wifi], session_id="s1", sensor_id="sensor-1", stale_after_s=30.0, history=history
    )

    t0 = _now()
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t0)
    assert len(history.trail_for("remoteid-DRONE-1")) == 1

    # Liveness-only broadcasts within stale_after_s: no extra history write.
    for i in range(1, 5):
        t = t0 + timedelta(seconds=i * 5)
        wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
        manager.poll(now=t)
    assert len(history.trail_for("remoteid-DRONE-1")) == 1

    # Past stale_after_s: exactly one extra history write, not per-broadcast.
    t_far = t0 + timedelta(seconds=35)
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t_far)
    assert len(history.trail_for("remoteid-DRONE-1")) == 2

    history.close()


def test_history_eviction_keyed_to_track_not_a_single_mac_timeout(tmp_path):
    history = SqliteHistory(tmp_path / "history.sqlite")
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    ble = FakeBackend("unix_rid_capture", "ble")
    manager = RemoteIdManager(
        store,
        [wifi, ble],
        session_id="s1",
        sensor_id="sensor-1",
        transport_timeout_s=5.0,
        stale_after_s=30.0,
        expire_after_s=300.0,
        history=history,
    )

    t0 = _now()
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    ble.queue(_broadcast(backend="unix_rid_capture", transport="ble", serial="DRONE-1", mac="BB:BB:BB:BB:BB:BB"))
    manager.poll(now=t0)
    assert len(history.trail_for("remoteid-DRONE-1")) == 1

    # WiFi goes quiet (times out) while BLE keeps sending liveness-only
    # broadcasts past stale_after_s. If the freshness timer were (wrongly)
    # evicted the moment the WiFi MAC key times out, the next BLE broadcast
    # would trigger an immediate extra history write here.
    t1 = t0 + timedelta(seconds=10)  # wifi times out (>5s), no wifi broadcast
    ble.queue(_broadcast(backend="unix_rid_capture", transport="ble", serial="DRONE-1", mac="BB:BB:BB:BB:BB:BB"))
    manager.poll(now=t1)
    assert len(history.trail_for("remoteid-DRONE-1")) == 1  # still just the one

    # Only once stale_after_s has genuinely elapsed does the coarse timer fire.
    t2 = t0 + timedelta(seconds=35)
    ble.queue(_broadcast(backend="unix_rid_capture", transport="ble", serial="DRONE-1", mac="BB:BB:BB:BB:BB:BB"))
    manager.poll(now=t2)
    assert len(history.trail_for("remoteid-DRONE-1")) == 2

    history.close()


def test_track_reresolves_and_self_heals_after_external_track_store_clear():
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    manager = RemoteIdManager(store, [wifi], session_id="s1", sensor_id="sensor-1")

    t0 = _now()
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t0)
    assert store.get("remoteid-DRONE-1") is not None

    store.clear()  # e.g. an external consumer restarting a shared session

    t1 = t0 + timedelta(seconds=1)
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    messages = manager.poll(now=t1)

    track = store.get("remoteid-DRONE-1")
    assert track is not None  # self-healed, not lost forever
    assert messages[0].track is track


def test_poll_now_not_advancing_does_not_spuriously_fire_transport_lost():
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    manager = RemoteIdManager(
        store, [wifi], session_id="s1", sensor_id="sensor-1", transport_timeout_s=5.0
    )

    t0 = _now()
    wifi.queue(_broadcast(backend="droneid-go", transport="wifi", serial="DRONE-1", mac="AA:AA:AA:AA:AA:AA"))
    manager.poll(now=t0)
    manager.pop_transport_events()

    # now at or before the previous call's now: must not raise, and must not
    # fire transport_lost from a spurious negative elapsed delta.
    manager.poll(now=t0)
    assert manager.pop_transport_events() == []
    manager.poll(now=t0 - timedelta(seconds=100))
    assert manager.pop_transport_events() == []


def test_sentinel_position_never_reaches_the_track():
    store = TrackStore()
    wifi = FakeBackend("droneid-go", "wifi")
    manager = RemoteIdManager(store, [wifi], session_id="s1", sensor_id="sensor-1")

    wifi.queue(
        RemoteIdBroadcast(
            backend="droneid-go",
            transport="wifi",
            serial="DRONE-1",
            mac="AA:AA:AA:AA:AA:AA",
            lat=None,
            lon=None,
        )
    )
    messages = manager.poll(now=_now())

    # Identity known, but no position ever seen -- no Track fabricated with
    # a placeholder/sentinel position.
    assert messages[0].track is None
    assert store.list() == []
