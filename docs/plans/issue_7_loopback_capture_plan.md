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
   and return only monitor sources that can be matched to a safe PortAudio
   capture device. Return the existing selection fields plus loopback metadata
   such as `deviceType: "loopback"`, the friendly sink/source names, and an
   `isDefault` flag for the server's default sink.
2. Build the source-to-PortAudio matching in a small private helper with
   deterministic normalization and no ambient device mutation. Enumerate only
   through the existing safe `get_input_devices()` rules, so a monitor source
   can never reintroduce filtered ALSA plugin names. If Pulse exposes a monitor
   but PortAudio has no usable matching input device, omit it from the
   selectable result and expose a diagnostic message/error rather than
   returning an unusable index.
3. Export the new helper from `olab_audio.__init__` and document it in the
   package README with the normal lifecycle:
   `loopback = get_loopback_input_devices()[0]`; `Mic(deviceID=loopback["deviceID"])`;
   `start()`; optional `recordStart(filename=...)`; `recordStop()`; `stop()`.
   Document that capture only receives audio routed to that sink, may be
   silent when nothing is playing, and has the sink's native device rate.
4. Keep `Mic`'s public constructor and callback semantics unchanged. A
   loopback entry is simply a valid capture-device selection, so NumPy
   reachback data, `mic.db`, time limits, channels, same-rate recording, and
   the established cross-rate failure behavior work exactly as they do for a
   microphone.
5. If source/sink inspection reveals that stable source-to-PortAudio matching
   needs an explicit PulseAudio source selection before opening the stream,
   add that narrowly-scoped configuration API alongside the existing
   source-port functions and require callers to stop capture before changing
   it. Do not silently change the user's default source or sink.

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
  available, PulseAudio. Confirm the loopback device is selectable, records
  speaker output, does not alter default routing, and leaves normal mic
  enumeration/recording unchanged.
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
