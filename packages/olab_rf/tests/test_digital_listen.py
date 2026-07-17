from __future__ import annotations

import pytest

from olab_rf.config import config_from_dict
from olab_rf.services.session_manager import SessionManager


def _config(tmp_path):
    launcher = tmp_path / "sdr-trunk"
    launcher.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    launcher.chmod(0o755)
    profile = tmp_path / "probe.xml"
    profile.write_text("<playlist />\n", encoding="utf-8")
    jmbe = tmp_path / "jmbe"
    jmbe.mkdir()
    return config_from_dict({
        "sdrtrunk": {"launcher_path": str(launcher), "working_directory": str(tmp_path), "profile_path": str(profile), "jmbe_path": str(jmbe)},
        "digital_system_catalog": {"systems": [{"id": "probe", "backend": "sdrtrunk", "mode": "profile", "sdrtrunk_profile_path": str(profile)}]},
    })


def test_start_digital_listen_launches_profile_backend(tmp_path):
    manager = SessionManager.from_config(_config(tmp_path))
    session = manager.start_digital_listen(system_id="probe")
    assert session.mode == "digital_listen"
    assert manager.current_digital_listen_status().profile_found is True
    assert manager.status.process_running is True
    manager.stop()
    assert manager.current_digital_listen_status().state == "stopped"


def test_start_digital_listen_rejects_unknown_system(tmp_path):
    manager = SessionManager.from_config(_config(tmp_path))
    with pytest.raises(RuntimeError, match="unknown digital system"):
        manager.start_digital_listen(system_id="missing")


def test_start_digital_listen_rejects_non_profile_system(tmp_path):
    config = _config(tmp_path)
    config.digital_system_catalog["systems"][0]["mode"] = "conventional"
    manager = SessionManager.from_config(config)
    with pytest.raises(RuntimeError, match="only sdrtrunk profile"):
        manager.start_digital_listen(system_id="probe")
