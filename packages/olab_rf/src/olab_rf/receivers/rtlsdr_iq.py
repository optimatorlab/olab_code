from __future__ import annotations

import subprocess
from typing import Any


def capture_iq_samples(
    *,
    center_frequency_hz: int,
    sample_rate_hz: int,
    sample_count: int,
    device_index: int = 0,
    serial: str | None = None,
    gain_db: float | None = None,
    ppm: int = 0,
    sdr_class: type | None = None,
) -> Any:
    """Capture complex IQ samples from an RTL-SDR using the optional pyrtlsdr adapter."""
    if center_frequency_hz <= 0:
        raise ValueError("center_frequency_hz must be greater than zero")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be greater than zero")
    if sample_count <= 0:
        raise ValueError("sample_count must be greater than zero")
    rtl_sdr_class = sdr_class or _rtlsdr_class()
    kwargs = {"serial_number": serial} if serial else {"device_index": device_index}
    sdr = rtl_sdr_class(**kwargs)
    try:
        sdr.sample_rate = sample_rate_hz
        sdr.center_freq = center_frequency_hz
        sdr.freq_correction = ppm
        if gain_db is not None:
            sdr.gain = gain_db
        return sdr.read_samples(sample_count)
    finally:
        close = getattr(sdr, "close", None)
        if close:
            close()


def capture_iq_samples_with_rtl_sdr(
    *,
    path: str = "rtl_sdr",
    center_frequency_hz: int,
    sample_rate_hz: int,
    sample_count: int,
    device_index: int = 0,
    gain_db: float | None = None,
    ppm: int = 0,
    timeout_sec: float | None = None,
) -> Any:
    """Capture complex IQ samples from the rtl_sdr command-line recorder."""
    if center_frequency_hz <= 0:
        raise ValueError("center_frequency_hz must be greater than zero")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be greater than zero")
    if sample_count <= 0:
        raise ValueError("sample_count must be greater than zero")
    command = rtl_sdr_iq_command(
        path=path,
        center_frequency_hz=center_frequency_hz,
        sample_rate_hz=sample_rate_hz,
        sample_count=sample_count,
        device_index=device_index,
        gain_db=gain_db,
        ppm=ppm,
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout_sec or max(5.0, sample_count / sample_rate_hz + 3.0),
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{path} not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{path} timed out while capturing IQ samples") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"{path} exited with status {result.returncode}")
    return iq_samples_from_u8(result.stdout, sample_count=sample_count)


def rtl_sdr_iq_command(
    *,
    path: str = "rtl_sdr",
    center_frequency_hz: int,
    sample_rate_hz: int,
    sample_count: int,
    device_index: int = 0,
    gain_db: float | None = None,
    ppm: int = 0,
) -> list[str]:
    command = [
        path,
        "-f",
        str(center_frequency_hz),
        "-s",
        str(sample_rate_hz),
        "-d",
        str(device_index),
        "-p",
        str(ppm),
        "-n",
        str(sample_count),
        "-S",
    ]
    if gain_db is not None:
        command.extend(["-g", f"{gain_db:g}"])
    command.append("-")
    return command


def iq_samples_from_u8(payload: bytes, *, sample_count: int | None = None) -> Any:
    np = _numpy()
    if len(payload) % 2:
        raise ValueError("IQ byte payload must contain interleaved I/Q pairs")
    pairs = len(payload) // 2
    if sample_count is not None and pairs < sample_count:
        raise ValueError(f"expected {sample_count} IQ samples, received {pairs}")
    data = np.frombuffer(payload[: pairs * 2], dtype=np.uint8).astype(np.float32)
    iq = data.reshape((-1, 2))
    return ((iq[:, 0] - 127.5) + 1j * (iq[:, 1] - 127.5)) / 127.5


def _rtlsdr_class():
    try:
        from rtlsdr import RtlSdr
    except ImportError as exc:  # pragma: no cover - covered by adapter tests
        if exc.name == "rtlsdr":
            raise RuntimeError(
                "Install the pyrtlsdr extra to capture RTL-SDR IQ samples: "
                "pip install 'olab-rf[pyrtlsdr]'"
            ) from exc
        raise RuntimeError(
            f"pyrtlsdr could not import a required dependency ({exc})"
        ) from exc
    except (AttributeError, OSError) as exc:
        raise RuntimeError(
            "pyrtlsdr could not load the system librtlsdr library; "
            f"check librtlsdr compatibility ({exc})"
        ) from exc
    return RtlSdr


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - covered by IQ analysis tests
        raise RuntimeError(
            "numpy is missing but is a base dependency of olab-rf; "
            "reinstall olab-rf (pip install --force-reinstall olab-rf) to restore it"
        ) from exc
    return np
