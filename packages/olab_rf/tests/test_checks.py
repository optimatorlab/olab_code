from __future__ import annotations

from olab_rf.services.checks import environment_check


def test_environment_check_has_expected_top_level_keys(monkeypatch):
    class _Probe:
        devices = []
        errors = []

        def to_dict(self):
            return {"devices": [], "errors": [], "tuner": None}

    monkeypatch.setattr("olab_rf.services.checks.probe_rtlsdr", lambda: _Probe())
    monkeypatch.setattr("olab_rf.services.checks.find_rtlsdr_usb_devices", lambda: [])
    monkeypatch.setattr("olab_rf.services.checks.loaded_dvb_modules", lambda: [])

    payload = environment_check()

    assert {
        "context",
        "tools",
        "rtlsdr_devices",
        "rtlsdr_usb_devices",
        "rtlsdr_probe",
        "kernel_dvb_modules",
        "warnings",
        "sdrtrunk",
    } <= set(payload)


def test_environment_check_reports_sdrtrunk_readiness_paths(tmp_path, monkeypatch):
    class _Probe:
        devices = []
        errors = []

        def to_dict(self):
            return {"devices": [], "errors": [], "tuner": None}

    launcher = tmp_path / "sdr-trunk"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    java = tmp_path / "java"
    java.write_text("#!/bin/sh\n", encoding="utf-8")
    java.chmod(0o755)
    profile = tmp_path / "probe.xml"
    profile.write_text("<playlist />\n", encoding="utf-8")
    jmbe = tmp_path / "jmbe"
    jmbe.mkdir()
    monkeypatch.setattr("olab_rf.services.checks.probe_rtlsdr", lambda: _Probe())
    monkeypatch.setattr("olab_rf.services.checks.find_rtlsdr_usb_devices", lambda: [])
    monkeypatch.setattr("olab_rf.services.checks.loaded_dvb_modules", lambda: [])

    payload = environment_check(
        sdrtrunk_paths={
            "launcher_path": str(launcher),
            "java_path": str(java),
            "working_directory": str(tmp_path),
            "profile_path": str(profile),
            "jmbe_path": str(jmbe),
        }
    )

    assert payload["sdrtrunk"] == {
        "launcher": {
            "name": "sdrtrunk",
            "found": True,
            "path": str(launcher),
            "executable": True,
        },
        "java": {"name": "java", "found": True, "path": str(java), "executable": True},
        "working_directory": {
            "configured": True,
            "path": str(tmp_path),
            "found": True,
            "valid_type": True,
        },
        "profile": {
            "configured": True,
            "path": str(profile),
            "found": True,
            "valid_type": True,
        },
        "jmbe": {"configured": True, "path": str(jmbe), "found": True, "valid_type": True},
    }


def test_environment_check_uses_configured_tool_path(tmp_path, monkeypatch):
    class _Probe:
        devices = []
        errors = []

        def to_dict(self):
            return {"devices": [], "errors": [], "tuner": None}

    readsb = tmp_path / "readsb"
    readsb.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("olab_rf.services.checks.probe_rtlsdr", lambda: _Probe())
    monkeypatch.setattr("olab_rf.services.checks.find_rtlsdr_usb_devices", lambda: [])
    monkeypatch.setattr("olab_rf.services.checks.loaded_dvb_modules", lambda: [])

    payload = environment_check(tool_paths={"readsb": str(readsb)})

    tools = {tool["name"]: tool for tool in payload["tools"]}
    assert tools["readsb"]["found"] is True
    assert tools["readsb"]["path"] == str(readsb)
    assert tools["readsb"]["executable"] is False
    assert payload["context"]["local_config_found"] is False


def test_environment_check_reports_context(tmp_path, monkeypatch):
    class _Probe:
        devices = []
        errors = []

        def to_dict(self):
            return {"devices": [], "errors": [], "tuner": None}

    monkeypatch.chdir(tmp_path)
    (tmp_path / "olab_rf.yaml").write_text("history: {}\n", encoding="utf-8")
    monkeypatch.setattr("olab_rf.services.checks.probe_rtlsdr", lambda: _Probe())
    monkeypatch.setattr("olab_rf.services.checks.find_rtlsdr_usb_devices", lambda: [])
    monkeypatch.setattr("olab_rf.services.checks.loaded_dvb_modules", lambda: [])

    payload = environment_check(config_path="olab_rf.yaml")

    assert payload["context"] == {
        "cwd": str(tmp_path),
        "config_path": "olab_rf.yaml",
        "local_config_found": True,
    }


def test_environment_check_can_skip_rtlsdr_probe(monkeypatch):
    def fail_probe():
        raise AssertionError("probe should be skipped")

    monkeypatch.setattr("olab_rf.services.checks.probe_rtlsdr", fail_probe)
    monkeypatch.setattr("olab_rf.services.checks.find_rtlsdr_usb_devices", lambda: [])
    monkeypatch.setattr("olab_rf.services.checks.loaded_dvb_modules", lambda: [])

    payload = environment_check(skip_rtlsdr_probe_reason="adsb session is running")

    assert payload["rtlsdr_probe"]["skipped"] == "adsb session is running"
