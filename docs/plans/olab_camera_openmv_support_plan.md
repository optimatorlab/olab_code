# `olab_camera` OpenMV Support Plan

## Goal

Add a usable OpenMV integration to `olab_camera` for laptops and Raspberry Pi
hosts. Lab members must be able to run maintained profiles without writing
MicroPython, while retaining a documented custom-script path. The first
hardware outcome is a single GENX320 that produces a live event-activity
preview; the first multi-device outcome is an aligned pair of GENX320 cameras.

This plan intentionally excludes ROS integration and persistent standalone
deployment in its first implementation. Both can follow the host-launched
workflow once it is reliable.

## Context from codebase exploration

- `olab_camera.Camera` owns the frame deque, capture/feature threads, and the
  HTTPS MJPEG/WebSocket/WebRTC streaming interfaces. Existing hardware
  backends supply conventional frames to that API.
- `CameraUSB` assumes OpenCV `VideoCapture`; an OpenMV cam requires its own
  host-protocol connection and capture loop instead.
- The project supports Python >=3.10. The current OpenMV host Python package
  requires Python >=3.12, so OpenMV support must be optional and must not
  raise the base package's Python requirement.
- The OpenMV protocol supports script upload/execution, standard frame
  streaming, stdout, and named bidirectional channels. GENX320 event data
  cannot correctly be represented only as `Camera` frames.

## Inventory and pairing guide

There are five OpenMV base boards and eight removable camera modules, so at
most five of the listed modules can operate at a time. Rotation among the five
boards is an accepted lab workflow.

| Camera module | RT1062 V5 (2) | H7 Plus (3) | Recommended initial role |
|---|---|---|---|
| GENX320 (5) | Prefer for raw-event experiments, USB host streaming, and the first synchronized pair | Use for histogram previews and edge/event processing | Put the first synchronized GENX320 pair on the two RT1062 boards; use remaining modules in rotating ground/air trials. |
| MT9V034 global-shutter monochrome (3) | Prefer when high-FPS host interaction or more processing headroom is wanted | Suitable for independent onboard trackers | Use as the conventional-frame, fast-motion baseline for ground tracking, then as an onboard edge tracker. |

This is a deployment/benchmarking guide, not a compatibility restriction.
Both available board types support the GENX320; the MT9V034 is compatible with
modular OpenMV base boards. Verify each physical board/module combination at
bring-up and record the firmware version.

## Agreed decisions

- Initial research priority: ground-based tracking/avoidance; later, deploy
  selected configurations on drones.
- Start with one GENX320, then a two-GENX320 synchronized ground rig. The
  present synchronization requirement is perception alignment, not a claimed
  sub-millisecond measurement guarantee.
- Connect host computers through USB initially. Host-launched, non-persistent
  scripts are the first deployment model; persistent standalone/autostart
  support is deferred.
- Provide maintained parameterized profiles so users are not required to write
  OpenMV MicroPython. Also allow a custom local MicroPython script through a
  documented helper/channel contract and retain an explicitly low-level
  arbitrary-script option for experiments.
- Initial GENX320 profile: histogram preview. It is a 320x320 event-activity
  grayscale frame stream, integrated with existing `olab_camera` web
  streaming. The local OpenMV viewer is only a bring-up diagnostic, not the
  lab workflow.
- Raw-event mode is a separate API. It streams timestamped batches, derives a
  rate-limited live preview, and can record event data; recording is optional
  for live viewing.
- Raw recordings use a compact, native self-describing format initially;
  external event-tool exports are deferred until an analysis target is chosen.
- Add OpenMV as an `olab-camera[openmv]` extra requiring Python >=3.12. Base
  `olab_camera` remains Python >=3.10 and must import without that extra.
- No ROS work in this scope.

## Proposed design

### Package boundary and dependency behavior

1. Add the `openmv` optional extra in `packages/olab_camera/pyproject.toml`,
   pinning an appropriate `openmv` host-library minimum and documenting its
   Python >=3.12 requirement.
2. Keep all imports of the optional host library lazy or guarded. Constructing
   an OpenMV class without the extra must give a clear `ImportError` describing
   the extra and Python requirement; importing `olab_camera` must continue to
   work unchanged.
3. Export public OpenMV classes conditionally in a way consistent with the
   RealSense optional dependency behavior, with tests for the absent-dependency
   case.

### Host control/session layer

Implement an OpenMV-specific session class (for example `OpenMVDevice`) that
wraps the official host `openmv.Camera` protocol client rather than treating
the device as a V4L2 camera. Its responsibilities are:

