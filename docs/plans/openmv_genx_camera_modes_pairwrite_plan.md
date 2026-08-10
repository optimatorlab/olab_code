# Pairwrite draft — GENX320 visual modes and event access in `olab_camera`

## Goal

Support the attached OpenMV GENX320 within the normal `olab_camera` framework,
not as a standalone diagnostic viewer. Users must be able to select three
exclusive camera session modes:

1. **Histogram pixels:** a full 320×320 event-activity image through
   `CameraOpenMV` and the existing MJPEG/WebSocket/WebRTC pathways, with
   grayscale or host-applied false color.
2. **Histogram pixels plus movement regions:** the same visual stream, with
   compact on-board regions available as telemetry and optionally drawn on the
   streamed image.
3. **Raw polarity events:** decoded ON/OFF event batches available to
   application code and recording, plus a host-rendered live pixel view that
   can feed the ordinary `olab_camera` streaming interfaces.

The default must be the mode with the highest *measured* smooth visual update
rate that meets the acceptance criteria below. Do not choose a default from
device-side snapshot FPS or a theoretical USB rate alone.

## Context from codebase exploration

- `CameraOpenMV` already converts standard OpenMV frame-stream packets into
  BGR `numpy.ndarray` frames in `Camera.frameDeque`. The existing camera
  streaming code therefore already serves those frames through the same
  browser interfaces used by the rest of `olab_camera`.
- The maintained `genx_histogram_preview` profile already configures the
  required `csi.CSI(cid=csi.GENX320)` histogram path. It is the starting point
  for the pixel-level histogram modes, not the standalone movement viewer.
- The OpenMV official GENX raw-event example uses a named channel to transfer
  packed EVT2.0 bytes and renders them on the host. It confirms that raw
  polarity, coordinates, and microsecond timestamps are available. Event mode
  and histogram mode are distinct sensor modes, so they cannot run in one
  device session.
- The proof-of-concept `genx_movement_regions` profile demonstrated real
  on-board compact movement regions and identified a channel overwrite race;
  its single-buffer guard fixes that race. It is useful device-side evidence,
  but its standalone OpenCV viewer is not the product UI.
- The proof-of-concept viewer uses OpenCV. `dearpygui` was installed only to
  run OpenMV's separate raw-event benchmark GUI. It must not be added as an
  `olab_camera` dependency.

## Agreed decisions

- All three modes are supported, but a camera session runs exactly one mode.
  Mode changes are explicit stop/reconfigure/restart operations; no code
  implies that raw event and histogram acquisition run concurrently.
- Histogram pixels and movement regions belong in one coherent profile/session.
  Regions are exposed as structured telemetry and can be enabled as an
  overlay; they are not the only visual result.
- Histogram rendering offers grayscale and named false-color palettes. The
  palette is host display policy and does not alter device acquisition data.
- Raw rendering has semantic polarity colors (configurable named palette;
  initial defaults documented in the implementation) and temporal decay. It
  must not pretend that a false-colored histogram retains ON/OFF polarity.
- Raw mode exposes typed decoded event batches early, including coordinates,
  polarity, sensor timestamps, source sequence/time range, host receipt time,
  format metadata, and explicit loss counters. Preserve the original packed
  payload for low-level experiments.
- The initial decoded raw format is OpenMV's default EVT2.0. EVT2.1 and EVT3.0
  are deferred extension points, not silently accepted inputs.
- User callbacks never run on the serial/protocol acquisition thread. A
  bounded queue and dispatcher thread hand batches to callbacks; when full,
  oldest queued batches are dropped and accounted for. Slow consumers must
  never stall the board.
- Raw sessions receive a durable recorder early: a versioned session directory
  with a manifest and append-only event-batch chunks. It records configuration,
  firmware/profile identity, timing ranges, and every loss counter.
- Existing event-safe V2 transport work in `openmv_device.py` is preserved
  unless new evidence supports a narrowly scoped change.

## Candidate-mode benchmark and default-selection gate

Before naming a package default, run each candidate with the real GENX320 in
three repeatable scenes: static, ordinary hand/object motion, and dense
large-area motion. For each mode measure and log:

