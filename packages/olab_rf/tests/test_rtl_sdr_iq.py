from __future__ import annotations

import pytest

from olab_rf.decoders import rtl_sdr_iq
from olab_rf.decoders.rtl_sdr_iq import estimate_iq_peak


def test_estimate_iq_peak_finds_synthetic_tone():
    np = pytest.importorskip("numpy")
    sample_rate_hz = 240_000
    center_frequency_hz = 462_700_000
    tone_offset_hz = 12_500
    count = 4096
    time = np.arange(count) / sample_rate_hz
    samples = np.exp(2j * np.pi * tone_offset_hz * time)

    estimate = estimate_iq_peak(
        samples,
        center_frequency_hz=center_frequency_hz,
        sample_rate_hz=sample_rate_hz,
    )

    assert estimate.frequency_hz == pytest.approx(
        center_frequency_hz + tone_offset_hz,
        abs=sample_rate_hz / count,
    )
    assert estimate.offset_hz == pytest.approx(tone_offset_hz, abs=sample_rate_hz / count)
    assert estimate.fft_size == count
    assert estimate.power_db > 0
    assert estimate.to_dict()["frequency_hz"] == estimate.frequency_hz


def test_estimate_iq_peak_limits_search_to_offset_window():
    np = pytest.importorskip("numpy")
    sample_rate_hz = 240_000
    center_frequency_hz = 462_700_000
    count = 4096
    time = np.arange(count) / sample_rate_hz
    in_channel_offset_hz = 2_500
    edge_offset_hz = 118_000
    samples = (
        0.25 * np.exp(2j * np.pi * in_channel_offset_hz * time)
        + 1.0 * np.exp(2j * np.pi * edge_offset_hz * time)
    )

    estimate = estimate_iq_peak(
        samples,
        center_frequency_hz=center_frequency_hz,
        sample_rate_hz=sample_rate_hz,
        max_offset_hz=12_500,
    )

    assert estimate.offset_hz == pytest.approx(in_channel_offset_hz, abs=sample_rate_hz / count)


def test_estimate_iq_peak_rejects_invalid_inputs():
    pytest.importorskip("numpy")
    with pytest.raises(ValueError, match="sample_rate_hz"):
        estimate_iq_peak([1 + 0j], center_frequency_hz=462_700_000, sample_rate_hz=0)

    with pytest.raises(ValueError, match="non-empty"):
        estimate_iq_peak([], center_frequency_hz=462_700_000, sample_rate_hz=240_000)

    with pytest.raises(ValueError, match="fft_size"):
        estimate_iq_peak(
            [1 + 0j],
            center_frequency_hz=462_700_000,
            sample_rate_hz=240_000,
            fft_size=2,
        )

    with pytest.raises(ValueError, match="max_offset_hz"):
        estimate_iq_peak(
            [1 + 0j],
            center_frequency_hz=462_700_000,
            sample_rate_hz=240_000,
            max_offset_hz=0,
        )


def test_estimate_iq_peak_missing_numpy_message(monkeypatch):
    def missing_numpy():
        raise RuntimeError("numpy is missing but is a base dependency of olab-rf")

    monkeypatch.setattr(rtl_sdr_iq, "_numpy", missing_numpy)

    with pytest.raises(RuntimeError, match="base dependency of olab-rf"):
        estimate_iq_peak([1 + 0j], center_frequency_hz=462_700_000, sample_rate_hz=240_000)
