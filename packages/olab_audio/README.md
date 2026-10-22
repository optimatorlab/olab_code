# olab_audio

Audio device I/O (`Mic`, `Speaker`, multichannel `MicArray`, recording,
device enumeration, PulseAudio port control) — the core install needs only `pyaudio`,
`pulsectl`, and `numpy` — plus two optional extras: `resample` (lightweight
cross-rate PCM conversion) and `analysis` (a DSP/teaching/research toolkit:
`Wave`, `Spectrogram`, `Spectrum`, tone/chirp/pitch synthesis, `trim`,
`normalize`, matplotlib plotting).

Extracted from `~/Projects/ofm/ofm/sensor/ub_audio.py` per
[`docs/plans/olab_packages_reorg_plan.md`](https://github.com/optimatorlab/olab_code/blob/main/docs/plans/olab_packages_reorg_plan.md)'s
"`olab_audio` v1 scope" section and Migration sequence step 5.

## Installing

```bash
python3 -m venv venv
source venv/bin/activate
pip install olab-audio
```

Add `resample`, `analysis`, and/or `mp3` as needed:
`pip install "olab-audio[analysis,mp3]"`.

**Local development**, against an `olab_code` checkout:

```bash
pip install -e "packages/olab_audio[analysis,mp3]"
```

## Extras

| Extra | Adds | Needed for |
|---|---|---|
| *(core, default)* | `pyaudio`, `pulsectl`, `numpy` | `Mic`/`Speaker`/recording at the device's own native rate, device enumeration, PulseAudio port control. |
| `resample` | `soxr` | Cross-rate recording (`Mic.recordStart(samplerateRec=...)` at a rate other than the mic's native one) and `olab_audio.resample`'s `resample()`/`StreamResampler`. Not yet validated on target Raspberry Pi hardware — see the plan doc's acceptance checklist. |
| `analysis` | `resample` (soxr) + `librosa`, `soundfile`, `matplotlib` | `olab_audio.analysis`'s `Wave`/`Spectrogram`/`Spectrum`, tone/chirp/pitch synthesis, `trim`, `read_wave_librosa`, plotting, `Recording.make_wave()`, and `Recording_np`'s explicit `.resample()` method. |
| `mp3` | `lameenc` (bundled LAME bindings) | Save a capture as MP3 or convert a 16-bit PCM WAV with `wav_to_mp3()`. No system `ffmpeg` binary is required. |

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

## PipeWire capture identity

On PipeWire's ALSA compatibility device, each `Mic.start()` open is also given
a fresh `node.name`/`application.name` identity for that open. This prevents a
WirePlumber stream-restore rule made for one `start_loopback_capture()` stream
from being replayed onto an unrelated process's microphone capture. Any valid
caller-provided `PIPEWIRE_PROPS` dictionary is retained for the open and the
environment is restored immediately afterward.

## MP3 output

Install the optional encoder first:

```bash
pip install "olab-audio[mp3]"
```

Use an `.mp3` filename to encode when saving a recording; WAV remains the
default behavior for every other filename. `bitrate` is optional: it defaults
to 128 kbps for rates of 16 kHz and above, and 64 kbps for 8/11.025/12 kHz.

```python
mic.recordStart(filename="capture.mp3")
# ... let it capture some audio ...
mic.recordStop()  # saves a 128 kbps MP3 for a normal 44.1/48 kHz capture

# Or select a valid constant bitrate while saving a recording manually:
mic.recording.save(filename="capture.mp3", bitrate=192)
```

Convert an already-saved uncompressed 16-bit PCM WAV without invoking an
external command:

```python
olab_audio.wav_to_mp3("system_audio.wav")
olab_audio.wav_to_mp3("system_audio.wav", out_filepath="share.mp3", bitrate=192)
```

MP3 supports only mono or stereo 16-bit PCM input and these sample rates:
8, 11.025, 12, 16, 22.05, 24, 32, 44.1, and 48 kHz. `olab_audio` rejects
other rates and invalid rate/bitrate combinations rather than allowing the
encoder to silently change them. If a device's native capture rate is not
MP3-compatible, first capture/resample with `Recording_np.resample()` (the
`analysis` extra) to a supported rate, then save the recording as MP3.

## Loopback (system-output) capture

Record "whatever is playing on this speaker/output" via PulseAudio/PipeWire's
monitor sources -- Linux with a PulseAudio or PipeWire's PulseAudio-compatible
server only, ALSA host API only.

```python
import olab_audio

loopbacks = olab_audio.get_loopback_input_devices()
loopback = loopbacks[0]

mic = olab_audio.Mic(deviceID=loopback['deviceID'])
olab_audio.start_loopback_capture(mic, loopback)  # starts mic AND routes only this stream

mic.recordStart(filename="system_audio.wav")
# ... let it capture some audio ...
mic.recordStop()

mic.stop()  # also removes this stream's PulseAudio source-output -- nothing to restore
```

Notes:
- **All** entries returned by `get_loopback_input_devices()` share one
  `deviceID` (the single PulseAudio/PipeWire-routed PortAudio device) --
  `start_loopback_capture()` is what actually determines which sink's audio
  is captured, not the `deviceID`.
- `mic` must **not** be started before calling `start_loopback_capture()` --
  it calls `mic.start()` itself (forwarding any keyword arguments you pass
  after `source`, e.g. `reachbackFunc=`, exactly like calling `mic.start()`
  directly), then moves only that one resulting PulseAudio source-output to
  the selected monitor. The system default source, and every other
  application's capture, are never touched.
- If the new capture stream can't be identified (timeout, or an ambiguous
  new stream), or the move itself is rejected by PulseAudio/PipeWire,
  `start_loopback_capture()` stops `mic` and raises `RuntimeError` rather
  than leaving it silently capturing the wrong source. A move call that
  doesn't raise is trusted as successful -- some `pipewire-pulse` versions
  don't reliably report a source-output's routing state afterward, so this
  doesn't attempt to re-confirm it.
- Capture only receives audio actually routed to the selected sink (silent
  otherwise -- expected, not a bug), and uses the sink's native device rate,
  which may not be 44.1kHz.

## Multichannel microphone arrays (`MicArray`)

`MicArray` captures many microphones from **one** multichannel USB audio
interface on **one** PortAudio stream, so the channels can never drift apart.
It gives each microphone a label, live level/peak/spectrum readouts, and a
recorder that writes one mono 32-bit WAV per microphone plus a JSON manifest.
It is hardware-agnostic: labels and per-channel `meta` are opaque and passed
through untouched, and nothing in it knows what interface or microphones are
attached. `Mic` is unaffected; `MicArray` is a separate class.

```python
import olab_audio

arr = olab_audio.MicArray(
    channels=[
        {'label': 'B1', 'channel': 1,  'meta': {'sensitivity': 0.05}},
        {'label': 'D1', 'channel': 14, 'meta': {}},
    ],
    deviceName='UMC1820',        # substring match, resolved at start()
)
if not arr.start():              # False on failure; the reason goes to excFunc
    raise SystemExit('could not start the array')

arr.db                           # {'B1': -48.2, 'D1': -51.7} dBFS; None until the first buffer
arr.peak_db                      # peak hold per label, never below the live level
arr.spectrum('B1')               # log-binned dB spectrum of one label's latest buffer

arr.recordStart('takes', 'take1')    # takes/take1_B1.wav, takes/take1_D1.wav, takes/take1_manifest.json
# ... capture ...
take = arr.recordStop()          # blocks until the files and manifest are final
arr.stop()
```

**Channels.** Each entry is `{'label', 'channel', 'meta'}`. `channel` is the
1-based hardware jack number. Labels must match `[A-Za-z0-9._-]+` because they
become filenames, and neither labels nor jack numbers may repeat. `meta` must
be JSON-serialisable and is copied into the manifest. Find a device with
`olab_audio.get_input_device_info('UMC')`, which returns
`{'deviceID', 'name', 'maxInputChannels', 'defaultSampleRate'}` for the first
match (case-insensitive) or `None`; `name=None` gives the default input
device. Or pass `deviceID` (the global PortAudio index) instead of
`deviceName`. `meta` is optional and defaults to `{}`. Invalid constructor
arguments raise `ValueError`; `start()` and `recordStart()` report failures
instead.

**The stream is opened at an explicit sample rate (default 48000), never the
device's own default, and at the device's full input width.** Both matter:
many multichannel interfaces report a 44.1 kHz default, and on a Behringer
UMC1820 opening only the first 14 of its 18 inputs overflowed about every
1.4 s while opening all 18 was clean. `open_channels=` overrides the width
(it must be at least the highest configured jack and no more than the device
has). For a virtual or alias device (`pipewire`, `default`), which may report
a very large channel count, pass `deviceID` and `open_channels` explicitly.
`frames_per_buffer` defaults to 8192 (about 171 ms at 48 kHz); latency doesn't
matter for recording. In standalone tests on a UMC1820 (18 channels), 1024-
and 4096-frame buffers overflowed about every 3 s and 8192 did not. Only
`paInt32` is supported.

**Live readouts.** `db` and `peak_db` are dBFS per label and are `None` until
the first buffer arrives; `reset_peaks()` restarts the peak hold at the
current level, and a peak of `-0.1` dBFS or higher is a clipping indicator.
`subscribe(callback, labels=None)` calls `callback(data=..., labels=...)` for
every buffer with a float32 array of shape `(frames, len(labels))`; treat
`data` as read-only. Subscriber and `excFunc` exceptions are isolated, so one
bad callback never stops the audio callback or the others. `lastCallbackTime`
is `time.monotonic()` at the most recent callback (`None` until the first), so
a caller can notice a stalled stream.

**Recording.** `recordStart(filepath, basename, labels=None, timeLimitSec=None,
postFunc=None)` arms a take; every selected label (all by default) starts on
the same buffer boundary and ends on the same one, so the files have equal
lengths and share a start sample. It returns `False` (and reports through
`excFunc`) instead of raising, and never overwrites an existing take.
`recordStop()` ends the take at a buffer boundary and returns
`{'dir', 'files', 'manifest', 'frames', 'gaps', 'error'}` (`None` if there is
nothing to stop; `frames` is the shortest file's length, and `manifest` is
`None` if the manifest could not be written); the same dict is passed to
`postFunc(take=...)` and kept as `arr.lastTake`. WAV files are limited to
4 GiB (roughly 6 hours per channel at 48 kHz); use `timeLimitSec` to split
long recordings.

**Manifest** (`<basename>_manifest.json`): `samplerate`, `frames_per_buffer`,
`startFrameIndex`, `totalFrames`, `startedAtWall`, `startedAtWallNote`,
`measuredFramesPerWallSecond`, `gaps`, `gapNote`, `error`, `manifestVersion`,
and `labels`, which maps each label to `{channel, meta, file, frames}`.
`startedAtWall` is the
wall-clock time of the take's first sample, derived from the PortAudio buffer
time at the take's first callback; its accuracy against an independent clock
has **not** been measured, so don't rely on it for fine alignment with other
sensors until it is. If the time information is unusable it is `null` with a
`startedAtWallNote`; if the take ended before its first buffer, `startedAtWall`,
`startedAtWallNote` and `startFrameIndex` are all `null`.
`measuredFramesPerWallSecond` is the stream's measured rate against the
computer's clock (an interface can run slightly off nominal); it is `null`
for takes shorter than two buffers and for takes with unfilled gaps, and on a
take with estimated overflow fills it includes those estimated frames.

**Dropouts.** If PortAudio reports an input overflow, an unknown number of
frames was lost. `MicArray` estimates how many from the gap in buffer
timestamps, inserts that many zero frames into **every** channel (so the
files stay equal length and aligned), and records
`{'at_frame', 'est_lost_frames', 'method': 'adc_time'}` in `gaps`. **This is an
estimate**. In standalone checks against the computer's clock on a healthy
18-channel stream (8192-frame buffers with injected stalls, and 1024-frame
buffers with natural overflows) the net error was about 10 frames per gap,
with roughly 1 ms resolution; `MicArray`'s own zero-fill path has not yet been
run on hardware (see below). When overflows are frequent the estimate can be
badly wrong: about 2.4x too high on a stream that overflowed about every
1.4 s.
Other entries: `queue_drop` is an **exact** count (the writer fell behind, so
a buffer was replaced by zeros of the same length). `unflagged_gap` (a timing
gap with no overflow flag) and `flag_only` (an overflow was flagged but the
estimate was unavailable or implausible) are logged but never filled, since a
wrong fill would corrupt good audio. Overflow handling is
a safety net; if you see gaps routinely, fix the cause (stream width, system
load) rather than relying on the zero-fill.

**Lifecycle.** `start()` and `recordStart()` return `True`/`False`, and
`start()` fails (and closes the stream) if the opened stream is not actually
active. `stop()` finalises any take in progress and is safe to call
repeatedly (its `postFunc` fires on every call). `start()`
and `stop()` are refused (reported through `excFunc`) when called from inside
a subscriber or other callback running on the audio thread. User callbacks
never run while a `MicArray` lock is held.

**Validation status.** Validated on real hardware with a Behringer UMC1820
(18 inputs, UMC analog plus an ADAT-connected preamp): a 20 s recording with
no overflows and a clap recorded on two channels, plus the failure modes listed
in `CHANGELOG.md`. **Not yet validated:** long runs through `MicArray`,
the overflow zero-fill path on a healthy stream, `startedAtWall` accuracy, all
12-16 channels of a full array, operation under load, and unplug/replug during
a take. Tracked in
[#81](https://github.com/optimatorlab/olab_code/issues/81).

## Normalizing recordings

Loopback (and mic) recordings capture whatever level PulseAudio/PipeWire
happened to be playing at -- e.g. a quiet per-app stream volume (PipeWire
gives each playback app its own independent volume, restored per media
role, separate from the sink/master volume) that's unrelated to what the
system volume slider shows. Two ways to fix a too-quiet recording,
depending on when you catch it:

**Already saved to a WAV file** -- `normalize_wav()` peak-normalizes the
file on disk:

```python
import olab_audio

olab_audio.normalize_wav("system_audio.wav")  # overwrites in place, peak -> 0dBFS
# or write to a separate file instead of overwriting:
olab_audio.normalize_wav("system_audio.wav", out_filepath="system_audio_normalized.wav")
```

**Still in memory, before the first save** -- skip `recordStart(filename=...)`
so `recordStop()`'s automatic save is a no-op, call `Recording.normalize()`
on the buffer, then save it yourself:

```python
import olab_audio

loopback = olab_audio.get_loopback_input_devices()[0]
mic = olab_audio.Mic(deviceID=loopback['deviceID'])
olab_audio.start_loopback_capture(mic, loopback)

mic.recordStart()  # no filename -- recordStop() below won't auto-save
# ... let it capture some audio ...
mic.recordStop()   # writes nothing yet, since no filename was given

mic.recording.normalize()                  # peak -> 0dBFS, in place, before saving
mic.recording.save(filename="system_audio.wav")

mic.stop()
```

Both use the same peak-scaling math (loudest sample -> +-`amp`, default
`1.0` == 0dBFS) and print a `NOTE` instead of raising if the audio is
silent (nothing to normalize). Neither touches PulseAudio/PipeWire device
or mixer state -- they only rescale the samples you already captured.

`amp` must be a finite value in `(0, 1.0]` -- 1.0 (0dBFS) is the loudest
peak 16-bit PCM can represent at all, so an `amp` above 1.0 is rejected
with `ValueError` rather than silently hard-clipping at 1.0 and falling
short of the peak it promised.

`Recording.normalize()` on a `Recording_bytes` only supports `paInt16`
(the default `frmt`) -- it also raises `ValueError` for any other PyAudio
format, since the int16 decode/re-encode it uses internally would
otherwise corrupt other bit depths/formats. There's currently no working
alternative for non-int16 capture: `Mic`'s NumPy callback path
(`Mic._callback_np()`) also hardcodes an int16 decode regardless of
`frmt`, so `Recording_np` can't correctly normalize (or otherwise
process) a non-int16 capture either -- that's a separate, pre-existing
limitation of `Mic` itself (tracked as
[issue #29](https://github.com/optimatorlab/olab_code/issues/29)), not
something `normalize()` works around.

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
- **More real-hardware validation of `MicArray`** (long runs, the overflow
  zero-fill path on a healthy stream, `startedAtWall` accuracy, a full
  12-16 channel array, load, unplug/replug) — tracked in
  [#81](https://github.com/optimatorlab/olab_code/issues/81).
- **Real Raspberry Pi hardware validation** of device filtering,
  sample-rate selection, and the `soxr` backend's performance — the
  non-hardware test suite is thorough, but real-hardware confirmation
  (especially before deploying to vehicles) hasn't happened yet.