- device acquisition/update FPS;
- host receipt rate and rendered-frame rate;
- browser stream rate/latency where applicable;
- USB payload rate;
- source, transport, queue, preview, callback, and recorder drops separately;
- CPU use sufficient to explain a bottleneck; and
- clean stop/disconnect/reconnect behavior.

The winner is the mode with the best sustained low-percentile rendered-frame
rate and responsive browser view while producing no unaccounted loss. A high
event rate alone is not a visual-frame-rate win. If no candidate reaches a
useful browser rate under the dense scene, retain the measured best as an
explicit provisional default and document the limitation rather than hiding it.

The initial implementation must make the selected mode visible in status and
recording metadata. It may not change the package's public default again
without a recorded comparable benchmark.

## Proposed design

### Session and profile model

Add an explicit GENX mode selection surface to `CameraOpenMV`/its maintained
profiles. A mode owns exactly one script and one protocol-client owner at a
time. Starting a new mode follows the existing safe lifecycle: connect,
stop any old script, upload/execute the selected source, consume only the
channels expected by that source, then stop and disconnect in the capture
owner's `finally` path.

Keep frame-bearing histogram modes inside `CameraOpenMV` so every existing
streaming endpoint, `frameDeque` consumer, decoration, and lifecycle behavior
continues to apply. Add a profile capability declaration rather than assuming
every profile has `resolution`, standard frames, or a raw event channel.

### Histogram pixels and regions

Refactor/extend `genx_histogram_preview` into the supported histogram-frame
source. Add a maintained histogram-plus-regions profile or a documented
capability/configuration of that source only after confirming that standard
frame streaming and compact custom telemetry coexist reliably on this firmware.

The host parser validates each region record, tracks sequence gaps and corrupt
records, and publishes latest valid telemetry atomically with an associated
host receipt time. An optional camera decoration reads that latest record and
draws boxes/centroids onto a copy of the frame; it must not mutate the capture
frame or block capture. A user can turn the overlay off while retaining the
pixel view and structured telemetry.

Apply histogram false color on the host after frame conversion and before the
normal stream/decoration path. Keep grayscale available. Use NumPy/OpenCV only;
do not introduce a GUI toolkit dependency. The palette selection must be
visible in status/metadata and testable from a synthetic grayscale input.

### Raw event stream, rendering, callback, and recording

Implement a dedicated `OpenMVEventStream`-style session component rather than
forcing raw bytes through the standard frame reader. It is responsible for:

- uploading the maintained EVT2.0 event-mode profile and reading its raw-event
  channel;
- exact, stateful EVT2.0 decoding into a typed `EventBatch` representation;
- preserving raw payload plus packet/batch identity for investigation;
- tracking time-high state, invalid words, source/transport discontinuities,
  queue drops, and decode failures; and
- bounded ownership-safe shutdown with script stop and disconnect in `finally`.

Use separate bounded queues for (a) callback/recorder delivery and (b) derived
preview work. A slow callback, recorder, or browser client only causes
explicitly counted queue loss in its own branch. It never blocks acquisition.

The preview rasterizer converts batches into 320×320 BGR frames at a configured
viewer rate with temporal decay and ON/OFF color semantics. It feeds a small
adapter respecting `Camera`'s frame-deque/condition contract, so existing
MJPEG/WebSocket/WebRTC mechanisms can serve it. Browser frames are only an
operator view; their timestamps and rate are not substitutes for sensor event
time. The raw event callback and recorder remain usable even if preview is
disabled or rate-limited.

The recorder writes a session directory with a manifest and append-only chunks.
Manifest fields include device/firmware/profile identity, active configuration,
format/schema version, chunk sequence/time ranges/counts, and every loss
counter. Supply a replay reader that yields the same public `EventBatch` type.

## Implementation steps for pairwrite

1. Review the current uncommitted proof-of-concept changes and preserve the
   event-safe transport fix. Remove or deprecate the standalone movement viewer
   only after its functionality is represented by the package integration.
2. Add profile capability metadata and make `CameraOpenMV` reject incompatible
   profile/mode combinations before opening hardware.
