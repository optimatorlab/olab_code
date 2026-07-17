from __future__ import annotations

from olab_rf.config import config_from_dict


def test_config_from_dict_loads_receiver_and_history():
    config = config_from_dict(
        {
            "receivers": [{"id": "roof-rtlsdr", "serial": "00000002", "ppm": 1}],
            "history": {"sqlite_path": "tmp/test.sqlite", "trail_retention_hours": 2},
        }
    )

    assert config.receivers[0].id == "roof-rtlsdr"
    assert config.receivers[0].serial == "00000002"
    assert config.history.trail_retention_hours == 2


def test_config_from_dict_preserves_default_decoders_when_overriding_one():
    config = config_from_dict({"decoders": {"readsb": {"path": "external/readsb/readsb"}}})

    assert config.decoders["readsb"].path == "external/readsb/readsb"
    assert config.decoders["rtl_ais"].path == "rtl_ais"
    assert config.decoders["rtl_power"].path == "rtl_power"
    assert config.decoders["rtl_fm"].path == "rtl_fm"


def test_config_from_dict_keeps_frequency_catalog_payload():
    config = config_from_dict(
        {
            "frequency_catalog": {
                "ranges": [
                    {
                        "id": "local_test",
                        "label": "Local test",
                        "min_freq_hz": 100,
                        "max_freq_hz": 200,
                    }
                ]
            }
        }
    )

    assert config.frequency_catalog["ranges"][0]["id"] == "local_test"


def test_config_from_dict_loads_sdrtrunk_readiness_paths():
    config = config_from_dict(
        {
            "sdrtrunk": {
                "launcher_path": "/opt/sdrtrunk/bin/sdr-trunk",
                "java_path": "/opt/sdrtrunk/bin/java",
                "working_directory": "/opt/sdrtrunk",
                "profile_path": "/home/operator/SDRTrunk/playlist/probe.xml",
                "jmbe_path": "/home/operator/SDRTrunk/jmbe",
            }
        }
    )

    assert config.sdrtrunk.to_dict() == {
        "launcher_path": "/opt/sdrtrunk/bin/sdr-trunk",
        "java_path": "/opt/sdrtrunk/bin/java",
        "working_directory": "/opt/sdrtrunk",
        "profile_path": "/home/operator/SDRTrunk/playlist/probe.xml",
        "jmbe_path": "/home/operator/SDRTrunk/jmbe",
    }
