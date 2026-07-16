from __future__ import annotations

import pytest

from olab_rf.decoders.rtl_ais import parse_ais_nmea_line, rtl_ais_command


def test_parse_ais_nmea_line_normalizes_position_report():
    pytest.importorskip("pyais", reason="pyais is not installed; install olab-rf[ais]")
    message = parse_ais_nmea_line(
        "!AIVDM,1,1,,A,15Muq@002>G?svP00<:O?vN60<0,0*5C",
        sensor_id="rtlsdr-1",
        session_id="session-1",
    )

    assert message is not None
    assert message.track.track_id == "ais-366967104"
    assert message.track.domain == "marine"
    assert message.track.speed_mps > 7
    assert message.observation.raw.startswith("!AIVDM")


def test_parse_ais_nmea_line_ignores_non_ais_line():
    assert parse_ais_nmea_line("not ais", sensor_id="rtlsdr-1", session_id="session-1") is None


def test_rtl_ais_command_includes_serial():
    assert rtl_ais_command(device_index=0, ppm=2) == ["rtl_ais", "-n", "-d", "0", "-p", "2"]
