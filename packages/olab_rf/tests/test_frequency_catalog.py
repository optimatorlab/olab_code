from __future__ import annotations

from olab_rf.services.frequency_catalog import FrequencyCatalog


def test_default_frequency_catalog_includes_walkie_channels():
    catalog = FrequencyCatalog.default()

    frs = catalog.range_by_id("frs_gmrs")

    assert frs is not None
    assert frs.default_bin_size_hz == 12_500
    assert any(channel.label == "FRS/GMRS Ch 3" for channel in frs.channels)


def test_frequency_catalog_merges_overrides_by_id_and_matches_channels():
    catalog = FrequencyCatalog.merged(
        override_payload={
            "ranges": [
                {
                    "id": "frs_gmrs",
                    "label": "Local walkies",
                    "min_freq_hz": 462000000,
                    "max_freq_hz": 468000000,
                    "default_modulation": "NFM",
                    "default_bin_size_hz": 12500,
                    "channels": [
                        {
                            "id": "local_ch",
                            "label": "Local Ch",
                            "frequency_hz": 462612500,
                            "modulation": "NFM",
                        }
                    ],
                },
                {
                    "id": "local_range",
                    "label": "Local range",
                    "min_freq_hz": 100,
                    "max_freq_hz": 200,
                },
            ]
        }
    )

    assert catalog.range_by_id("frs_gmrs").label == "Local walkies"
    assert catalog.range_by_id("local_range").label == "Local range"
    match = catalog.match_frequency(462_612_500)
    assert match.label == "Local Ch"
    assert match.channel_id == "local_ch"


def test_frequency_catalog_favorites_take_label_priority():
    catalog = FrequencyCatalog.default().with_favorites(
        [{"frequency_hz": 462612500, "label": "My radio", "modulation": "NFM"}]
    )

    match = catalog.match_frequency(462_612_500)

    assert match.label == "My radio"
    assert match.channel_label == "FRS/GMRS Ch 3"
