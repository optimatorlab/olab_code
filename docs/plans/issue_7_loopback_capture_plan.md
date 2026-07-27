# Issue #7: Record from a loopback device

## Goal

Allow a caller to discover a system-output loopback (the PulseAudio/PipeWire
monitor source for a sink), select it through the existing `Mic` input-device
workflow, receive its PCM frames through the normal callback, and optionally
write those frames to WAV through the existing recording API.

## Context from codebase exploration

- `olab_audio` is intentionally local device I/O only. `Mic` owns PortAudio
  input streams and its `Recording_np`/`Recording_bytes` implementations
  already provide callback-time buffering and optional WAV persistence.
- `device.get_input_devices()` currently exposes PortAudio capture devices as
  `{deviceID, deviceType: "mic", name}`. Its ALSA filter deliberately omits
  unsafe pseudo-plugin nodes because opening some of them can crash the
  process.
- PulseAudio/PipeWire controls are already an optional runtime integration via
  `pulsectl`; source-port helpers live in `device.py`. A sink's monitor source
  is the supported Linux/PulseAudio/PipeWire representation of speaker/system
  output. It is a capture source, not an output device and not a new streaming
  transport.
- Recording is already orthogonal to input type: after `Mic.start()`, callers
  use `recordStart(...)` and `recordStop()`; same-rate recording remains a
  core-only path, while cross-rate NumPy recording uses the existing
  `resample` extra.

## Agreed scope and non-goals

- Target Linux hosts running PulseAudio or PipeWire's PulseAudio-compatible
  server. Preserve current non-ALSA behavior and do not claim monitor-source
  discovery on macOS or Windows.
- Add an explicit loopback discovery/selection path without changing the
  meaning of ordinary physical-microphone enumeration.
- Reuse `Mic` and the current recording classes; do not fork a `LoopbackMic`,
  add a network/WebSocket stream, or put STT in `olab_audio`.
- Treat missing PulseAudio/PipeWire support, no default sink, and a monitor
  source that PortAudio cannot expose as clear, recoverable conditions.

## Proposed design

1. In `packages/olab_audio/src/olab_audio/device.py`, add a public
   `get_loopback_input_devices()` helper. Query sinks and sources using a
   short-lived `pulsectl.Pulse` client, identify each sink's monitor source,
   and return them alongside the **one** shared, safe PortAudio capture
   device that represents Pulse/PipeWire-routed audio on this host (the ALSA
   host API's `pipewire`/`pulse`/`default` alias — confirmed during Stage 1
   implementation planning that PortAudio's ALSA host API does not expose a
   distinct device per Pulse sink/source, only this one shared alias; only
   accept the alias when the host API is actually ALSA). Every returned
   entry therefore shares the same `deviceID` — this is expected, not a
   defect — since discovery alone cannot select between sinks. Return the
   existing selection fields plus loopback metadata such as
   `deviceType: "loopback"`, the friendly sink/source names, and an
   `isDefault` flag for whether the sink is the server's default sink
   (informational only — independent of which source is actually selected
   for capture, see step 2).
2. **Selection routes only this process's own capture stream -- it never
   touches the server-wide default.** *(Revised after real PipeWire
   hardware testing showed the earlier default-source-switch design didn't
   actually work: the capture stream kept reading the physical microphone
   even with the monitor source switched to default and at 100% volume.
   The user's own successful manual workaround -- moving only their
   recording app's active stream to the monitor via pavucontrol's
   Recording tab -- pointed at the real mechanism: PulseAudio's per-stream
   "source-output" object, not the source-wide default.)* Add
   `start_loopback_capture(mic, source, *, timeout=2.0, poll_interval=0.05,
   **mic_start_kwargs)` alongside the existing source-port functions. It
   snapshots existing `pulsectl` source-outputs, calls
   `mic.start(**mic_start_kwargs)` itself (so `mic` must not already be
   started), then bounded-polls for the one new source-output positively
   identified as this process's (PID match via its own or its owning
   client's proplist -- an entry present in the snapshot, or one whose
   identity can't be confirmed, is never a candidate), and moves *only*
   that source-output to the target monitor via `pulsectl`'s
   `source_output_move()`. Any failure (already started, `mic.start()`
   failing, timeout, ambiguity, or `source_output_move()` itself raising)
   stops `mic` and raises rather than leaving it silently capturing the
   wrong source. A non-raising `source_output_move()` call is trusted as
   successful on its own -- *(round-6 finding, from real PipeWire
   hardware testing)* an earlier design additionally re-checked the
   source-output's reported attached-source afterward, but that field was
   found to be unreliable on at least one `pipewire-pulse` version (it
   read identically before and after a `pactl move-source-output` that a
   live-audio check confirmed had actually worked), so comparing against
   it produced false failures and was removed rather than patched. No
   restoration step exists or is needed: PulseAudio destroys the routed
   source-output automatically when `mic.stop()` closes the stream, so
   unlike the earlier default-switch design, nothing persists afterward.
