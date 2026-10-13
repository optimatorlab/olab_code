from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from olab_rf.models.tracks import utc_now


@dataclass(slots=True)
class RemoteIdBroadcast:
    """One normalized per-transport Remote ID observation, pre-``Track`` merge.

    Backends parse their tool's wire format into this small, dumb structure.
    Cross-transport identity resolution and ``Track`` construction/merging is
    ``RemoteIdManager``'s job, not the backend's -- a backend alone can't see
    whether the other transport has already established this drone's serial.
    """

    backend: str
    #: The nominal transport of the *backend* that produced this broadcast
    #: (``"wifi"``/``"ble"``) -- this is what `RemoteIdManager` keys on for
    #: identity, liveness, and ``Track.metadata["transports"]``.
    transport: str
    #: The transport as *reported on the wire itself* (e.g. droneid-go's
    #: ``Basic ID`` block carries its own ``transport`` field), when the
    #: backend's format provides one -- kept distinct from `transport` above
    #: precisely so it's never mistaken for the key: a message that disagrees
    #: with its own backend's nominal transport (e.g. droneid-go's Sniffle
    #: BLE mode, not used by this pass) still gets keyed on the backend's
    #: fixed value, not this field.
    reported_transport: str | None = None
    serial: str | None = None
    mac: str | None = None
    lat: float | None = None
    lon: float | None = None
    altitude_m: float | None = None
    speed_mps: float | None = None
    course_deg: float | None = None
    operator_lat: float | None = None
    operator_lon: float | None = None
    self_id: str | None = None
    observed_at: datetime = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict)


class RemoteIdBackend(Protocol):
    """Shared shape for a Remote ID transport backend.

    Each concrete backend declares a fixed, class-level nominal ``transport``
    (``"wifi"`` for `DroneidGoBackend`, ``"ble"`` for `UnixRidCaptureBackend`)
    and ``name`` used as the key in ``Track.metadata["transports"]``.
    """

    transport: str
    name: str

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def is_running(self) -> bool: ...

    def poll(self) -> list[RemoteIdBroadcast]: ...


def valid_lat_lon(lat: object, lon: object) -> tuple[float | None, float | None]:
    """Screen a (lat, lon) pair for ODID's "unknown position" sentinel and
    non-finite values.

    ASTM F3411 encodes "unknown position" as latitude/longitude both ``0``.
    Both upstream tools decode the wire encoding faithfully and pass that
    sentinel straight through, so it has to be screened here or every
    unknown-position drone plots at Null Island. Non-finite values (``NaN``/
    ``Infinity``, which ``json.loads`` accepts by default) are screened for
    the same reason -- an unscreened ``NaN`` would also poison the
    materiality comparison in ``RemoteIdManager`` (``abs(nan - nan) > eps``
    is ``False``, so a corrupted track would never be judged "changed" again).
    """
    if lat is None or lon is None:
        return None, None
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None, None
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        return None, None
    if lat_f == 0.0 and lon_f == 0.0:
        return None, None
    return lat_f, lon_f


def valid_altitude(value: object) -> float | None:
    """Screen an altitude for ODID's "unknown altitude" sentinel (``-1000``m)
    and non-finite values."""
    number = safe_float(value)
    if number is None:
        return None
    if number <= -1000:
        return None
    return number


def safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_course(value: object) -> float | None:
    number = safe_float(value)
    if number is None:
        return None
    if number < 0 or number >= 360:
        return None
    return number
