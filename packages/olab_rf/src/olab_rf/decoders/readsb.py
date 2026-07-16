from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from olab_rf.decoders.base import DecodedMessage
from olab_rf.models import Observation, Track
from olab_rf.models.tracks import utc_now


def parse_readsb_aircraft_json(
    payload: str | dict,
    *,
    sensor_id: str,
    session_id: str,
) -> list[DecodedMessage]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    aircraft = data.get("aircraft", [])
    messages: list[DecodedMessage] = []
    for item in aircraft:
        lat = item.get("lat")
        lon = item.get("lon")
        hex_id = item.get("hex")
        if lat is None or lon is None or not hex_id:
            continue
        now = utc_now()
        track_id = f"adsb-{hex_id}"
        track = Track(
            track_id=track_id,
            domain="air",
            protocol="adsb",
            label=(item.get("flight") or "").strip() or hex_id,
            lat=float(lat),
            lon=float(lon),
            altitude_m=_feet_to_m(item.get("alt_baro") or item.get("alt_geom")),
            speed_mps=_knots_to_mps(item.get("gs")),
            course_deg=item.get("track"),
            heading_deg=item.get("true_heading") or item.get("mag_heading"),
            source_sensor=sensor_id,
            first_seen=now,
            last_seen=now,
            metadata=item,
        )
        messages.append(
            DecodedMessage(
                observation=Observation(
                    observation_id=f"obs-{uuid4()}",
                    sensor_id=sensor_id,
                    session_id=session_id,
                    protocol="adsb",
                    domain="air",
                    timestamp=now,
                    track_id=track_id,
                    metadata={"source": "readsb"},
                ),
                track=track,
            )
        )
    return messages


def readsb_command(
    path: str = "readsb",
    device_serial: str | None = None,
    write_json_dir: str | Path = "data/readsb",
) -> list[str]:
    command = [
        path,
        "--device-type",
        "rtlsdr",
        "--gain",
        "auto",
        "--quiet",
        "--write-json",
        str(write_json_dir),
        "--write-json-every",
        "1",
    ]
    if device_serial:
        command.extend(["--device", device_serial])
    return command


def parse_readsb_aircraft_file(
    path: str | Path,
    *,
    sensor_id: str,
    session_id: str,
) -> list[DecodedMessage]:
    aircraft_path = Path(path)
    if not aircraft_path.exists():
        return []
    return parse_readsb_aircraft_json(
        aircraft_path.read_text(encoding="utf-8"),
        sensor_id=sensor_id,
        session_id=session_id,
    )


def _feet_to_m(value: object) -> float | None:
    if value is None or value == "ground":
        return None
    return float(value) * 0.3048


def _knots_to_mps(value: object) -> float | None:
    if value is None:
        return None
    return float(value) * 0.514444
