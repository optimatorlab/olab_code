from __future__ import annotations

from olab_rf.decoders.readsb import parse_readsb_aircraft_json, readsb_command


def test_parse_readsb_aircraft_json_normalizes_positioned_aircraft():
    messages = parse_readsb_aircraft_json(
        {
            "aircraft": [
                {
                    "hex": "a1b2c3",
                    "flight": " N123RF ",
                    "lat": 40.1,
                    "lon": -73.9,
                    "alt_baro": 1000,
                    "gs": 100,
                    "track": 90,
                },
                {"hex": "missing-position"},
            ]
        },
        sensor_id="rtlsdr-1",
        session_id="session-1",
    )

    assert len(messages) == 1
    assert messages[0].track.track_id == "adsb-a1b2c3"
    assert messages[0].track.label == "N123RF"
    assert round(messages[0].track.altitude_m, 1) == 304.8


def test_readsb_command_includes_serial_when_provided():
    assert readsb_command(device_serial="00000002", write_json_dir="data/readsb") == [
        "readsb",
        "--device-type",
        "rtlsdr",
        "--gain",
        "auto",
        "--quiet",
        "--write-json",
        "data/readsb",
        "--write-json-every",
        "1",
        "--device",
        "00000002",
    ]
