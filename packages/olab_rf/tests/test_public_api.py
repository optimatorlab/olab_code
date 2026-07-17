from __future__ import annotations

import pytest

import olab_rf
from olab_rf import (
    FrequencyCandidate,
    FrequencyCatalog,
    FrequencyRangeScanPlan,
    FrequencyScanBackend,
    FrequencyScanRequest,
    FrequencyScanStatus,
    SessionManager,
    SpectrumSnapshot,
)


def test_top_level_exports_cover_primary_python_api():
    expected = {
        "FrequencyCatalog",
        "FrequencyRangeScanPlan",
        "FrequencyScanBackend",
        "FrequencyScanRequest",
        "FrequencyScanStatus",
        "RadioSession",
        "SessionManager",
        "SpectrumSnapshot",
        "build_frequency_range_scan_plan",
        "get_history",
    }

    assert expected <= set(olab_rf.__all__)
    assert olab_rf.SessionManager is SessionManager
    assert olab_rf.FrequencyRangeScanPlan is FrequencyRangeScanPlan
    assert olab_rf.FrequencyScanBackend is FrequencyScanBackend


def test_frequency_scan_request_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="max_freq_hz"):
        FrequencyScanRequest(
            min_freq_hz=468_000_000,
            max_freq_hz=462_000_000,
            bin_size_hz=12_500,
            duration_sec=5,
        )

    with pytest.raises(ValueError, match="duration_sec"):
        FrequencyScanRequest(
            min_freq_hz=462_000_000,
            max_freq_hz=468_000_000,
            bin_size_hz=12_500,
            duration_sec=0,
        )

    with pytest.raises(ValueError, match="backend"):
        FrequencyScanRequest(
            min_freq_hz=462_000_000,
            max_freq_hz=468_000_000,
            bin_size_hz=12_500,
            duration_sec=5,
            backend="unknown",
        )


def test_frequency_candidate_distinguishes_observed_and_matched_frequency():
    candidate = FrequencyCandidate(
        frequency_hz=462_714_798,
        matched_frequency_hz=462_712_500,
        frequency_offset_hz=2_298,
        power_db=-28.0,
        label="FRS/GMRS Ch 7",
    )

    payload = candidate.to_dict()
    restored = FrequencyCandidate.from_dict(payload)

    assert payload["frequency_hz"] == 462_714_798
    assert payload["observed_frequency_hz"] == 462_714_798
    assert payload["matched_frequency_hz"] == 462_712_500
    assert payload["frequency_offset_hz"] == 2_298
    assert restored == candidate


def test_frequency_scan_status_returns_matched_candidates_ranked_by_margin_then_power():
    request = FrequencyScanRequest(
        min_freq_hz=462_000_000,
        max_freq_hz=468_000_000,
        bin_size_hz=12_500,
        duration_sec=5,
    )
    unmatched = FrequencyCandidate(frequency_hz=463_500_000, power_db=30.0)
    matched_low_margin = FrequencyCandidate(
        frequency_hz=462_612_500,
        matched_frequency_hz=462_612_500,
        power_db=-25.0,
        margin_db=8.0,
    )
    matched_high_margin = FrequencyCandidate(
        frequency_hz=462_625_000,
        matched_frequency_hz=462_625_000,
        power_db=-35.0,
        margin_db=12.0,
    )
    matched_power_only = FrequencyCandidate(
        frequency_hz=462_650_000,
        matched_frequency_hz=462_650_000,
        power_db=-15.0,
    )
    status = FrequencyScanStatus(
        scan_id="scan-1",
        request=request,
        candidates=[unmatched, matched_low_margin, matched_power_only, matched_high_margin],
    )

    assert status.matched_candidates == [
        matched_high_margin,
        matched_low_margin,
        matched_power_only,
    ]
    assert status.best_matched_candidate == matched_high_margin
    assert [item["frequency_hz"] for item in status.to_dict()["matched_candidates"]] == [
        462_625_000,
        462_612_500,
        462_650_000,
    ]
    assert status.to_dict()["best_matched_candidate"]["frequency_hz"] == 462_625_000


def test_session_manager_has_model_oriented_state_accessors():
    manager = SessionManager()

    assert isinstance(manager.current_spectrum(), SpectrumSnapshot)
    assert isinstance(manager.catalog_with_favorites(), FrequencyCatalog)
    assert manager.current_frequency_scan() is None
    assert manager.spectrum_history() == []
    assert manager.spectrum_history(limit=0) == []
    assert manager.spectrum_events() == []
    assert manager.spectrum_events(limit=0) == []
