from __future__ import annotations

import pytest

from olab_rf.decoders.rtl_fm import (
    RtlFmArgumentError,
    rtl_fm_audio_rate_hz,
    rtl_fm_command,
    validate_rtl_fm_passthrough,
)


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
        "-",
    ]


def test_rtl_fm_command_builds_am_preview():
    command = rtl_fm_command(frequency_hz=121_500_000, modulation="AM")

    assert ["-M", "am"] == command[command.index("-M") : command.index("-M") + 2]
    assert ["-s", "12000"] == command[command.index("-s") : command.index("-s") + 2]


def test_rtl_fm_audio_rate_defaults_by_modulation():
    assert rtl_fm_audio_rate_hz("AM") == 12_000
    assert rtl_fm_audio_rate_hz("NFM") == 24_000
    # wbfm is a preset whose real output rate is its implied -r 32k, not -s.
    assert rtl_fm_audio_rate_hz("WFM") == 32_000


def test_wbfm_preset_is_not_overridden_by_a_default_sample_rate():
    command = rtl_fm_command(frequency_hz=98_500_000, modulation="WFM")

    assert ["-M", "wbfm"] == command[command.index("-M") : command.index("-M") + 2]
    assert "-s" not in command  # would override the preset's -s 170k
    assert command[-1] == "-"


def test_explicit_sample_rate_still_reaches_wbfm():
    command = rtl_fm_command(frequency_hz=98_500_000, modulation="WFM", sample_rate_hz=48_000)

    assert ["-s", "48000"] == command[command.index("-s") : command.index("-s") + 2]


def test_wbfm_below_the_preset_resample_rate_is_refused():
    """-M wbfm with -s below 32k makes rtl_fm divide by zero and die on SIGFPE.

    Verified against the installed binary: 16000/24000 crash, 32000/48000 do not.
    Refusing beats emitting a command that segfaults the receiver.
    """
    for rate in (16_000, 24_000):
        with pytest.raises(RtlFmArgumentError, match="32000"):
            rtl_fm_command(frequency_hz=98_500_000, modulation="WFM", sample_rate_hz=rate)


def test_typed_flags_are_validated_and_atan_follows_modulation():
    command = rtl_fm_command(
        frequency_hz=462_612_500, modulation="WFM", fir_size=9, atan_math="std"
    )

    # -A after -M so an explicit value wins over the wbfm preset's -A fast.
    assert command.index("-A") > command.index("-M")
    assert ["-F", "9"] == command[command.index("-F") : command.index("-F") + 2]
    with pytest.raises(RtlFmArgumentError):
        rtl_fm_command(frequency_hz=1, fir_size=5)
    with pytest.raises(RtlFmArgumentError):
        rtl_fm_command(frequency_hz=1, atan_math="turbo")


def test_passthrough_admits_only_flags_that_cannot_break_the_pipeline():
    assert validate_rtl_fm_passthrough(["-T", "-o", "4", "-E", "offset"]) == [
        "-T", "-o", "4", "-E", "offset",
    ]


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        # Each case breaks a different stated invariant, which is why there are
        # four rather than four variations of one.
        (["out.raw"], "stdout contract"),
        (["-r", "8000"], "declared sample rate"),
        (["-E", "deemp"], "conditioning the detector assumes absent"),
        (["-l", "50"], "squelch the gate depends on"),
        (["-F", "9"], "owned by a typed field"),
    ],
)
def test_passthrough_rejects_rather_than_silently_dropping(args, reason):
    with pytest.raises(RtlFmArgumentError):
        validate_rtl_fm_passthrough(args)


def test_passthrough_valued_flag_rejects_a_missing_value():
    # ["-o", "-T"] previously validated: the value slot was consumed unchecked, so
    # the validator reported success on input it had not understood.
    with pytest.raises(RtlFmArgumentError):
        validate_rtl_fm_passthrough(["-o", "-T"])
    with pytest.raises(RtlFmArgumentError):
        validate_rtl_fm_passthrough(["-o"])


def test_passthrough_reaches_the_command_line_before_the_stdout_filename():
    command = rtl_fm_command(frequency_hz=1, extra_args=["-T"])

    assert command[-2:] == ["-T", "-"]
