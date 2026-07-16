#!/usr/bin/env python3
from __future__ import annotations

import argparse
from time import sleep

from olab_rf import FrequencyCatalog, SessionManager
from olab_rf.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a range-based frequency discovery scan.")
    parser.add_argument("--config", help="Optional olab_rf.yaml path for local hardware settings.")
    parser.add_argument("--range-id", default="frs_gmrs")
    parser.add_argument("--min-hz", type=int)
    parser.add_argument("--max-hz", type=int)
    parser.add_argument("--bin-hz", type=int)
    parser.add_argument("--duration-sec", type=float, default=10.0)
    parser.add_argument("--channel-width-hz", type=int)
    parser.add_argument("--poll-sec", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    config = load_config(args.config)
    catalog = FrequencyCatalog.merged(override_payload=config.frequency_catalog)
    manager = SessionManager(
        receiver=config.receivers[0],
        frequency_catalog=catalog,
    )
    try:
        manager.start_range_scan(
            path=config.decoders["rtl_power"].path,
            range_id=args.range_id,
            min_freq_hz=args.min_hz,
            max_freq_hz=args.max_hz,
            step_hz=args.bin_hz,
            duration_sec=args.duration_sec,
            channel_width_hz=args.channel_width_hz,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    while True:
        manager.poll()
        scan = manager.current_frequency_scan()
        if scan is None:
            raise RuntimeError("scan disappeared")
        print(
            f"scan={scan.scan_id} status={scan.status} "
            f"progress={scan.progress:.0%} sweeps={scan.sweeps_completed}"
        )
        if scan.status != "running":
            break
        sleep(args.poll_sec)

    if scan.error:
        raise SystemExit(scan.error)

    for candidate in scan.candidates[: args.limit]:
        label = f" {candidate.label}" if candidate.label else ""
        margin = (
            f" margin={candidate.margin_db:.1f}dB"
            if candidate.margin_db is not None
            else ""
        )
        matched = (
            f" matched={candidate.matched_frequency_hz}Hz"
            f" offset={candidate.frequency_offset_hz}Hz"
            if candidate.matched_frequency_hz is not None
            else ""
        )
        print(
            f"observed={candidate.frequency_hz}Hz power={candidate.power_db:.1f}dB"
            f"{matched}{margin}{label}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
