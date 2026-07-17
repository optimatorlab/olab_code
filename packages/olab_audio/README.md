# olab_audio

Audio device I/O (`Mic`, `Speaker`, recording, device enumeration,
PulseAudio port control) — the core install needs only `pyaudio`,
`pulsectl`, and `numpy` — plus two optional extras: `resample` (lightweight
cross-rate PCM conversion) and `analysis` (a DSP/teaching/research toolkit:
`Wave`, `Spectrogram`, `Spectrum`, tone/chirp/pitch synthesis, `trim`,
`normalize`, matplotlib plotting).

Extracted from `~/Projects/ofm/ofm/sensor/ub_audio.py` per
[`docs/plans/olab_packages_reorg_plan.md`](../../docs/plans/olab_packages_reorg_plan.md)'s
"`olab_audio` v1 scope" section and Migration sequence step 5.

## Installing

Normal installation (no `olab_code` checkout required):

```bash
python3 -m venv venv
source venv/bin/activate
pip install "olab-audio @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_audio"
```

Add `resample` and/or `analysis` as needed:
`pip install "olab-audio[analysis] @ git+...#subdirectory=packages/olab_audio"`.

Once release wheels exist, prefer pinning the release's exact URL and
SHA-256 hash instead of a git reference.

**Local development**, against an `olab_code` checkout:

```bash
pip install -e "packages/olab_audio[analysis]"
```

## Extras

| Extra | Adds | Needed for |
|---|---|---|
| *(core, default)* | `pyaudio`, `pulsectl`, `numpy` | `Mic`/`Speaker`/recording at the device's own native rate, device enumeration, PulseAudio port control. |
| `resample` | `soxr` | Cross-rate recording (`Mic.recordStart(samplerateRec=...)` at a rate other than the mic's native one) and `olab_audio.resample`'s `resample()`/`StreamResampler`. Not yet validated on target Raspberry Pi hardware — see the plan doc's acceptance checklist. |
| `analysis` | `resample` (soxr) + `librosa`, `soundfile`, `matplotlib` | `olab_audio.analysis`'s `Wave`/`Spectrogram`/`Spectrum`, tone/chirp/pitch synthesis, `trim`, `read_wave_librosa`, plotting, `Recording.make_wave()`, and `Recording_np`'s explicit `.resample()` method. |

Recording at the microphone's own native sample rate — the default, and
almost always what you want — needs **only the core install**. Cross-rate
recording fails fast and clearly at `recordStart()` time (not from inside
the audio callback thread) if `resample` isn't installed.

**API parity with the original `ub_audio` module**: every `analysis`-extra
symbol (`Wave`, `Spectrum`, `Spectrogram`, `createTone`, `trim`,
`read_wave`, `pitch_map`, etc.) is available directly at `olab_audio.<name>`
— not just `olab_audio.analysis.<name>` — via lazy module `__getattr__`.
`olab_audio.analysis` is only actually imported the first time one of
those names is accessed, so a core-only install never pays for it, but
`[analysis]` installed gives you the same flat namespace the original
module had. `resample()` (the function) is always at `olab_audio.resample`
directly, matching the original API exactly — the backend module itself is
named `olab_audio._resample` (private) specifically to avoid that name
colliding with the function.

## Quick start

```python
import olab_audio

mics = olab_audio.get_input_devices()  # ALSA pseudo-device plugins (e.g. 'vdownmix') filtered out
mic = olab_audio.Mic(deviceID=mics[0]['deviceID'])
mic.start()  # queries the device's own default sample rate if none is given

mic.recordStart(filename="test.wav")
# ... let it capture some audio ...
mic.recordStop()

mic.stop()
```

## TLS/security note

Unlike `olab_camera`, `olab_audio` has no network-facing streaming server
in v1 — see the plan doc's "`olab_audio` v1 scope" item 5 (resolved: no
`Camera`-style network streaming in v1, deferred to v2). No TLS/cert
concerns apply here.

## Known bugs fixed during this migration

Found in the original `ub_audio.py` (which had zero automated tests) and
fixed here, not just carried forward:

- **ALSA pseudo-device segfault risk**: `get_input_devices()`/
  `get_output_devices()`/`get_connected_devices()` now filter to real
  hardware (`hw:`-named) devices plus the safe `default`/`pipewire`/`pulse`
  aliases — never offering resampling/mixing plugins (`vdownmix`,
  `sysdefault`, `lavrate`, etc.) as selectable inputs, since opening one as
  a capture stream is a C-level segfault `try`/`except` cannot catch.
- **Hardcoded 44.1kHz default sample rate**: `Mic.start()` now queries the
  device's own reported default rate when none is given, instead of
  assuming 44100Hz universally (some hardware, e.g. certain USB mics, only
  supports other rates).
- **`Mic.start()` failure left a half-open object**: `self.stream` is now
  initialized to `None` and guarded everywhere it's used, so `.stop()` is
  always safe to call — including after a failed `.start()` — and is
  idempotent.
- **`get_connected_devices()`'s `maxInputChannels` bug**: it was populated
  with the `maxOutputChannels` value (a copy-paste bug), not the actual
  input channel count. Fixed.
- **`ftt_freq()`'s `NameError`**: its body referenced `nfft`, but the
  parameter is named `n_fft` — any call would crash. Fixed.
- **`Wave.zero_pad()`'s `NameError`**: called a module-level `zero_pad()`
  function that was never defined anywhere in the file. Implemented.
