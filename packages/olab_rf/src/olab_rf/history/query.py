from __future__ import annotations

from typing import Literal

from olab_rf.config import load_config
from olab_rf.history.sqlite import SqliteHistory


HistoryType = Literal[
    "favorites",
    "frequency_scans",
    "spectrum_events",
    "tracks",
]


def get_history(
    *,
    type: HistoryType,
    config: str | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    ub_config = load_config(config)
    history = SqliteHistory(ub_config.history.sqlite_path)
    try:
        if type == "favorites":
            return history.list_frequency_favorites()[:limit]
        if type == "frequency_scans":
            return history.list_frequency_scans(limit=limit)
        if type == "spectrum_events":
            return history.list_spectrum_events(limit=limit)
        if type == "tracks":
            return history.list_track_dicts(limit=limit)
    finally:
        history.close()
    raise ValueError(f"unknown history type: {type!r}")
