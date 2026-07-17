from __future__ import annotations

from datetime import datetime, timezone

from olab_rf.models.spectrum import FrequencyRange, SpectrumBin, SpectrumPeak, SpectrumSnapshot


def rtl_power_command(
    *,
    path: str = "rtl_power",
    ranges: list[FrequencyRange],
    device_index: int = 0,
    ppm: int | None = None,
    gain_db: float | None = None,
    sample_rate_hz: int | None = None,
    interval_s: int = 2,
) -> list[str]:
    command = [path, "-d", str(device_index)]
    for frequency_range in ranges:
        command.extend(
            [
                "-f",
                (
                    f"{frequency_range.start_hz}:"
                    f"{frequency_range.stop_hz}:"
                    f"{frequency_range.bin_hz}"
                ),
            ]
        )
    command.extend(["-i", f"{interval_s}s"])
    if ppm is not None:
        command.extend(["-p", str(ppm)])
    if gain_db is not None:
        command.extend(["-g", f"{gain_db:g}"])
    if sample_rate_hz is not None:
        command.extend(["-s", str(sample_rate_hz)])
    return command


def parse_rtl_power_line(line: str, peak_limit: int = 8) -> SpectrumSnapshot | None:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 7:
        return None
    try:
        captured_at = datetime.strptime(
            f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        start_hz = int(float(parts[2]))
        stop_hz = int(float(parts[3]))
        step_hz = int(float(parts[4]))
        powers = [float(item) for item in parts[6:] if item]
    except ValueError:
        return None
    if step_hz <= 0 or stop_hz <= start_hz or not powers:
        return None
    bins = [
        SpectrumBin(center_hz=start_hz + int((index + 0.5) * step_hz), power_db=power)
        for index, power in enumerate(powers)
    ]
    peaks = [
        SpectrumPeak(center_hz=item.center_hz, power_db=item.power_db)
        for item in sorted(bins, key=lambda item: item.power_db, reverse=True)[:peak_limit]
    ]
    return SpectrumSnapshot(bins=bins, peaks=peaks, captured_at=captured_at)