- address a device by explicit serial path, with optional discovery helpers;
- connect/disconnect cleanly and surface board/firmware/profile identity;
- stop a running script, upload and execute a profile or custom script, and
  collect stdout/log messages;
- enumerate/read/write named channels and expose connection/throughput/health
  counters;
- make script failures, disconnects, channel errors, and dropped preview/event
  data observable rather than silently retrying forever.

Define stable helper channels for profile metadata, configuration updates,
health/counters, structured results, and optional event transport. The
helper's payload envelopes should include schema version, profile identifier,
device time/sequence information, and an error/result discriminator. The
format should be simple enough for MicroPython and versioned before the first
custom-script users depend on it.

### Profiles and custom scripts

Ship maintained MicroPython profile assets with the package, separate from the
CPython host control code. Each profile has a typed host configuration model,
defaults, validation, metadata, and a declared set of channels.

Initial profiles:

1. `genx_histogram_preview`: configure GENX320 histogram mode, resolution,
   histogram rate, baseline brightness, contrast, bias preset, anti-flicker,
   spatio-temporal filtering, and hot-pixel-calibration policy; publish frames
   through the standard OpenMV frame stream plus health/config channels.
2. `genx_raw_record`: configure raw-event batching and the same sensor-control
   surface; publish binary event batches with loss/overflow counters and sync
   edge records; optionally publish a bounded-rate preview.
3. `mt9v034_frame_passthrough`: configure grayscale/global-shutter capture and
   publish frames plus basic health metadata. Add an onboard tracker only after
   the generic path is validated.

Provide a custom-script runner accepting a local `.py` file and a documented
helper import/contract. The helper route is recommended for interoperability;
the arbitrary-script path is expressly experimental and only guarantees script
lifecycle/stdout, not profile-level channels or frame/event semantics.

### Frame integration

Implement `CameraOpenMV(Camera)` for standard OpenMV frame output. Its capture
thread reads host-protocol frames, validates their declared dimensions/formats,
converts RGB data to the BGR `numpy.ndarray` convention expected by existing
`olab_camera` features, appends frames, timestamps them with host receipt time
and sequence number, and announces the base-class condition.

`CameraOpenMV` owns only frame mode and can immediately use existing
`startStream()` browser endpoints. It must support clean shutdown, reconnect
failure reporting, start/stop idempotence, and a documented newest-frame-wins
policy consistent with the existing frame deque. It must not claim that host
receipt time is a sensor-exposure timestamp.

### GENX320 event integration

Implement a separate `OpenMVEventStream`/recorder, not a `Camera` subclass.
Represent each batch as an explicit typed object containing:

- event payload and encoding/version;
- sensor event timestamps and source sequence range;
- received-at host monotonic time;
- ON/OFF counts, trigger-edge records, and source/host loss counters;
- active profile/sensor filter settings needed to interpret the data.

The profile reads raw sensor events in bounded batches. The host event stream
handles the protocol channel without blocking the frame/web-server capture
thread. It exposes a bounded queue and explicit overload policy/counters; the
implementation must choose and document whether loss occurs at the source,
transport, queue, preview, or recorder.

Generate a 320x320 preview on the host from received batches at a configurable
viewer rate (default 30--60 Hz). The preview is deliberately decoupled from
raw-event ingestion: lowering the preview rate reduces host/UI work but does
not reduce raw USB traffic. Allow that preview to feed a `CameraOpenMV`-like
frame publisher or small adapter only after its ownership/lifecycle is clear.

### Recording and pair synchronization

Define a versioned native recording container or session directory before raw
capture is exposed as a durable feature. It must store a manifest plus
append-only event chunks. Include:

- board/module identifiers, firmware and helper/profile versions;
- every profile/configuration change and GENX bias/filter/calibration setting;
- chunk sequence, sensor timestamp range, host monotonic timestamp range,
  event counts, trigger edges, and explicit loss/overflow counters;
- pair topology, common-trigger configuration, and calibration/measurement
  results when synchronization is active.

For the pair experiment, wire a common ground and one external pulse source to
each GENX320 P10/frame-sync input. Confirm that both event streams contain the
same trigger edges, then align their time bases in post-processing. Measure
and report residual trigger-time skew and drift over the intended run length;
do not advertise sub-millisecond alignment until the measured setup supports
it.

## Implementation steps

1. Add the optional dependency, guarded import behavior, public exports, and
   installation/deployment documentation.
2. Add an injectable protocol-client seam and implement/test `OpenMVDevice`
   connection, lifecycle, channel, logging, and error contracts without
   hardware.
