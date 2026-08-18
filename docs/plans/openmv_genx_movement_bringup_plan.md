# GENX320 on-device movement detection bring-up

## Goal

Prove that the attached GENX320 detects scene movement and have the OpenMV
board perform the first-stage processing itself. The laptop should receive
small, sequence-numbered movement-region records over USB, not raw event
streams or video frames.

## Current facts

- The board runs firmware reported as v5.0.0 by Protocol V2.
- The legacy OpenMV debug protocol client at the exact firmware-repository
  tag `v4.8.1` (`tools/pyopenmv.py`) has already executed a trivial script
  and returned stdout on this same current firmware. It is the initial
  hardware bring-up tool; it is not yet a production dependency.
- The earlier `sensor.reset()` attempt was not a GENX test. GENX320 must be
  initialized through `csi.CSI(cid=csi.GENX320)`, not the generic `sensor`
  module.
- The existing `genx_histogram_preview` profile contains the appropriate
  `csi` initialization shape, but it has never been verified on hardware.
- The V2 client has a local event-timeout compatibility patch under review;
  it must not be used as the only bring-up route until the same physical
  script has worked independently.

## Agreed decisions

- Detect arbitrary scene movement, not a deliberately blinking marker.
- Do initial processing on the board in GENX320 histogram mode.
- Report compact movement regions, not frames and not raw event records.
- Use USB only. WiFi and firmware downgrade are out of scope.
- Start with legacy `v4.8.1` `pyopenmv.py` for the first hardware experiment,
  then A/B the identical script with the V2 `OpenMVDevice` path.

## Proposed device result

At a deliberately low, fixed report rate (initially 5 Hz), emit one newline
delimited JSON record with this shape:

```json
{"seq": 41, "t_ms": 123456, "regions": [{"x": 101, "y": 77, "w": 24, "h": 18, "cx": 113, "cy": 86, "pixels": 192}]}
```

`regions` contains at most the largest few activity blobs. A minimum blob
pixel/area threshold rejects isolated noise and hot pixels. Empty regions are
valid and mean no material movement was detected in that reporting window.

The first bring-up version need not distinguish ON/OFF polarity. Add polarity
only after movement locations are reliable and its value is demonstrated.

## Bring-up stages

### 1. Confirm real GENX320 initialization

Use `tools/pyopenmv.py` from OpenMV tag `v4.8.1` to upload a minimal script
that imports `csi`, creates `csi.CSI(cid=csi.GENX320)`, resets/configures a
320×320 grayscale histogram stream, calls `snapshot()` in a loop, and prints
a startup marker plus FPS once per second.

Success gate: no `csi` initialization exception and sustained FPS output while
the scene is moved. Stop here and capture the full stdout traceback if this
fails; do not change host transport code to compensate for a device-script
failure.

### 2. Compute movement regions on board

Extend the proven script only after stage 1 passes. For each histogram image:

- use image blob detection against activity thresholds around the configured
  brightness baseline;
- reject regions below the agreed minimum pixel/area threshold;
- sort by size and retain a small fixed maximum number of regions;
- calculate bounding box, centroid, and pixel count on the board;
- print the JSON record no faster than 5 Hz, even if sensor snapshots run
  faster.

Test static, hand-motion, and large-motion scenes. Tune thresholds only from
captured observations; do not guess sensor/noise values in host code.

### 3. Establish stdout delivery limits

Poll the legacy text buffer fast enough to retain all records for a sustained
five-minute run. Capture sequence numbers, missing records, record size, and
observed report rate.

The current firmware's stdout ring buffer is bounded (1024 bytes), so this
stage decides whether stdout is adequate for this low-rate telemetry. If
sequence gaps occur, first lower record rate/size or raise host poll rate. If
gaps persist at the required rate, stdout is not the production transport and
a separate binary transport decision is required.

### 4. A/B the transport, not the device algorithm

Run the exact proven device script through patched Protocol V2
`OpenMVDevice.runSource()` and `readStdout()`. Compare startup success,
sequence gaps, report rate, reconnect recovery, and failure behavior to the
legacy run.

Only after this comparison choose the supported host transport. The legacy
client remains a useful diagnostic fallback; it is not incorporated into the
package merely because it was first to work.

### 5. Productize only a proven path

If a transport meets the delivery requirement, add a small host-facing
movement-result API and tests. Keep raw event streaming and browser video
out of this scope. The existing unimplemented V2 custom-channel helper is
not a candidate until real firmware semantics are confirmed.

## Verification

- Device-side: startup/FPS log and movement-region JSON from a real GENX320.
- Semantics: static scene yields empty/no regions; hand movement yields
  spatially plausible regions; noise threshold prevents persistent speckle.
- Transport: five-minute sequence-gap measurement and reconnect test.
- Host code, once changed: focused fake-client tests plus the full
  `pytest packages/olab_camera/tests` suite.

## Risks and mitigations

- **GENX module not initialized/wired:** fail at stage 1 with captured
  traceback; verify physical attachment/board support before code changes.
- **Output overwhelms stdout:** fixed low reporting rate, compact schema, and
  sequence-gap measurement before adopting it.
- **Noise/hot pixels look like motion:** use area thresholds; later add the
  documented calibration/filter settings only after basic operation is seen.
- **Protocol V2 regression:** legacy path provides an independent control;
  do not attribute a device-script failure to V2 without that comparison.
