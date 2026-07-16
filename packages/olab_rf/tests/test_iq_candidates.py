from __future__ import annotations

import pytest

from olab_rf.decoders.rtl_sdr_iq import IqPeakEstimate
from olab_rf.services.frequency_catalog import FrequencyCatalog
from olab_rf.services.iq_candidates import candidate_from_iq_peak


def test_candidate_from_iq_peak_matches_catalog_channel():
    catalog = FrequencyCatalog.from_dict(
        {
            "frequency_catalog": {
                "ranges": [
                    {
                        "id": "frs_gmrs",
                        "label": "FRS/GMRS",
                        "min_freq_hz": 462_000_000,
                        "max_freq_hz": 468_000_000,
                        "default_modulation": "NFM",
                        "channels": [
                            {
                                "id": "frs_gmrs_7",
                                "label": "FRS-GMRS Ch 7",
                                "frequency_hz": 462_712_500,
                                "modulation": "NFM",
                            }
                        ],
                    }
                ]
            }
        }
    )
    estimate = IqPeakEstimate(
        frequency_hz=462_714_800,
        offset_hz=14_800.0,
        power_db=-34.0,
        fft_size=4096,
    )

    candidate = candidate_from_iq_peak(
        estimate,
        catalog=catalog,
        tolerance_hz=12_500,
        baseline_power_db=-60.0,
        sweeps_seen=2,
    )

    assert candidate.frequency_hz == 462_714_800
    assert candidate.matched_frequency_hz == 462_712_500
    assert candidate.frequency_offset_hz == 2_300
    assert candidate.label == "FRS-GMRS Ch 7"
    assert candidate.modulation == "NFM"
    assert candidate.range_id == "frs_gmrs"
    assert candidate.channel_id == "frs_gmrs_7"
    assert candidate.margin_db == 26.0
    assert candidate.sweeps_seen == 2
    assert candidate.source == "iq_peak"


def test_candidate_from_iq_peak_restricts_match_to_requested_channels():
    catalog = FrequencyCatalog.from_dict(
        {
            "frequency_catalog": {
                "ranges": [
                    {
                        "id": "frs_gmrs",
                        "label": "FRS/GMRS",
                        "min_freq_hz": 462_000_000,
                        "max_freq_hz": 468_000_000,
                        "default_modulation": "NFM",
                        "channels": [
                            {
                                "id": "frs_gmrs_7",
                                "label": "FRS-GMRS Ch 7",
                                "frequency_hz": 462_712_500,
                                "modulation": "NFM",
                            },
                            {
                                "id": "gmrs_21",
                                "label": "GMRS Ch 21",
                                "frequency_hz": 462_700_000,
                                "modulation": "NFM",
                            },
                        ],
                    }
                ]
            }
        }
    )

    candidate = candidate_from_iq_peak(
        IqPeakEstimate(
            frequency_hz=462_703_424,
            offset_hz=-9_076.0,
            power_db=-13.7,
            fft_size=4096,
        ),
        catalog=catalog,
        tolerance_hz=12_500,
        channel_frequencies_hz=[462_712_500],
    )

    assert candidate.matched_frequency_hz == 462_712_500
    assert candidate.frequency_offset_hz == -9_076
    assert candidate.label == "FRS-GMRS Ch 7"
    assert candidate.channel_id == "frs_gmrs_7"


def test_candidate_from_iq_peak_uses_range_when_no_channel_matches():
    catalog = FrequencyCatalog.from_dict(
        {
            "frequency_catalog": {
                "ranges": [
                    {
                        "id": "local",
                        "label": "Local Range",
                        "min_freq_hz": 460_000_000,
                        "max_freq_hz": 470_000_000,
                        "default_modulation": "NFM",
                        "channels": [],
                    }
                ]
            }
        }
    )

    candidate = candidate_from_iq_peak(
        IqPeakEstimate(
            frequency_hz=462_714_800,
            offset_hz=14_800.0,
            power_db=-34.0,
            fft_size=4096,
        ),
        catalog=catalog,
    )

    assert candidate.label == "Local Range"
    assert candidate.modulation == "NFM"
    assert candidate.range_id == "local"
    assert candidate.channel_id is None
    assert candidate.matched_frequency_hz is None
    assert candidate.frequency_offset_hz is None
    assert candidate.margin_db is None
    assert candidate.source == "iq_peak"


def test_candidate_from_iq_peak_rejects_invalid_metadata():
    catalog = FrequencyCatalog()
    estimate = IqPeakEstimate(
        frequency_hz=462_714_800,
        offset_hz=14_800.0,
        power_db=-34.0,
        fft_size=4096,
    )

    with pytest.raises(ValueError, match="tolerance_hz"):
        candidate_from_iq_peak(estimate, catalog=catalog, tolerance_hz=0)

    with pytest.raises(ValueError, match="sweeps_seen"):
        candidate_from_iq_peak(estimate, catalog=catalog, sweeps_seen=0)
