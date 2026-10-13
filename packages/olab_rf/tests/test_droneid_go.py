from __future__ import annotations

import threading
import time

import pytest

from olab_rf.decoders.droneid_go import (
    DroneidGoBackend,
    droneid_go_command,
    parse_droneid_go_json,
)


def test_droneid_go_command_includes_interface_and_port():
    command = droneid_go_command(path="droneid-go", iface="wlan1", zmq_port=4224)
    assert command == ["droneid-go", "-i", "wlan1", "-z", "-zmqsetting", "127.0.0.1:4224"]


def test_droneid_go_command_uses_custom_bind_address():
    command = droneid_go_command(zmq_host="0.0.0.0", zmq_port=5000)
    assert command[-3:] == ["-z", "-zmqsetting", "0.0.0.0:5000"]
    assert "-ble" not in command


def test_droneid_go_command_without_interface():
    assert droneid_go_command(path="droneid-go") == [
        "droneid-go",
        "-z",
        "-zmqsetting",
        "127.0.0.1:4224",
    ]


def test_parse_droneid_go_json_dict_with_basic_id_and_location():
    payload = {
        "MAC": "AA:BB:CC:DD:EE:FF",
        "Basic ID": {"id": "1596F5931234A1B2C3D4", "id_type": 1, "transport": "wifi"},
        "Location/Vector Message": {
            "latitude": 45.5,
            "longitude": -122.6,
            "geodetic_altitude": 120.0,
            "speed": 12.5,
            "direction": 90.0,
        },
        "Self-ID Message": {"text": "Test flight"},
        "System Message": {"operator_lat": 45.4, "operator_lon": -122.5},
    }

    broadcasts = parse_droneid_go_json(payload)

    assert len(broadcasts) == 1
    broadcast = broadcasts[0]
    assert broadcast.backend == "droneid-go"
    assert broadcast.transport == "wifi"  # nominal, always "wifi" for this backend
    assert broadcast.reported_transport == "wifi"  # the wire's own value, kept separate
    assert broadcast.serial == "1596F5931234A1B2C3D4"
    assert broadcast.mac == "AA:BB:CC:DD:EE:FF"
    assert broadcast.lat == 45.5
    assert broadcast.lon == -122.6
    assert broadcast.altitude_m == 120.0
    assert broadcast.speed_mps == 12.5
    assert broadcast.course_deg == 90.0
    assert broadcast.operator_lat == 45.4
    assert broadcast.operator_lon == -122.5
    assert broadcast.self_id == "Test flight"


def test_parse_droneid_go_json_transport_stays_nominal_even_if_wire_disagrees():
    # droneid-go's own -ble (Sniffle) mode can report "bluetooth" in a
    # message's Basic ID block; the nominal `transport` this backend keys on
    # must stay "wifi" regardless (R11-N2) -- only `reported_transport`
    # reflects the wire value.
    payload = {
        "MAC": "AA:BB:CC:DD:EE:FF",
        "Basic ID": {"id": "DRONE-Z", "transport": "bluetooth"},
    }
    broadcast = parse_droneid_go_json(payload)[0]
    assert broadcast.transport == "wifi"
    assert broadcast.reported_transport == "bluetooth"


def test_parse_droneid_go_json_list_of_two_items():
    payload = [
        {"MAC": "AA:AA:AA:AA:AA:AA", "Basic ID": {"id": "DRONE-A"}},
        {"MAC": "BB:BB:BB:BB:BB:BB", "Basic ID": {"id": "DRONE-B"}},
    ]

    broadcasts = parse_droneid_go_json(payload)

    assert [broadcast.serial for broadcast in broadcasts] == ["DRONE-A", "DRONE-B"]


