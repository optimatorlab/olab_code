#!/usr/bin/env python3
from __future__ import annotations

import argparse
from time import sleep

from olab_rf import SessionManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Run synthetic replay through SessionManager.")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--poll-sec", type=float, default=0.25)
    args = parser.parse_args()

    manager = SessionManager()
    manager.start_replay(steps=args.steps)

    while manager.status.process_running:
        status = manager.poll()
        print(
            f"mode={status.mode} messages={status.message_count} "
            f"tracks={len(manager.track_store.list())}"
        )
        sleep(args.poll_sec)

    for track in manager.track_store.list():
        print(
            f"{track.track_id} {track.domain}/{track.protocol} "
            f"lat={track.lat:.5f} lon={track.lon:.5f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
