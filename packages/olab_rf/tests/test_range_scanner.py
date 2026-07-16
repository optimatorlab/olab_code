from __future__ import annotations

import pytest

from olab_rf import FrequencyCatalog, SessionManager, build_frequency_range_scan_plan
from olab_rf.services.range_scanner import FrequencyRangeScanPlan


def test_range_scan_plan_uses_catalog_channels():
    catalog = FrequencyCatalog.from_dict(
        {
            "frequency_catalog": {
                "ranges": [
                    {
                        "id": "frs_gmrs",
                        "label": "FRS/GMRS",
                        "min_freq_hz": 462_000_000,
                        "max_freq_hz": 468_000_000,
                        "default_bin_size_hz": 12_500,
                        "channels": [
                            {
                                "id": "frs_7",
                                "label": "FRS Ch 7",
                                "frequency_hz": 462_712_500,
                            },
                            {
                                "id": "frs_8",
                                "label": "FRS Ch 8",
                                "frequency_hz": 467_562_500,
                            },
                        ],
                    }
                ]
            }
        }
    )

    plan = build_frequency_range_scan_plan(catalog=catalog, range_id="frs_gmrs")

    assert plan == FrequencyRangeScanPlan(
        min_freq_hz=462_000_000,
        max_freq_hz=468_000_000,
        channel_frequencies_hz=[462_712_500, 467_562_500],
        channel_width_hz=12_500,
        range_id="frs_gmrs",
    )


def test_range_scan_plan_generates_catalog_grid_without_channels():
    catalog = FrequencyCatalog.from_dict(
        {
            "frequency_catalog": {
                "ranges": [
                    {
                        "id": "local_grid",
                        "label": "Local Grid",
                        "min_freq_hz": 150_000_000,
                        "max_freq_hz": 150_050_000,
                        "default_bin_size_hz": 25_000,
                        "channels": [],
                    }
                ]
            }
        }
    )

    plan = build_frequency_range_scan_plan(catalog=catalog, range_id="local_grid")

    assert plan.min_freq_hz == 150_000_000
    assert plan.max_freq_hz == 150_050_000
    assert plan.channel_width_hz == 25_000
    assert plan.channel_frequencies_hz == [150_000_000, 150_025_000, 150_050_000]


def test_range_scan_plan_generates_arbitrary_grid():
    plan = build_frequency_range_scan_plan(
        catalog=FrequencyCatalog(),
        range_id=None,
        min_freq_hz=150_000_000,
        max_freq_hz=150_050_000,
        step_hz=25_000,
        channel_width_hz=12_500,
    )

    assert plan.min_freq_hz == 150_000_000
    assert plan.max_freq_hz == 150_050_000
    assert plan.channel_width_hz == 12_500
    assert plan.channel_frequencies_hz == [150_000_000, 150_025_000, 150_050_000]


def test_range_scan_plan_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="provided together"):
        build_frequency_range_scan_plan(
            catalog=FrequencyCatalog(),
            min_freq_hz=150_000_000,
            max_freq_hz=None,
        )

    with pytest.raises(ValueError, match="range_id"):
        build_frequency_range_scan_plan(catalog=FrequencyCatalog(), range_id=None)

    with pytest.raises(ValueError, match="step_hz"):
        build_frequency_range_scan_plan(
            catalog=FrequencyCatalog(),
            min_freq_hz=150_000_000,
            max_freq_hz=150_050_000,
            step_hz=0,
        )


def test_session_manager_start_range_scan_delegates_to_frequency_scan(monkeypatch):
    calls = []

    def fake_start_frequency_scan(self, **kwargs):
        calls.append(kwargs)
        return "scan-status"

    monkeypatch.setattr(SessionManager, "start_frequency_scan", fake_start_frequency_scan)
    manager = SessionManager()

    status = manager.start_range_scan(
        range_id="frs_gmrs",
        backend="rtl_sdr_iq",
        duration_sec=0.25,
        sample_rate_hz=240_000,
    )

    assert status == "scan-status"
    assert calls == [
        {
            "path": "rtl_sdr",
            "backend": "rtl_sdr_iq",
            "min_freq_hz": 462_000_000,
            "max_freq_hz": 468_000_000,
            "bin_size_hz": 12_500,
            "duration_sec": 0.25,
            "channel_frequencies_hz": [
                462_562_500,
                462_587_500,
                462_612_500,
                462_637_500,
                462_662_500,
                462_687_500,
                462_712_500,
                467_562_500,
                467_587_500,
                467_612_500,
                467_637_500,
                467_662_500,
                467_687_500,
                467_712_500,
                462_550_000,
                462_575_000,
                462_600_000,
                462_625_000,
                462_650_000,
                462_675_000,
                462_700_000,
                462_725_000,
            ],
            "channel_width_hz": 12_500,
            "gain_db": None,
            "sample_rate_hz": 240_000,
            "baseline": None,
            "resume_previous": False,
        }
    ]


def test_session_manager_capture_range_baseline_delegates_to_frequency_baseline(monkeypatch):
    calls = []

    def fake_capture_frequency_baseline(self, **kwargs):
        calls.append(kwargs)
        return "baseline-status"

    monkeypatch.setattr(
        SessionManager,
        "capture_frequency_baseline",
        fake_capture_frequency_baseline,
    )
    manager = SessionManager()

    status = manager.capture_range_baseline(
        range_id="frs_gmrs",
        duration_sec=10,
    )

    assert status == "baseline-status"
    assert calls[0]["path"] == "rtl_power"
    assert calls[0]["backend"] == "rtl_power"
    assert calls[0]["min_freq_hz"] == 462_000_000
    assert calls[0]["max_freq_hz"] == 468_000_000
    assert calls[0]["bin_size_hz"] == 12_500
    assert calls[0]["duration_sec"] == 10
    assert calls[0]["channel_width_hz"] == 12_500
    assert 462_712_500 in calls[0]["channel_frequencies_hz"]


def test_session_manager_find_active_channels_delegates_to_range_scan(monkeypatch):
    calls = []

    def fake_start_range_scan(self, **kwargs):
        calls.append(kwargs)
        return "channel-scan-status"

    monkeypatch.setattr(SessionManager, "start_range_scan", fake_start_range_scan)
    manager = SessionManager()

    status = manager.find_active_channels(
        range_id="frs_gmrs",
        duration_sec=10,
        gain_db=0,
    )

    assert status == "channel-scan-status"
    assert calls == [
        {
            "range_id": "frs_gmrs",
            "backend": "rtl_power",
            "path": None,
            "duration_sec": 10,
            "channel_width_hz": None,
            "gain_db": 0,
            "sample_rate_hz": None,
            "baseline": None,
            "resume_previous": False,
        }
    ]


def test_session_manager_find_active_channels_requires_catalog_channels():
    catalog = FrequencyCatalog.from_dict(
        {
            "frequency_catalog": {
                "ranges": [
                    {
                        "id": "local_grid",
                        "label": "Local Grid",
                        "min_freq_hz": 150_000_000,
                        "max_freq_hz": 150_050_000,
                        "default_bin_size_hz": 25_000,
                        "channels": [],
                    }
                ]
            }
        }
    )
    manager = SessionManager(frequency_catalog=catalog)

    with pytest.raises(ValueError, match="no catalog channels"):
        manager.find_active_channels(range_id="local_grid")

    with pytest.raises(ValueError, match="range id not found"):
        manager.find_active_channels(range_id="missing")
