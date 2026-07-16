from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import time
from typing import Sequence

from olab_rf.config import load_config
from olab_rf.history import SqliteHistory
from olab_rf.history import get_history
from olab_rf.services.checks import environment_check
from olab_rf.services.frequency_catalog import FrequencyCatalog
from olab_rf.services.session_manager import SessionManager
from olab_rf.web.app import create_app


def check_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run olab_rf environment checks.")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    config_path = _default_config_path(args.config)
    config = load_config(config_path)
    print(
        json.dumps(
            environment_check(
                tool_paths={
                    "readsb": config.decoders["readsb"].path,
                    "rtl_ais": config.decoders["rtl_ais"].path,
                    "rtl_power": config.decoders["rtl_power"].path,
                    "rtl_fm": config.decoders["rtl_fm"].path,
                },
                sdrtrunk_paths=config.sdrtrunk.to_dict(),
                config_path=config_path,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def replay_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run synthetic olab_rf replay once.")
    parser.add_argument("--steps", type=int, default=12)
    args = parser.parse_args(argv)
    manager = SessionManager()
    manager.start_replay(steps=args.steps)
    print(json.dumps([track.to_dict() for track in manager.track_store.list()], indent=2))
    return 0


def run_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one olab_rf mode.")
    parser.add_argument("--config")
    parser.add_argument("--mode", choices=["adsb", "ais", "replay", "spectrum"], default="replay")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    manager = SessionManager.from_config(config)
    if args.mode == "replay":
        manager.start_replay()
        print(json.dumps([track.to_dict() for track in manager.track_store.list()], indent=2))
        return 0
    try:
        if args.mode == "adsb":
            session = manager.start_adsb()
        elif args.mode == "ais":
            session = manager.start_ais()
        else:
            session = manager.start_spectrum()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(session.to_dict(), indent=2, sort_keys=True))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        manager.stop()
    return 0


def history_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect olab_rf SQLite history.")
    parser.add_argument(
        "type",
        choices=[
            "favorites",
            "frequency-scans",
            "frequency-scan",
            "spectrum-events",
            "tracks",
        ],
    )
    parser.add_argument("scan_id", nargs="?")
    parser.add_argument("--config")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table")
    args = parser.parse_args(argv)
    history_type = args.type.replace("-", "_")
    if history_type == "frequency_scan":
        if not args.scan_id:
            raise SystemExit("frequency-scan requires SCAN_ID")
        config = load_config(args.config)
        history = SqliteHistory(config.history.sqlite_path)
        try:
            row = history.get_frequency_scan(args.scan_id)
        finally:
            history.close()
        rows = [row] if row else []
    else:
        rows = get_history(type=history_type, config=args.config, limit=args.limit)
    _print_rows(rows, output_format=args.format, history_type=history_type)
    return 0


def demo_server_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the olab_rf local demo server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--config")
    parser.add_argument("--history", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args(argv)
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Reinstall olab-rf with the web extra, e.g. "
            "pip install --upgrade 'olab-rf[web] @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_rf'"
        ) from exc
    config_path = _default_config_path(args.config)
    config = load_config(config_path)
    history = None if args.no_history else SqliteHistory(config.history.sqlite_path)
    manager = SessionManager(
        receiver=config.receivers[0],
        history=history,
        frequency_catalog=FrequencyCatalog.merged(override_payload=config.frequency_catalog),
    )
    uvicorn.run(
        create_app(manager=manager, config=config, config_path=config_path),
        host=args.host,
        port=args.port,
    )
    return 0


def _default_config_path(config: str | None) -> str | None:
    if config:
        return config
    local_config = Path("olab_rf.yaml")
    return str(local_config) if local_config.exists() else None


def _print_rows(
    rows: list[dict[str, object]],
    *,
    output_format: str,
    history_type: str,
) -> None:
    if output_format == "json":
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    if output_format == "csv":
        fieldnames = _fieldnames(rows)
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(_flatten_row(row, fieldnames=fieldnames))
        return
    print(_table(_table_rows(rows, history_type=history_type)))


def _table_rows(
    rows: list[dict[str, object]],
    *,
    history_type: str,
) -> list[dict[str, object]]:
    if history_type in {"frequency_scans", "frequency_scan"}:
        return [_frequency_scan_table_row(row) for row in rows]
    return rows


def _frequency_scan_table_row(row: dict[str, object]) -> dict[str, object]:
    request = row.get("request") if isinstance(row.get("request"), dict) else {}
    best = row.get("best_candidate") if isinstance(row.get("best_candidate"), dict) else {}
    min_freq_hz = request.get("min_freq_hz", "") if isinstance(request, dict) else ""
    max_freq_hz = request.get("max_freq_hz", "") if isinstance(request, dict) else ""
    candidates = row.get("candidates") or []
    return {
        "scan_id": row.get("scan_id", ""),
        "status": row.get("status", ""),
        "started_at": row.get("started_at", ""),
        "range_hz": f"{min_freq_hz}-{max_freq_hz}" if min_freq_hz or max_freq_hz else "",
        "candidates": len(candidates) if isinstance(candidates, list) else "",
        "best_frequency_hz": (
            best.get("matched_frequency_hz") or best.get("frequency_hz", "")
            if isinstance(best, dict)
            else ""
        ),
        "observed_frequency_hz": best.get("frequency_hz", "") if isinstance(best, dict) else "",
        "best_label": best.get("label", "") if isinstance(best, dict) else "",
        "source": best.get("source", "") if isinstance(best, dict) else "",
    }


def _fieldnames(rows: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for row in rows:
        for name in _flatten_row(row).keys():
            if name not in names:
                names.append(name)
    return names


def _flatten_row(
    row: dict[str, object],
    *,
    fieldnames: list[str] | None = None,
) -> dict[str, object]:
    flattened: dict[str, object] = {}
    for key, value in row.items():
        if isinstance(value, (str, int, float)) or value is None:
            flattened[key] = value
        elif isinstance(value, bool):
            flattened[key] = value
        else:
            flattened[key] = json.dumps(value, sort_keys=True)
    if fieldnames is not None:
        return {name: flattened.get(name, "") for name in fieldnames}
    return flattened


def _table(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "No rows."
    flattened = [_flatten_row(row) for row in rows]
    fieldnames = _fieldnames(rows)
    widths = {
        name: max(len(name), *(len(str(row.get(name, ""))) for row in flattened))
        for name in fieldnames
    }
    lines = [
        "  ".join(name.ljust(widths[name]) for name in fieldnames),
        "  ".join("-" * widths[name] for name in fieldnames),
    ]
    for row in flattened:
        lines.append(
            "  ".join(str(row.get(name, "")).ljust(widths[name]) for name in fieldnames)
        )
    return "\n".join(lines)
