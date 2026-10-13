from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from olab_rf.models.tracks import dt_to_iso, utc_now


@dataclass(frozen=True, slots=True)
class RemoteIdTransportEvent:
    """A lifecycle transition for one (backend, transport, MAC) Remote ID contact.

    Mirrors `VoiceCaptureEvent`'s shape (continuous data -- the `Track` --
    plus discrete lifecycle events layered on top), extended with `mac`
    because these events are keyed by `(backend, transport, mac)`, not
    `serial` -- a drone's identity (`serial`) may not be resolved yet when
    liveness first starts, so `serial` is `None` until it is.

    `event` is one of:
    - ``"transport_acquired"``: this (backend, transport, mac) key started
      reporting.
    - ``"transport_lost"``: it stopped reporting for `transport_timeout_s`.
    - ``"identity_resolved"``: a previously-`serial`-less key was resolved to
      a `serial` (a distinct, one-time event -- resolution never re-emits
      `transport_acquired`).
    """

    event: str
    mac: str | None
    transport: str
    backend: str
    serial: str | None = None
    occurred_at: datetime = field(default_factory=utc_now)
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "mac": self.mac,
            "transport": self.transport,
            "backend": self.backend,
            "serial": self.serial,
            "occurred_at": dt_to_iso(self.occurred_at),
            "message": self.message,
        }
