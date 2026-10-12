from __future__ import annotations

FIR_SIZES = (0, 9)
ATAN_MATHS = ("std", "fast", "lut")

# Flags a caller may pass through verbatim via ``DecoderConfig.args``.
#
# This is an allowlist rather than a denylist on purpose: a denylist admits every
# flag a future rtl_fm release adds, and so silently stops being correct. The
# allowlist fails closed.
#
# Invariant it serves: *the passthrough may not alter anything the capture
# pipeline's correctness depends on* -- the stdout contract, the declared sample
# rate, the absence of demodulator-side conditioning the carrier detector assumes,
# or the squelch state the gate depends on. Judge a new flag against that rule,
# not against this list.
_PASSTHROUGH_BOOLEAN = ("-T",)
_PASSTHROUGH_VALUED = ("-o",)
_PASSTHROUGH_ENABLE_OPTIONS = ("edge", "offset")

# Why each rejected flag is rejected, surfaced verbatim in the error.
_PASSTHROUGH_REJECTED = {
    "-l": "re-enables rtl_fm squelch, which suppresses the FM-quieting hiss the "
          "carrier gate measures against; use the Python gate instead",
    "-t": "squelch delay is meaningful only with -l, which is rejected",
    "-r": "resamples the output, desynchronising the actual sample rate from the "
          "declared one; no downstream guard can detect this",
    "-E": "only 'edge' and 'offset' are permitted; 'deemp' and 'dc' apply "
          "demodulator-side conditioning the carrier detector assumes is absent",
    "-f": "owned by the frequency argument",
    "-M": "owned by the modulation argument",
    "-s": "owned by the sample rate argument",
    "-d": "owned by the device index argument",
    "-g": "owned by the gain argument",
    "-p": "owned by the ppm argument",
    "-F": "owned by the fir_size argument",
    "-A": "owned by the atan_math argument",
}


class RtlFmArgumentError(ValueError):
    """Raised when rtl_fm arguments are invalid or unsafe to pass through."""


def validate_rtl_fm_passthrough(args: list[str] | tuple[str, ...] | None) -> list[str]:
    """Return ``args`` unchanged, or raise explaining which token is unacceptable.

    Rejects rather than silently dropping: a passthrough that quietly ignores its
    input is the trap this validation exists to remove.
    """
    if not args:
        return []
    tokens = list(args)
    validated: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-") or token == "-":
            raise RtlFmArgumentError(
                f"rtl_fm passthrough rejects {token!r}: bare arguments are taken as an "
                "output filename, which would divert PCM away from stdout and stall "
                "capture with no error"
            )
        if token in _PASSTHROUGH_REJECTED:
            if token == "-E":
                value = tokens[index + 1] if index + 1 < len(tokens) else None
                if value in _PASSTHROUGH_ENABLE_OPTIONS:
                    validated.extend([token, value])
                    index += 2
                    continue
            raise RtlFmArgumentError(
                f"rtl_fm passthrough rejects {token!r}: {_PASSTHROUGH_REJECTED[token]}"
            )
        if token in _PASSTHROUGH_BOOLEAN:
            validated.append(token)
            index += 1
            continue
        if token in _PASSTHROUGH_VALUED:
            if index + 1 >= len(tokens):
                raise RtlFmArgumentError(f"rtl_fm passthrough flag {token!r} requires a value")
            value = tokens[index + 1]
            if value.startswith("-"):
                raise RtlFmArgumentError(
                    f"rtl_fm passthrough flag {token!r} expects a value, got {value!r}"
                )
            validated.extend([token, value])
            index += 2
            continue
        raise RtlFmArgumentError(
            f"rtl_fm passthrough rejects {token!r}: not in the permitted set "
            f"{sorted(_PASSTHROUGH_BOOLEAN + _PASSTHROUGH_VALUED)} plus "
            f"-E {list(_PASSTHROUGH_ENABLE_OPTIONS)}"
        )
    return validated


def rtl_fm_audio_rate_hz(modulation: str, sample_rate_hz: int | None = None) -> int:
    """Return the rate rtl_fm will actually emit, which is not always ``-s``.

    ``-M wbfm`` is a preset (``-M fm -s 170k -o 4 -A fast -r 32k -l 0 -E deemp``);
    with no explicit override its *output* rate is the preset's ``-r 32k``, not the
    ``-s`` value. Reporting 24 kHz there would desynchronise every frame-size and
    duration calculation downstream.
    """
    if sample_rate_hz is not None:
        return sample_rate_hz
    mode = _rtl_fm_mode(modulation)
    if mode == "am":
        return 12_000
    if mode == "wbfm":
        return 32_000
    return 24_000


def rtl_fm_command(
    *,
    path: str = "rtl_fm",
    frequency_hz: int,
    modulation: str = "nfm",
    device_index: int = 0,
    ppm: int | None = None,
    gain_db: float | None = None,
    sample_rate_hz: int | None = None,
    squelch_db: int | None = None,
    fir_size: int | None = None,
    atan_math: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build an rtl_fm invocation that writes PCM to stdout.

    ``squelch_db`` is retained for scan-mode callers -- rtl_fm's multi-``-f``
    scanning requires ``-l`` -- but the voice capture path must not set it, since
    squelching removes the hiss the carrier gate detects quieting against.
    """
    mode = _rtl_fm_mode(modulation)
    if mode == "wbfm" and sample_rate_hz is not None and sample_rate_hz < 32_000:
        # The wbfm preset carries an implied -r 32k. A lower -s makes rtl_fm's
        # resampler divide by an integer zero and die on SIGFPE, so refuse loudly
        # rather than emit a command that crashes.
        raise RtlFmArgumentError(
            f"wbfm requires sample_rate_hz >= 32000 (the preset resamples to 32k); got {sample_rate_hz}"
        )
    command = [path, "-d", str(device_index), "-f", str(frequency_hz), "-M", mode]

    # For wbfm with no explicit rate, leave the preset's -s 170k alone. Appending
    # -s 24000 after -M wbfm overrode the preset's input rate while leaving its
    # implied -r 32k in place, which is not a configuration anyone asked for.
    if sample_rate_hz is not None or mode != "wbfm":
        command.extend(["-s", str(rtl_fm_audio_rate_hz(modulation, sample_rate_hz))])
    if ppm is not None:
        command.extend(["-p", str(ppm)])
    if gain_db is not None:
        command.extend(["-g", f"{gain_db:g}"])
    if squelch_db is not None:
        command.extend(["-l", str(squelch_db)])
    if fir_size is not None:
        if fir_size not in FIR_SIZES:
            raise RtlFmArgumentError(f"fir_size must be one of {list(FIR_SIZES)}, got {fir_size!r}")
        command.extend(["-F", str(fir_size)])
    if atan_math is not None:
        if atan_math not in ATAN_MATHS:
            raise RtlFmArgumentError(
                f"atan_math must be one of {list(ATAN_MATHS)}, got {atan_math!r}"
            )
        # Emitted after -M so an explicit value wins over the wbfm preset's -A fast.
        command.extend(["-A", atan_math])
    command.extend(validate_rtl_fm_passthrough(extra_args))

    # Explicit stdout filename. rtl_fm's usage is `rtl_fm -f freq [-options]
    # [filename]`, so this makes the stdout contract independent of passthrough
    # validation rather than resting on it alone.
    command.append("-")
    return command


def _rtl_fm_mode(modulation: str) -> str:
    normalized = modulation.strip().lower()
    if normalized in {"am", "airband", "aviation"}:
        return "am"
    if normalized in {"wfm", "widefm", "broadcast_fm"}:
        return "wbfm"
    return "fm"