- **`Wave.__add__()`'s `NameError`**: called `warnings.warn(...)` but
  `warnings` was never imported. Fixed.
- **Lazy PyAudio initialization**: the module-level `audio` singleton no
  longer constructs `pyaudio.PyAudio()` (which opens the whole PortAudio
  subsystem) unconditionally at import time — `import olab_audio` alone no
  longer touches audio hardware or fails on a machine with no audio
  drivers.
- **Embedded Whisper transcription hooks removed** (`Mic.transcribeStart`/
  `transcribeStop`/`_thread_transcribe`/`_transcribePrep`) — not migrated,
  per the plan's explicit decision. Transcription is `olab_voice`'s
  territory now.
- **Core/analysis dependency split**: `Recording_np.append()`'s automatic
  cross-rate resampling previously called `librosa.resample()`
  unconditionally on every captured chunk — heavyweight, and run even on
  the (default, same-rate) common case. It now skips conversion entirely
  when rates match, and uses a persistent, stateful `StreamResampler`
  (`soxr`-backed, not `librosa`) when they don't — a fresh one-shot
  conversion per chunk would introduce boundary artifacts and drift at
  every chunk edge, which the persistent converter avoids; it's flushed
  exactly once at save time. `saveAudio()`'s numpy-array save path now
  uses the stdlib `wave` module instead of requiring `soundfile`, so basic
  recording never needs the DSP/teaching dependency stack.
- **`Recording.duration`'s frame-count bug**: it was `len(self.ys) /
  samplerateRec` — wrong for `Recording_bytes` (`self.ys` is a list of raw
  byte *chunks*, not samples) and wrong for multi-channel `Recording_np`
  (interleaved sample count overcounts by a factor of `channels`), which
  broke `timeLimitSec` cutoff behavior in both cases. Fixed with an
  explicit per-frame counter maintained by each subclass's `append()`.
- **`Recording_bytes` cross-rate silent mislabeling**: `Mic.recordStart(
  samplerateRec=...)` could construct a `Recording_bytes` at a rate
  different from the mic's capture rate; `Recording_bytes` never
  resamples, so it would save the original-rate bytes into a WAV file
  *labeled* with the wrong rate — wrong playback speed/pitch, no error.
  Now rejected explicitly at construction.
- **`Mic.recordStart()`'s failure was silently swallowed**: it caught every
  exception (including a cross-rate `Recording_np` failing because
  `resample` isn't installed) and only reported it via `excFunc`, with no
  way for a caller to detect failure except inferring it from
  `mic.isRecording` afterward. It now also returns `True`/`False`.
- **Stereo/multi-channel WAV files got a mono header**: `Recording.save()`
  called `saveAudio()` without passing `self.channels`/`self.frmt`, so it
  silently defaulted to mono — a stereo recording's interleaved samples
  were written with a one-channel WAV header, doubling apparent duration
  and corrupting playback. Fixed by passing them through explicitly.
- **Cross-rate stereo recording misinterpreted as mono**: `Mic._callback_np()`
  produces a flat interleaved buffer, but that flat buffer was passed
  straight to `soxr.ResampleStream`, which requires 2D `(frames, channels)`
  for anything but mono — silently treating a stereo buffer as a mono one
  twice as long. `StreamResampler.process()`/`.flush()` now reshape to
  `(frames, channels)` immediately before the soxr call and flatten the
  result immediately after, so every other caller still only ever sees
  flat/interleaved 1D data.
- **`Recording_np.resample()` double-converted already-cross-rate-captured
  audio**: after a 32kHz→16kHz capture, `self.ys` is already at 16kHz, but
  `.resample()` defaulted `framerateOrig` to `self.samplerateMic` (32kHz)
  — treating the already-converted 16kHz data as if it were still 32kHz
  and converting it a second time. `framerateOrig` now defaults to
  `self.samplerateRec` (the rate the data is actually at), and
  `self.samplerateRec` itself is updated after an explicit resample.

## Not yet done

- **The future `olab_voice` streaming integration adapter** (consuming
  native `olab_audio.Mic` frames and resampling them asynchronously to a
  streaming STT engine's target rate, e.g. 16kHz) is out of scope for this
  migration — see the plan doc.
- **Migrating OFM's `sensor_node.py` and `CoG/realtime_transcription`'s
  `AudioCapture`/`audio_processing.py` onto `olab_audio.Mic`** — deferred
  to a follow-up commit, same pattern as the other `olab_*` migrations.
  This closes the segfault risk currently live in `realtime_transcription`
  specifically (see the plan doc's "Consumer migration candidates").
- **`Mic.start()`'s failure is still reported only via `excFunc`** (unlike
  `recordStart()`, which also returns `True`/`False`). Safe to call
  `.stop()` afterward regardless, but a documented `True`/`False` result
  (or a dedicated exception) would make consumer migration cleaner.
  Flagged by review as a follow-up-quality item, not a blocker.
- **`recordStop()` can do disk I/O (the WAV write in `save()`) from inside
  the PortAudio callback thread** when a `timeLimitSec` cutoff triggers it
  automatically from `_callback_np`/`_callback_record_bytes`. Fine for
  short recordings; for long ones, finalization should eventually move to
  a non-callback worker thread. Flagged by review as a follow-up-quality
  item, not a blocker.
- **Real Raspberry Pi hardware validation** of device filtering,
  sample-rate selection, and the `soxr` backend's performance — the
  non-hardware test suite is thorough, but real-hardware confirmation
  (especially before deploying to vehicles) hasn't happened yet.