def test_parse_droneid_go_json_location_only_has_no_serial():
    payload = {
        "MAC": "AA:BB:CC:DD:EE:FF",
        "Location/Vector Message": {"latitude": 45.5, "longitude": -122.6},
    }

    broadcasts = parse_droneid_go_json(payload)

    assert len(broadcasts) == 1
    assert broadcasts[0].serial is None
    assert broadcasts[0].mac == "AA:BB:CC:DD:EE:FF"
    assert broadcasts[0].lat == 45.5
    assert broadcasts[0].lon == -122.6


def test_parse_droneid_go_json_sentinel_and_nonfinite_screened():
    payload = {
        "MAC": "AA:BB:CC:DD:EE:FF",
        "Basic ID": {"id": "DRONE-X"},
        "Location/Vector Message": {
            "latitude": 0.0,
            "longitude": 0.0,
            "geodetic_altitude": -1000.0,
        },
    }
    broadcasts = parse_droneid_go_json(payload)
    assert broadcasts[0].lat is None
    assert broadcasts[0].lon is None
    assert broadcasts[0].altitude_m is None

    nan_payload = {
        "MAC": "AA:BB:CC:DD:EE:FF",
        "Basic ID": {"id": "DRONE-Y"},
        "Location/Vector Message": {"latitude": float("nan"), "longitude": -122.6},
    }
    broadcasts = parse_droneid_go_json(nan_payload)
    assert broadcasts[0].lat is None
    assert broadcasts[0].lon is None


def test_parse_droneid_go_json_malformed_or_no_identity_returns_empty():
    assert parse_droneid_go_json("not a payload") == []
    assert parse_droneid_go_json({"System Message": {"latitude": 1.0}}) == []
    assert parse_droneid_go_json([{"System Message": {}}, "garbage"]) == []


def test_droneid_go_backend_missing_pyzmq_raises_runtime_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "zmq":
            raise ImportError("no zmq")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    backend = DroneidGoBackend(path="does-not-matter")

    with pytest.raises(RuntimeError, match="droneid-go extra"):
        backend.start()


def _write_fake_binary(tmp_path) -> str:
    """A long-running placeholder subprocess (ignores any arguments
    droneid_go_command would pass it) so DecoderProcess has a real,
    long-lived process to manage without actually needing droneid-go."""
    import stat

    fake_binary = tmp_path / "fake-droneid-go"
    fake_binary.write_text("#!/bin/sh\nsleep 30\n")
    fake_binary.chmod(fake_binary.stat().st_mode | stat.S_IEXEC)
    return str(fake_binary)


def test_droneid_go_backend_subscriber_thread_end_to_end(tmp_path):
    zmq = pytest.importorskip("zmq", reason="pyzmq is not installed; install olab-rf[droneid-go]")
    fake_binary = _write_fake_binary(tmp_path)

    context = zmq.Context.instance()
    publisher = context.socket(zmq.PUB)
    port = publisher.bind_to_random_port("tcp://127.0.0.1")
    # Give the PUB socket a moment to bind before anyone subscribes.
    time.sleep(0.05)

    backend = DroneidGoBackend(
        path=str(fake_binary),
        zmq_host="127.0.0.1",
        zmq_port=port,
        queue_maxlen=5,
        recv_timeout_ms=100,
        join_timeout_s=2.0,
    )
    try:
        backend.start()
        # Idle first (exercises the RCVTIMEO/zmq.Again path -- the bug this
        # test exists to catch would kill the thread on this very first
        # idle tick), then publish a real message.
        time.sleep(0.3)
        assert backend.is_running() is True

        message = {"MAC": "AA:BB:CC:DD:EE:FF", "Basic ID": {"id": "DRONE-1"}}
        broadcasts = _publish_until_delivered(publisher, backend, message, "DRONE-1")
        assert any(b.serial == "DRONE-1" for b in broadcasts)

        # Overflow the bounded buffer and confirm it stays bounded with
        # dropped_count incrementing, rather than growing without limit.
        for i in range(20):
            publisher.send_string(f'{{"Basic ID": {{"id": "FLOOD-{i}"}}}}')
        time.sleep(0.5)
        assert backend.dropped_count > 0
        backend.poll()  # drain, don't assert on flood contents

        # Re-arm: start() -> stop() -> start() must actually deliver a
        # message afterward, not just leave "one thread" that is silently
        # dead (a reused stop Event would make the new thread evaluate
        # `while not stop_event.is_set()` as False immediately).
        backend.error = "stale error from a previous run"
        dropped_before_restart = backend.dropped_count
        backend.stop()
        assert backend.is_running() is False
        backend.start()
        assert backend.error is None
        assert backend.dropped_count == dropped_before_restart  # cumulative, not reset

        message2 = {"MAC": "11:22:33:44:55:66", "Basic ID": {"id": "DRONE-2"}}
        broadcasts = _publish_until_delivered(publisher, backend, message2, "DRONE-2")
        assert any(b.serial == "DRONE-2" for b in broadcasts)
    finally:
        backend.stop()
        publisher.close(linger=0)

    # No live thread left behind after the final stop().
    thread_names = {thread.name for thread in threading.enumerate()}
    assert "olab-rf-droneid-go-zmq" not in thread_names


