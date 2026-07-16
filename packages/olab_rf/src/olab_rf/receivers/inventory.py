from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ToolCheck:
    name: str
    found: bool
    path: str | None
    executable: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "found": self.found,
            "path": self.path,
            "executable": self.executable,
        }


def check_tool(name: str) -> ToolCheck:
    path = shutil.which(name)
    return ToolCheck(
        name=name,
        found=path is not None,
        path=path,
        executable=Path(path).is_file() if path else None,
    )