3. Complete the histogram-frame path with unit tests, then run the real
   hardware/browser baseline benchmark. Add host grayscale/false-color policy
   and the optional overlay hook without changing normal camera behavior.
4. Integrate regions with the frame path only after a hardware probe proves
   standard frame traffic and region channel traffic coexist without corrupt
   records. Include the single-buffer pending-record invariant in the device
   profile.
5. Add the EVT2.0 profile, decoder, typed batches, counters, bounded queues,
   non-blocking callback dispatcher, and deterministic cleanup. Unit-test the
   decoder using official-format fixtures, including TIME_HIGH transitions and
   malformed input.
6. Add preview rasterization and the `Camera`-compatible frame publisher;
   use it with the normal browser streaming stack. Add session-directory
   recording and replay before declaring raw mode usable for exploration.
7. Execute comparable real-board benchmarks for all three modes and update the
   documented default from evidence. Verify static, hand-motion, and dense
   motion behavior plus reconnects.
8. Run the full `packages/olab_camera` test suite, relevant packaging/optional
   dependency checks, and an on-board hardware acceptance run. Do not make
   `dearpygui`, `numba`, or an OpenMV desktop GUI package a runtime dependency
   unless a later reviewed requirement explicitly justifies it.

## Testing and verification

- Unit tests: profile capability/mode validation, colorization, immutable
  captured-frame behavior, overlay behavior, region parsing and gap counters,
  EVT2.0 decoding, timestamp rollover, malformed payload rejection, bounded
  queue drop policy/counters, callback isolation, preview rate limiting,
  recorder manifest/chunk/replay correctness, and lifecycle cleanup with fake
  clients.
- Integration tests: synthetic histogram and raw preview frames must traverse
  the same `Camera` frame/condition and streaming paths as other backends.
- Hardware acceptance: prove each mode independently on the actual GENX320;
  capture stdout/status/counters; verify normal cleanup; and append dated
  observations only to the shared investigation notebook.
- Default-selection acceptance: save one comparable benchmark table with all
  three scenes and make the chosen default/reason explicit.

## Risks and mitigations

- Raw-event load is scene-dependent and can exceed host/USB/disk capability.
  Use bounded buffers, branch-specific counters, and recorded benchmark limits.
- The same firmware may not support standard frame streaming and a custom
  movement channel together reliably. Prove it before adopting a combined
  profile; otherwise retain exclusive histogram and region modes temporarily.
- Histograms lose polarity. Label false color accurately and keep true polarity
  rendering only in event mode.
- Mode switching can expose OpenMV protocol/USB recovery failures. Reuse the
  event-safe transport, make stop/disconnect mandatory, and test reconnects.
- The historical shared notebook contains superseded claims. Treat its newest
  dated entries as authoritative and append rather than rewrite.

## Non-goals for this pairwrite cycle

- No WiFi, firmware downgrade, generic `sensor.reset()`, or back-to-back
  legacy/V2 protocol experiments in one boot.
- No `dearpygui`-based package viewer.
- No claim of lossless raw capture under arbitrary scenes; all loss must be
  measured and surfaced.
- No multi-camera synchronization, ROS, or drone deployment work in this
  iteration.

## Pairwrite API and delivery refinement

All modes are selected through `CameraOpenMV(profile=...)`; raw-event decoder,
reader, dispatcher, recorder, and preview machinery are internal implementation
components, not a parallel user-facing camera class. Raw consumers are added
with `addEventCallback(...)`, and recording with
`addEventRecorder(outputDir=...)`, matching the package's existing `add*`
capability style. A raw profile is named `GenxRawEventsProfile` with
`profile_id='genx_raw_events'`.

The software deliverable uses `genx_histogram_preview` as a documented
provisional default and is independently reviewable without physical-board
access. A later real-board benchmark across static, ordinary-motion, and dense
motion scenes is the only authority for replacing that default. Raw worker
lifecycle follows `CameraOpenMV`'s existing sole device-owner rule: capture
owns protocol cleanup; callback/recorder workers are bounded, exception-safe,
and must finish before a mode switch/re-arm clears the deferred-cleanup guard.
