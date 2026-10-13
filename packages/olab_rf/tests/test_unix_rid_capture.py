from __future__ import annotations

import json

from olab_rf.decoders.unix_rid_capture import (
    parse_unix_rid_capture_json,
    unix_rid_capture_command,
)


def test_unix_rid_capture_command_is_ble_only():
    command = unix_rid_capture_command(path="rid_capture", hci_device="hci1")
    assert command == ["rid_capture", "-b", "hci1"]
    assert "-w" not in command


def test_parse_unix_rid_capture_json_valid_line():
    line = json.dumps(
        {
            "mac": "AA:BB:CC:DD:EE:FF",
            "rssi": -45,
            "operator": "OP123",
            "uav id": "1596F5931234A1B2C3D4",
            "uav latitude": 45.5,
            "uav longitude": -122.6,
            "uav altitude": 120.0,
            "uav heading": 90.0,
            "uav speed": 12.5,
            "self id": "Test flight",
        }
    )

    broadcast = parse_unix_rid_capture_json(line)

    assert broadcast is not None
    assert broadcast.backend == "unix_rid_capture"
    assert broadcast.transport == "ble"
    assert broadcast.serial == "1596F5931234A1B2C3D4"
    assert broadcast.mac == "AA:BB:CC:DD:EE:FF"
    assert broadcast.lat == 45.5
    assert broadcast.lon == -122.6
    assert broadcast.altitude_m == 120.0
    assert broadcast.speed_mps == 12.5
    assert broadcast.course_deg == 90.0
    assert broadcast.self_id == "Test flight"


def test_parse_unix_rid_capture_json_identity_only_line_before_a_fix():
    # unix_rid_capture streams individual ODID messages as it decodes them --
    # a Basic-ID-only line arrives before any Location/Vector fix.
    line = json.dumps({"mac": "AA:BB:CC:DD:EE:FF", "uav id": "DRONE-X"})

    broadcast = parse_unix_rid_capture_json(line)

    assert broadcast is not None
    assert broadcast.serial == "DRONE-X"
    assert broadcast.lat is None
    assert broadcast.lon is None


def test_parse_unix_rid_capture_json_sentinel_and_nonfinite_screened():
    line = json.dumps(
        {"mac": "AA:BB:CC:DD:EE:FF", "uav id": "DRONE-X", "uav latitude": 0.0, "uav longitude": 0.0}
    )
    broadcast = parse_unix_rid_capture_json(line)
    assert broadcast.lat is None
    assert broadcast.lon is None

    line_nan = json.dumps(
        {
            "mac": "AA:BB:CC:DD:EE:FF",
            "uav id": "DRONE-X",
            "uav latitude": float("nan"),
            "uav longitude": -122.6,
        }
    )
    broadcast = parse_unix_rid_capture_json(line_nan)
    assert broadcast.lat is None
    assert broadcast.lon is None


def test_parse_unix_rid_capture_json_malformed_line_returns_none():
    assert parse_unix_rid_capture_json("not json") is None
    assert parse_unix_rid_capture_json("") is None
    assert parse_unix_rid_capture_json("[]") is None
    assert parse_unix_rid_capture_json(json.dumps({"rssi": -50})) is None
