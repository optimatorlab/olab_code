#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from olab_rf import get_history


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect olab_rf SQLite history from Python.")
    parser.add_argument(
        "type",
        choices=["favorites", "frequency_scans", "spectrum_events", "tracks"],
    )
    parser.add_argument("--config", help="Optional olab_rf.yaml path.")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    rows = get_history(type=args.type, config=args.config, limit=args.limit)
    print(json.dumps(rows, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
