from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from olab_rf.models.tracks import dt_to_iso


@dataclass(slots=True)
class SensorStatus:
    sensor_id: str
    mode: str = "idle"
    process_running: bool = False
    tool_found: bool = False
    messages_per_second: float = 0.0
    message_count: int = 0
    last_message_at: datetime | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "sensor_id": self.sensor_id,
            "mode": self.mode,
            "process_running": self.process_running,
            "tool_found": self.tool_found,
            "messages_per_second": self.messages_per_second,
            "message_count": self.message_count,
            "last_message_at": dt_to_iso(self.last_message_at),
            "error": self.error,
        }
