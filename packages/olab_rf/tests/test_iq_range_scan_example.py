from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

from olab_rf.services.frequency_catalog import FrequencyCatalog


def _load_example_module():
    path = Path(__file__).resolve().parents[1] / "examples" / "iq_range_scan.py"
    spec = importlib.util.spec_from_file_location("iq_range_scan_example", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_iq_range_plan_uses_catalog_channels():
    module = _load_example_module()
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

    plan = module.build_iq_range_plan(
        catalog=catalog,
        range_id="frs_gmrs",
        min_hz=None,
        max_hz=None,
        step_hz=None,
        channel_width_hz=None,
        channel_hz=None,
    )

    assert plan.min_hz == 462_000_000
    assert plan.max_hz == 468_000_000
    assert plan.channel_width_hz == 12_500
    assert plan.channel_frequencies_hz == [462_712_500, 467_562_500]


def test_iq_range_plan_generates_arbitrary_grid():
    module = _load_example_module()

    plan = module.build_iq_range_plan(
        catalog=FrequencyCatalog(),
        range_id="unused",
        min_hz=150_000_000,
        max_hz=150_050_000,
        step_hz=25_000,
        channel_width_hz=12_500,
        channel_hz=None,
    )

    assert plan.min_hz == 150_000_000
    assert plan.max_hz == 150_050_000
    assert plan.channel_width_hz == 12_500
    assert plan.channel_frequencies_hz == [150_000_000, 150_025_000, 150_050_000]


def test_iq_range_plan_rejects_partial_range():
    module = _load_example_module()

    with pytest.raises(ValueError, match="provided together"):
        module.build_iq_range_plan(
            catalog=FrequencyCatalog(),
            range_id="unused",
            min_hz=150_000_000,
            max_hz=None,
            step_hz=None,
            channel_width_hz=None,
            channel_hz=None,
        )
