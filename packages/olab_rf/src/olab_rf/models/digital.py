from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DigitalListenStatus:
    session_id: str | None = None
    system_id: str | None = None
    backend: str = "sdrtrunk"
    state: str = "idle"
    process_running: bool = False
    tool_found: bool = False
    profile_found: bool = False
    jmbe_available: bool = False
    command: list[str] | None = None
    error: str | None = None
    stderr: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id, "system_id": self.system_id, "backend": self.backend,
            "state": self.state, "process_running": self.process_running,
            "tool_found": self.tool_found, "profile_found": self.profile_found,
            "jmbe_available": self.jmbe_available, "command": self.command,
            "error": self.error, "stderr": self.stderr,
        }
