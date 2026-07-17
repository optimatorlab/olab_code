from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from olab_rf.models.tracks import dt_to_iso, utc_now


@dataclass(slots=True)
class RadioSession:
    session_id: str
    mode: str
    receiver_id: str
    status: str = "created"
    decoder: str | None = None
    command: list[str] | None = None
    started_at: datetime = field(default_factory=utc_now)
    stopped_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "receiver_id": self.receiver_id,
            "status": self.status,
            "decoder": self.decoder,
            "command": self.command,
            "started_at": dt_to_iso(self.started_at),
            "stopped_at": dt_to_iso(self.stopped_at),
        }
