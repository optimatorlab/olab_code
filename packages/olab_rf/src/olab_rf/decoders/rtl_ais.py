from __future__ import annotations

from uuid import uuid4

from olab_rf.decoders.base import DecodedMessage
from olab_rf.models import Observation, Track
from olab_rf.models.tracks import utc_now


def rtl_ais_command(path: str = "rtl_ais", device_index: int = 0, ppm: int = 0) -> list[str]:
    command = [path, "-n", "-d", str(device_index)]
    if ppm:
        command.extend(["-p", str(ppm)])
    return command


def parse_ais_nmea_line(
    line: str,
    *,
    sensor_id: str,
    session_id: str,
) -> DecodedMessage | None:
    if not line.startswith(("!AIVDM", "!AIVDO")):
        return None
    try:
        from pyais import decode
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install olab_rf with the ais extra to parse AIS NMEA") from exc
    try:
        message = decode(line)
    except Exception:
        return None
    payload = _jsonable(message.asdict())
    lat = payload.get("lat")
    lon = payload.get("lon")
    mmsi = payload.get("mmsi")
    if lat is None or lon is None or mmsi is None:
        return DecodedMessage(
            observation=Observation(
                observation_id=f"obs-{uuid4()}",
                sensor_id=sensor_id,
                session_id=session_id,
                protocol="ais",
                domain="marine",
                raw=line,
                metadata={"source": "ais_nmea", "payload": payload},
            )
        )
    now = utc_now()
    track_id = f"ais-{mmsi}"
    speed_knots = payload.get("speed")
    track = Track(
        track_id=track_id,
        domain="marine",
        protocol="ais",
        label=str(mmsi),
        lat=float(lat),
        lon=float(lon),
        speed_mps=_knots_to_mps(speed_knots),
        course_deg=_valid_degrees(payload.get("course")),
        heading_deg=_valid_degrees(payload.get("heading")),
        source_sensor=sensor_id,
        first_seen=now,
        last_seen=now,
        metadata=payload,
    )
    return DecodedMessage(
        observation=Observation(
            observation_id=f"obs-{uuid4()}",
            sensor_id=sensor_id,
            session_id=session_id,
            protocol="ais",
            domain="marine",
            timestamp=now,
            track_id=track_id,
            raw=line,
            metadata={"source": "ais_nmea"},
        ),
        track=track,
    )


def _knots_to_mps(value: object) -> float | None:
    if value is None:
        return None
    return float(value) * 0.514444


def _valid_degrees(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    if number < 0 or number >= 360:
        return None
    return number


def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if hasattr(value, "name"):
        return value.name
    if isinstance(value, bytes):
        return value.hex()
    return value
