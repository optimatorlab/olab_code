#!/usr/bin/env python3
from __future__ import annotations

import argparse
from time import sleep

from olab_rf import FrequencyCatalog, SessionManager
from olab_rf.config import load_config


def wait_for_scan(manager: SessionManager, *, poll_sec: float):
    while True:
        manager.poll()
        scan = manager.current_frequency_scan()
        if scan is None:
            raise RuntimeError("scan did not start")
        print(
            f"scan={scan.scan_id} status={scan.status} "
            f"progress={scan.progress:.0%} sweeps={scan.sweeps_completed}"
        )
        if scan.status != "running":
            return scan
        sleep(poll_sec)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture a quiet baseline, then scan for active transmitters."
    )
    parser.add_argument("--config", help="Optional olab_rf.yaml path for local hardware settings.")
    parser.add_argument("--range-id", default="frs_gmrs")
    parser.add_argument("--baseline-sec", type=float, default=10.0)
    parser.add_argument("--active-sec", type=float, default=20.0)
    parser.add_argument("--poll-sec", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    config = load_config(args.config)
    catalog = FrequencyCatalog.merged(override_payload=config.frequency_catalog)
    manager = SessionManager(
        receiver=config.receivers[0],
        frequency_catalog=catalog,
    )

    input("Turn the transmitter off, then press Enter to capture the baseline.")
    manager.capture_range_baseline(
        path=config.decoders["rtl_power"].path,
        range_id=args.range_id,
        duration_sec=args.baseline_sec,
    )
    baseline_scan = wait_for_scan(manager, poll_sec=args.poll_sec)
    if baseline_scan.error:
        raise SystemExit(baseline_scan.error)

    baseline = manager.latest_frequency_baseline()
    input("Start transmitting, then press Enter to run the active scan.")
    manager.start_range_scan(
        path=config.decoders["rtl_power"].path,
        range_id=args.range_id,
        duration_sec=args.active_sec,
        baseline=baseline,
    )
    active_scan = wait_for_scan(manager, poll_sec=args.poll_sec)
    if active_scan.error:
        raise SystemExit(active_scan.error)

    for candidate in active_scan.candidates[: args.limit]:
        margin = candidate.margin_db if candidate.margin_db is not None else 0.0
        label = f" {candidate.label}" if candidate.label else ""
        matched = (
            f" matched={candidate.matched_frequency_hz}Hz"
            f" offset={candidate.frequency_offset_hz}Hz"
            if candidate.matched_frequency_hz is not None
            else ""
        )
        print(
            f"observed={candidate.frequency_hz}Hz power={candidate.power_db:.1f}dB"
            f"{matched} baseline={candidate.baseline_power_db} margin={margin:.1f}dB{label}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