def test_droneid_go_backend_double_start_replaces_rather_than_duplicates(tmp_path):
    # start() -> start() with NO intervening stop() (R11-B1a) -- distinct
    # from the re-arm (start->stop->start) transition the end-to-end test
    # above covers. _start_subscriber()'s internal stop-then-start guard
    # must leave exactly one live subscriber thread, not two.
    pytest.importorskip("zmq", reason="pyzmq is not installed; install olab-rf[droneid-go]")
    fake_binary = _write_fake_binary(tmp_path)

    backend = DroneidGoBackend(
        path=fake_binary,
        zmq_host="127.0.0.1",
        zmq_port=59991,
        recv_timeout_ms=100,
        join_timeout_s=2.0,
    )
    try:
        backend.start()
        first_thread = backend._thread
        assert first_thread is not None and first_thread.is_alive()

        backend.start()  # no stop() in between
        second_thread = backend._thread

        assert second_thread is not first_thread  # replaced, not duplicated
        assert first_thread.is_alive() is False  # old one torn down
        assert second_thread.is_alive() is True

        live_subscriber_threads = [
            t for t in threading.enumerate() if t.name == "olab-rf-droneid-go-zmq"
        ]
        assert len(live_subscriber_threads) == 1
        assert backend.is_running() is True
    finally:
        backend.stop()


def test_droneid_go_backend_subscriber_error_captured_not_swallowed():
    # R11-I2: socket setup happening outside the try (as originally written)
    # would let a connect() failure kill the thread silently, leaving
    # self.error as None. A deliberately malformed zmq_host exercises that
    # same code path (context.socket()/connect() raising) without needing a
    # real subprocess.
    pytest.importorskip("zmq", reason="pyzmq is not installed; install olab-rf[droneid-go]")

    backend = DroneidGoBackend(
        path="true",
        zmq_host="not a valid host:with spaces",
        zmq_port=59992,
        recv_timeout_ms=100,
        join_timeout_s=2.0,
    )
    # Bypass DecoderProcess (the fake path isn't a real long-running
    # process) and exercise the subscriber thread directly -- this test is
    # about the ZMQ error-capture path, not process lifecycle.
    stop_event = threading.Event()
    thread = threading.Thread(target=backend._run, args=(stop_event,), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5.0
        while backend.error is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert backend.error is not None
    finally:
        stop_event.set()
        thread.join(timeout=2.0)


def _publish_until_delivered(publisher, backend: DroneidGoBackend, message: dict, serial: str):
    import json as _json

    deadline = time.monotonic() + 5.0
    collected = []
    while time.monotonic() < deadline:
        publisher.send_string(_json.dumps(message))
        time.sleep(0.1)
        collected.extend(backend.poll())
        if any(b.serial == serial for b in collected):
            return collected
    raise AssertionError(f"message for serial {serial!r} was never delivered")
