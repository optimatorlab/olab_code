#!/usr/bin/env python3
from __future__ import annotations

import argparse

from olab_rf import FrequencyCatalog, SessionManager, build_frequency_range_scan_plan
from olab_rf.config import load_config
from olab_rf.history import SqliteHistory


def build_iq_range_plan(**kwargs):
    """Backward-compatible alias for the package range scan planner."""
    if "min_hz" in kwargs:
        kwargs["min_freq_hz"] = kwargs.pop("min_hz")
    if "max_hz" in kwargs:
        kwargs["max_freq_hz"] = kwargs.pop("max_hz")
    if "channel_hz" in kwargs:
        kwargs["channel_frequencies_hz"] = kwargs.pop("channel_hz")
    return build_frequency_range_scan_plan(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a channelized RTL-SDR IQ scan across a range."
    )
    parser.add_argument("--config", help="Optional olab_rf.yaml path for local hardware settings.")
    parser.add_argument("--range-id", default="frs_gmrs")
    parser.add_argument("--min-hz", type=int)
    parser.add_argument("--max-hz", type=int)
    parser.add_argument("--step-hz", type=int)
    parser.add_argument(
        "--channel-hz",
        type=int,
        action="append",
        help="Optional channel frequency in Hz. Repeat to restrict the scan.",
    )
    parser.add_argument("--duration-sec", type=float, default=0.25)
    parser.add_argument("--sample-rate-hz", type=int, default=240_000)
    parser.add_argument("--channel-width-hz", type=int)
    parser.add_argument("--rtl-sdr-path", default="rtl_sdr")
    parser.add_argument("--gain-db", type=float)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    catalog = FrequencyCatalog.merged(override_payload=config.frequency_catalog)
    history = None if args.no_history else SqliteHistory(config.history.sqlite_path)
    manager = SessionManager(
        receiver=config.receivers[0],
        frequency_catalog=catalog,
        history=history,
    )
    try:
        try:
            scan = manager.start_range_scan(
                path=args.rtl_sdr_path,
                backend="rtl_sdr_iq",
                range_id=args.range_id,
                min_freq_hz=args.min_hz,
                max_freq_hz=args.max_hz,
                step_hz=args.step_hz,
                duration_sec=args.duration_sec,
                channel_frequencies_hz=args.channel_hz,
                channel_width_hz=args.channel_width_hz,
                sample_rate_hz=args.sample_rate_hz,
                gain_db=args.gain_db,
            )
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        if scan.error:
            raise SystemExit(scan.error)

        for candidate in scan.candidates[: args.limit]:
            label = f" {candidate.label}" if candidate.label else ""
            matched = (
                f" matched={candidate.matched_frequency_hz}Hz"
                f" offset={candidate.frequency_offset_hz}Hz"
                if candidate.matched_frequency_hz is not None
                else ""
            )
            print(
                f"observed={candidate.frequency_hz}Hz power={candidate.power_db:.1f}dB"
                f"{matched} source={candidate.source}{label}"
            )
    finally:
        if history:
            history.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