3. Package the helper and `genx_histogram_preview` asset; add host-side profile
   configuration validation and profile metadata/health channel parsing.
4. Implement `CameraOpenMV`, connect its protocol frames to the existing
   `Camera` deque/condition contract, and exercise existing HTTPS streaming
   against mocked protocol frames.
5. Perform single-GENX320 hardware bring-up: update/record firmware, run the
   vendor local viewer as a diagnostic, then run the shipped profile through
   `CameraOpenMV` and an `olab_camera` browser endpoint.
6. Add `mt9v034_frame_passthrough`, validating global-shutter high-FPS behavior
   and keeping tracking as an optional post-processing/profile addition. Also
   consider an `ov5640_frame_passthrough` profile alongside it: both the
   RT1062 V5 and H7 Plus ship with an OV5640 5MP color rolling-shutter module
   preinstalled (before swapping in GENX320/MT9V034 modules), and since it
   speaks the same standard OpenMV frame-stream protocol as
   `mt9v034_frame_passthrough` (no exotic sensor mode to configure, unlike
   GENX320), it should be a cheap addition and doubles as a simple smoke test
   for the `CameraOpenMV` pipeline.
7. Implement the raw batch schema, event channel, bounded ingestion pipeline,
   derived-preview path, recording manifest/chunks, replay reader, and
   overload/loss accounting.
8. Add raw-event hardware tests with one GENX320, then wire and execute the
   two-RT1062 synchronized GENX320 experiment. Document the measured timing,
   not a theoretical claim.
9. Add the custom-script helper documentation and runner only after the
   maintained profiles establish stable channel and lifecycle semantics.
10. Defer persistent standalone deployment, additional onboard tracker
   profiles, data exporters, drone mounting/power integration, and ROS to
   follow-on plans.

## Testing and verification

- Unit-test absent optional dependencies, profile validation, script/profile
  selection, lifecycle sequencing, channel parsing, payload version rejection,
  error propagation, disconnect cleanup, and public exports with an injected
  fake OpenMV client.
- Unit-test `CameraOpenMV` conversion, declared-size/format validation,
  newest-frame behavior, timestamp metadata, capture-loop failure behavior,
  and use of the existing stream lifecycle with synthetic frames.
- Unit-test event-batch decoding, bounded-queue overload behavior, loss
  counters, preview rasterization/rate limiting, recording manifests/chunks,
  and deterministic replay.
- Add package/import tests proving base installs remain usable under Python
  3.10+ without OpenMV; run OpenMV integration tests under Python 3.12+.
- Hardware acceptance test, single GENX320: detect the board, upload/run the
  maintained histogram profile, show a changing 320x320 activity preview,
  serve it through `olab_camera`, change a profile setting, and cleanly stop
  and reconnect.
- Hardware acceptance test, raw GENX320: collect a known-duration session,
  verify event/preview operation, inspect the manifest, replay it, and create
  intentional load to verify a nonzero counter rather than silent corruption.
- Hardware acceptance test, MT9V034: capture expected grayscale frames at the
  selected resolution/rate, stream them, and benchmark at least one RT1062 and
  one H7 Plus configuration.
- Hardware acceptance test, synchronized pair: verify shared trigger edges in
  both recordings, quantify alignment and drift, and record the wiring and
  settings with the dataset.

## Risks and mitigations

- Raw-event volume is scene-dependent and can exceed USB, host, or disk
  capacity. Use sensor-side filters, bounded batches/queues, explicit counters,
  and measured workload limits before drone/field deployment.
- Browser streaming is suitable for operator visualization but not for timing
  measurement. Preserve sensor events and trigger edges separately.
- A camera's independent clock and USB receipt time do not establish precise
  cross-camera timing. Use physical trigger markers and report measurements.
- Optional-package API changes can break base installs. Keep imports guarded,
  test without extras, and document the Python 3.12 requirement prominently.
- Custom scripts can violate maintained profile assumptions. Clearly separate
  the supported helper contract from the low-level arbitrary-script mode and
  include profile/schema versions in recordings.

## Open questions for follow-on work

- Choose an external event-vision analysis/export target before committing to
  an interchange format.
- Define persistent standalone profile installation, configuration, update,
  and logging behavior for untethered drone use.
- Select onboard tracker/detection profiles after histogram and raw-event
  benchmarks establish useful operating points.
- Specify individual drone payload, power, vibration, and flight-stack
  integration only when moving beyond the host-connected research testbed.
