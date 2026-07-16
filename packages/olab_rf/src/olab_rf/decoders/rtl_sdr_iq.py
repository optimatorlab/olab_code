from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IqPeakEstimate:
    frequency_hz: int
    offset_hz: float
    power_db: float
    fft_size: int

    def to_dict(self) -> dict[str, object]:
        return {
            "frequency_hz": self.frequency_hz,
            "offset_hz": self.offset_hz,
            "power_db": self.power_db,
            "fft_size": self.fft_size,
        }


def estimate_iq_peak(
    samples: Any,
    *,
    center_frequency_hz: int,
    sample_rate_hz: int,
    fft_size: int | None = None,
    max_offset_hz: int | None = None,
) -> IqPeakEstimate:
    """Estimate the strongest frequency peak in complex baseband IQ samples."""
    np = _numpy()
    if center_frequency_hz <= 0:
        raise ValueError("center_frequency_hz must be greater than zero")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be greater than zero")
    if max_offset_hz is not None and max_offset_hz <= 0:
        raise ValueError("max_offset_hz must be greater than zero")

    iq = np.asarray(samples, dtype=np.complex64)
    if iq.ndim != 1 or iq.size == 0:
        raise ValueError("samples must be a non-empty one-dimensional sequence")
    size = int(fft_size or iq.size)
    if size <= 0:
        raise ValueError("fft_size must be greater than zero")
    if size > iq.size:
        raise ValueError("fft_size must be less than or equal to the sample count")

    iq = iq[:size]
    iq = iq - np.mean(iq)
    window = np.hanning(size)
    spectrum = np.fft.fftshift(np.fft.fft(iq * window))
    powers = np.abs(spectrum) ** 2
    offsets_hz = np.fft.fftshift(np.fft.fftfreq(size, d=1.0 / sample_rate_hz))
    if max_offset_hz is not None:
        in_window = np.abs(offsets_hz) <= max_offset_hz
        if not np.any(in_window):
            raise ValueError("max_offset_hz excludes all FFT bins")
        powers = np.where(in_window, powers, -1.0)
    peak_index = int(np.argmax(powers))
    offset_hz = float(offsets_hz[peak_index])
    power_db = float(10.0 * np.log10(float(powers[peak_index]) / size + 1e-12))
    return IqPeakEstimate(
        frequency_hz=int(round(center_frequency_hz + offset_hz)),
        offset_hz=offset_hz,
        power_db=power_db,
        fft_size=size,
    )


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - covered by dependency-specific test
        raise RuntimeError(
            "numpy is missing but is a base dependency of olab-rf; "
            "reinstall olab-rf (pip install --force-reinstall olab-rf) to restore it"
        ) from exc
    return np
