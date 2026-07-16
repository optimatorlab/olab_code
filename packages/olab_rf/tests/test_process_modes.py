from __future__ import annotations

import os
import json
import textwrap
import time

import pytest

from olab_rf.config import config_from_dict
from olab_rf.models import ReceiverConfig
from olab_rf.services.session_manager import SessionManager


def test_start_adsb_reports_missing_tool():
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1"))

    with pytest.raises(RuntimeError, match="not found"):
        manager.start_adsb(path="/tmp/olab-rf-definitely-missing-readsb")

    assert manager.status.error


def test_start_adsb_launches_and_stops_process(tmp_path):
    script = tmp_path / "fake-readsb"
    script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))

    session = manager.start_adsb(path=str(script))

    assert session.mode == "adsb"
    assert session.command
    assert session.command[0] == str(script)
    assert manager.status.process_running is True
    manager.stop()
    assert manager.status.process_running is False


def test_start_adsb_uses_configured_path_and_temporary_json_dir(tmp_path):
    script = tmp_path / "fake-readsb"
    script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    os.chmod(script, 0o755)
    config = config_from_dict(
        {
            "receivers": [{"id": "rtlsdr-1", "serial": "00000001"}],
            "decoders": {"readsb": {"path": str(script)}},
        }
    )
    manager = SessionManager.from_config(config)

    session = manager.start_adsb()

    assert session.command
    assert session.command[0] == str(script)
    json_dir = session.command[session.command.index("--write-json") + 1]
    assert os.path.isdir(json_dir)
    assert "olab-rf-readsb-" in json_dir
    manager.stop()
    assert not os.path.exists(json_dir)


def test_start_adsb_preserves_explicit_json_dir(tmp_path):
    script = tmp_path / "fake-readsb"
    script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    os.chmod(script, 0o755)
    json_dir = tmp_path / "readsb-json"
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))

    session = manager.start_adsb(path=str(script), write_json_dir=json_dir)

    assert session.command
    assert session.command[session.command.index("--write-json") + 1] == str(json_dir)
    manager.stop()
    assert json_dir.exists()


def test_ingest_adsb_json_updates_tracks_without_hardware(tmp_path):
    script = tmp_path / "fake-readsb"
    script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    os.chmod(script, 0o755)
    json_dir = tmp_path / "readsb-json"
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))
    manager.start_adsb(path=str(script), write_json_dir=json_dir)
    (json_dir / "aircraft.json").write_text(
        json.dumps(
            {
                "aircraft": [
                    {
                        "hex": "a1b2c3",
                        "flight": "TEST123",
                        "lat": 40.1,
                        "lon": -73.9,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert manager.ingest_adsb_json() == 1
    assert manager.track_store.get("adsb-a1b2c3").label == "TEST123"
    assert manager.status.message_count == 1
    manager.stop()


def test_adsb_poll_reports_process_stderr_on_exit(tmp_path):
    script = tmp_path / "fake-readsb"
    script.write_text(
        "#!/bin/sh\n"
        "echo 'FATAL: rtlsdr: error opening the RTLSDR device: Device or resource busy' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))

    manager.start_adsb(path=str(script), write_json_dir=tmp_path / "readsb-json")
    time.sleep(0.05)
    status = manager.poll()

    assert status.process_running is False
    assert manager.session.status == "stopped"
    assert status.error == "FATAL: rtlsdr: error opening the RTLSDR device: Device or resource busy"
    manager.stop()


def test_start_ais_uses_configured_path(tmp_path):
    script = tmp_path / "fake-rtl-ais"
    script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    os.chmod(script, 0o755)
    config = config_from_dict(
        {
            "receivers": [{"id": "rtlsdr-1", "serial": "00000001"}],
            "decoders": {"rtl_ais": {"path": str(script)}},
        }
    )
    manager = SessionManager.from_config(config)

    session = manager.start_ais()

    assert session.command
    assert session.command[0] == str(script)
    manager.stop()


def test_ingest_ais_stdout_updates_tracks(tmp_path):
    pytest.importorskip("pyais", reason="pyais is not installed; install olab-rf[ais]")
    script = tmp_path / "fake-rtl-ais"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '!AIVDM,1,1,,A,15Muq@002>G?svP00<:O?vN60<0,0*5C' >&2
            sleep 30
            """
        ),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))
    manager.start_ais(path=str(script))

    count = 0
    for _ in range(10):
        count = manager.ingest_ais_stdout()
        if count:
            break
        time.sleep(0.05)

    assert count == 1
    assert manager.track_store.get("ais-366967104").domain == "marine"
    manager.stop()


def test_ais_poll_reports_process_stderr_on_exit(tmp_path):
    script = tmp_path / "fake-rtl-ais"
    script.write_text(
        "#!/bin/sh\n"
        "echo 'usb_claim_interface error -6' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))

    manager.start_ais(path=str(script))
    time.sleep(0.05)
    status = manager.poll()

    assert status.process_running is False
    assert manager.session.status == "stopped"
    assert status.error == "usb_claim_interface error -6"
    manager.stop()
