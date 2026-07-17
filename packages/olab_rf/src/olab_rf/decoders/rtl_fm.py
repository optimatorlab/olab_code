from __future__ import annotations


def rtl_fm_audio_rate_hz(modulation: str, sample_rate_hz: int | None = None) -> int:
    if sample_rate_hz is not None:
        return sample_rate_hz
    mode = _rtl_fm_mode(modulation)
    return 12_000 if mode == "am" else 24_000


def rtl_fm_command(
    *,
    path: str = "rtl_fm",
    frequency_hz: int,
    modulation: str = "nfm",
    device_index: int = 0,
    ppm: int | None = None,
    gain_db: float | None = None,
    sample_rate_hz: int | None = None,
    squelch_db: int | None = None,
) -> list[str]:
    mode = _rtl_fm_mode(modulation)
    command = [
        path,
        "-d",
        str(device_index),
        "-f",
        str(frequency_hz),
        "-M",
        mode,
    ]
    command.extend(["-s", str(rtl_fm_audio_rate_hz(modulation, sample_rate_hz))])
    if ppm is not None:
        command.extend(["-p", str(ppm)])
    if gain_db is not None:
        command.extend(["-g", f"{gain_db:g}"])
    if squelch_db is not None:
        command.extend(["-l", str(squelch_db)])
    return command


def _rtl_fm_mode(modulation: str) -> str:
    normalized = modulation.strip().lower()
    if normalized in {"am", "airband", "aviation"}:
        return "am"
    if normalized in {"wfm", "widefm", "broadcast_fm"}:
        return "wbfm"
    return "fm"
