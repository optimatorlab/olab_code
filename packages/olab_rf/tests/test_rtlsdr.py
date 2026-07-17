from __future__ import annotations

from dataclasses import dataclass

from olab_rf.receivers.rtlsdr import probe_rtlsdr, probe_rtlsdr_devices


@dataclass(slots=True)
class _Result:
    stdout: str = ""
    stderr: str = ""


def test_probe_rtlsdr_devices_parses_rtl_test_output(monkeypatch):
    def fake_run(*args, **kwargs):
        return _Result(
            stderr="""
Found 2 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: 00000001
  1:  Realtek, RTL2838UHIDIR, SN: 00000002
"""
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    devices = probe_rtlsdr_devices()

    assert devices[0].index == 0
    assert devices[0].serial == "00000001"
    assert devices[1].serial == "00000002"


def test_probe_rtlsdr_ignores_malformed_device_line_and_reports_open_error(monkeypatch):
    def fake_run(*args, **kwargs):
        return _Result(
            stderr=(
                "Found 1 device(s):\n"
                "  0:  \ufffd\ufffd, P\ufffd, SN: h\x15bad\n"
                "Using device 0: Generic RTL2832U OEM\n"
                "usb_open error -4\n"
                "Failed to open rtlsdr device #0.\n"
            )
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    probe = probe_rtlsdr()

    assert probe.devices == []
    assert "usb_open error -4" in probe.errors
