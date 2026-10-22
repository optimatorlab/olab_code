# Changelog

All notable changes to `olab-audio` are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-10-21

Adds `MicArray` for multichannel capture, and makes `Mic.stop()` and
`Speaker.stop()` always attempt to close their stream. Validated on real hardware only
for the cases listed below; the rest is tracked in
[#81](https://github.com/optimatorlab/olab_code/issues/81).

**Added:**

- `MicArray`: capture many microphones from one multichannel interface on one
  PortAudio stream (so channels cannot misalign), with per-label levels, peak
  hold, spectrum, subscribers, and an aligned recorder that writes one mono
  32-bit WAV per label plus a JSON manifest. Hardware-agnostic: labels and
  per-channel `meta` pass through untouched. See the README section.
  - The stream is opened at an explicit sample rate (default 48000) and at the
    device's full input width (override with `open_channels`). Opening 14 of
    a UMC1820's 18 inputs overflowed about every 1.4 s; opening all 18 was
    clean.
  - Overflows are zero-filled in every channel using an estimate from the
    buffer timestamps when the timestamps allow one (otherwise logged as
    `flag_only` and not filled), recorded in the manifest as an estimate
    (unreliable when overflows are frequent). Writer-queue drops are exact.
    Unflagged timing gaps are logged, never filled.
  - `lastCallbackTime` exposes stream liveness.
- `get_input_device_info(name=None)`: look up one input device by name
  substring (first match, case-insensitive) and get
  `{'deviceID', 'name', 'maxInputChannels', 'defaultSampleRate'}`, or `None`;
  `name=None` gives the default input device. `deviceID` is the global
  PortAudio index.
- `ONE_OVER_MAX_INT32` constant.

**Fixed:**

- `Mic.stop()` skipped `close()` when `stop_stream()` raised, then dropped its
  only reference to the stream, leaving the PortAudio stream and its device
  descriptor open until `terminate()`. `stop_stream()` and `close()` are now
  attempted separately.
- `Speaker.stop()` is no longer left holding a broken stream when
  `stop_stream()` raises, and `Speaker.play()` no longer leaves its output
  stream open when `volume` is invalid or `write()` fails.

**Changed (failure paths only):**

- A failed `Mic.stop()` can now call `excFunc` twice (`Could not stop stream:
  ...` and the new `Could not close stream: ...`), after both attempts and with
  `mic.stream` already `None`. A raising `excFunc` still propagates. A failure
  of `close()` alone is now reported as `Could not close stream: ...`
  (previously `Could not stop stream: ...`).
- `Speaker.stop()` always attempts `close()` and clears its stream, then
  re-raises the first error (a second `close()` error is dropped).

**Hardware validation (Behringer UMC1820):** a 20 s `MicArray` recording with
no overflows, equal-length files, and a clap recorded on two channels; and
`Mic.stop()` forced to fail on a real stream no longer leaks it. Two bugs found
this way during development, both fixed before release: calling
`Stream.get_time()` on a freshly opened callback stream kills it on this
hardware (`MicArray` never calls it), and opening 14 of the 18 inputs overflowed.
**Not yet validated:** long runs through `MicArray`, the overflow zero-fill
path on a healthy stream, `startedAtWall` accuracy, a full 12-16 channel array,
operation under load, and unplug/replug during a take.

## [0.1.0] - 2026-10-06

First PyPI release. Audio device I/O (Mic/Speaker/recording, device
enumeration, PulseAudio control) plus an optional DSP/analysis/teaching
toolkit.
