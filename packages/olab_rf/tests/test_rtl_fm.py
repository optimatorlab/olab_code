from __future__ import annotations

from olab_rf.decoders.rtl_fm import rtl_fm_audio_rate_hz, rtl_fm_command


def test_rtl_fm_command_builds_nfm_preview():
    command = rtl_fm_command(
        path="/usr/bin/rtl_fm",
        frequency_hz=462_612_500,
        modulation="NFM",
        ppm=1,
        gain_db=19.7,
    )

    assert command == [
        "/usr/bin/rtl_fm",
        "-d",
        "0",
        "-f",
        "462612500",
        "-M",
        "fm",
        "-s",
        "24000",
        "-p",
        "1",
        "-g",
        "19.7",
    ]


def test_rtl_fm_command_builds_am_preview():
    command = rtl_fm_command(frequency_hz=121_500_000, modulation="AM")

    assert ["-M", "am"] == command[command.index("-M") : command.index("-M") + 2]
    assert ["-s", "12000"] == command[command.index("-s") : command.index("-s") + 2]


def test_rtl_fm_audio_rate_defaults_by_modulation():
    assert rtl_fm_audio_rate_hz("AM") == 12_000
    assert rtl_fm_audio_rate_hz("NFM") == 24_000