3. Export the new helpers from `olab_audio.__init__` and document them in the
   package README with the real lifecycle:
   `loopback = get_loopback_input_devices()[0]`;
   `mic = Mic(deviceID=loopback["deviceID"])`;
   `start_loopback_capture(mic, loopback)` (starts `mic` and routes only
   this stream); optional `recordStart(filename=...)`; `recordStop()`;
   `mic.stop()` (also removes this stream's source-output -- nothing to
   restore). Document that all loopback entries share one `deviceID` and
   `start_loopback_capture()` alone determines which sink is captured; that
   only this stream's routing changes, never the system default or another
   application's capture; that capture only receives audio routed to that
   sink, may be silent when nothing is playing; and that it has the sink's
   native device rate.
4. Keep `Mic`'s public constructor and callback semantics unchanged --
   confirmed on reconsideration after the redesign, not just carried
   forward: `start_loopback_capture()` only calls `Mic`'s existing public
   `start()`/`stop()`/`micOn`, so a loopback entry is still simply a valid
   capture-device selection once routed. NumPy reachback data, `mic.db`,
   time limits, channels, same-rate recording, and the established
   cross-rate failure behavior work exactly as they do for a microphone.
5. Simultaneous multi-sink capture is no longer architecturally excluded
   (each `Mic`'s source-output routes independently, unlike the removed
   single-default-source design) but is not tested or claimed as working
   in this task -- a candidate for future manual verification, not a
   promise made here. Not safe against another thread/process opening a
   *new* capture stream during the snapshot-to-identify window -- handled
   by failing closed (the ambiguity path), not locking.

## Implementation steps

1. Define the public result schema and platform/error contract in `device.py`;
   add private Pulse/PortAudio discovery and matching helpers with lazy,
   exception-safe Pulse client use.
2. Add `get_loopback_input_devices()` and package-root export; retain the
   existing `get_input_devices()` return shape and physical-input behavior.
3. Add focused fake-Pulse/fake-PyAudio tests for default and non-default
   monitor sources, successful safe-device matching, no sinks/no monitors,
   absent `pulsectl`, unmatched sources, and proof that an unsafe ALSA
   pseudo-device is never returned.
4. Add a `Mic` integration-style unit test using the returned `deviceID` and
   the existing fake PortAudio stream. Verify `input=True`, normal callback
   delivery, `recordStart()`/`recordStop()` WAV metadata, and idempotent
   cleanup without real hardware.
5. Update README/API docs and add an opt-in manual hardware acceptance matrix
   for PipeWire and PulseAudio: choose default/non-default sink monitor,
   play known audio, verify callback level and WAV audibility/duration, verify
   silence behavior, and exercise a device with a non-44.1-kHz native rate.

## Testing and verification

- Run `pytest packages/olab_audio/tests -v` with fake device/Pulse fixtures;
  hardware-dependent monitor discovery must not run in generic CI.
- Run the documented manual Linux matrix on at least PipeWire and, where
  available, PulseAudio: play speaker audio while a person speaks near the
  physical microphone; the saved WAV must contain the speaker audio and
  exclude the room speech; verify via `pavucontrol` or `pactl list short
  source-outputs` that *this process's* capture stream -- not the system
  default source -- is attached to the monitor; confirm normal mic
  recording and the system's actual default source are unaffected after a
  loopback session ends.
- Install/test core-only and `[resample]` environments to verify same-rate
  loopback WAV recording needs no new heavyweight dependency and cross-rate
  behavior remains explicit.

## Risks and mitigations

- Monitor-source naming varies between audio servers and PortAudio host APIs:
  isolate matching, cover known forms with fixtures, and only return a match
  that is safe to open.
- PortAudio enumeration is cached: keep the existing `reinit_audio()` guidance
  and document that newly created routing/virtual devices require a re-scan.
- A source may exist but produce silence because no audio is routed to its
  sink: report selection metadata and document this as expected runtime state,
  not a recording failure.
- Identifying "this process's" newly-created source-output among possibly
  several is inherently racy: mitigated by snapshotting before `mic.start()`,
  requiring a positive PID match (own or owning-client proplist) rather than
  guessing from uniqueness alone, and failing closed (stopping `mic`, raising)
  on timeout or ambiguity instead of silently routing the wrong stream.
