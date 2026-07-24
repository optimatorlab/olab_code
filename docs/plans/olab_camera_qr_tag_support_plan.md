# olab_camera: QR tag reading, distance, and pose estimation

**Status: implemented.** This document originally proposed an automatic,
in-thread pose-computation design (tag-size config on `addQR()`, a
`Camera.tagPoseToWorld()`/`cameraPoseFromTag()` API). That design was
**abandoned mid-implementation** after user feedback revealed it duplicated
an existing, established pattern this task's original research had missed
(see "What actually shipped" below). This file has been rewritten to
describe the final, shipped design — see `.pairwork/
olab-camera-qr-tag-support.md` (gitignored, local history only) for the
full round-by-round story if you need it, or `git log` on the branch that
introduced this work.

## Goal

Add QR tag support to `olab_camera`:

1. Reliable decoding of QR codes even when viewed at a skew/oblique angle.
2. Distance-to-tag estimation given a known physical tag size and the
   camera's intrinsics.
3. Pose estimation: given the camera's own world-frame pose, compute a
   detected tag's world-frame position/orientation; and given a tag's known
   world-frame pose, compute the camera's world-frame position/orientation
   (e.g. for precision landing).

## What actually shipped

QR support follows the *same established pattern* ArUco/barcode already
used: detection threads (`_Aruco`, `_Barcode`, and the new `_QRCode`) report
raw data only — corners/IDs/payloads, no automatic pose or distance. Pose is
computed **on demand, by the caller's own `postFunction`**, via `olab_utils`
helper functions. This mirrors `docs/usage_guide.md`'s pre-existing ArUco
example (`aruco_post_poses()`, which already called `olab_utils.arucoFindPose()`
from a `postFunction`) — a pattern the original research for this task
missed because it grepped `packages/olab_camera/src` for pose-related code
but never grepped `packages/olab_utils` itself or read `packages/olab_camera/
docs/`. Worth checking both before designing another CV feature in this
package.

### `_QRCode` (new, in `cv_features.py`)

Mirrors `_Aruco`/`_Barcode` exactly:

```python
camera.addQR(idName, decoder='cv2', ids_of_interest=None, res_rows=None,
             res_cols=None, fps_target=5, postFunction=None,
             postFunctionArgs=None, color=(0,0,255))
```

- `decoder='cv2'` (default): `cv2.QRCodeDetector`, more robust to skewed/
  oblique views than pyzbar, and its corner order is anchored to the QR
  symbol's own finder-pattern structure — safe to use for pose.
  `decoder='pyzbar'`: corner order is *not* reliably anchored to the
  symbol's frame (empirically verified — physically rotating a tag does not
  rotate pyzbar's reported corner labeling in lockstep the way it does for
  `cv2.QRCodeDetector`), so only distance/position are meaningful if you
  compute pose from its corners, not orientation.
- `ids_of_interest`: filters which decoded payload strings are reported —
  same name and purpose as `addAruco()`'s parameter of the same name, just
  filtering payload text instead of numeric marker IDs.
- `camera.qr[idName].deque[0]` = `{'data': [...], 'corners': [...], 'color':
  ...}` — same shape/spirit as `_Barcode`'s deque, no pose/distance fields.
- Honors `res_rows`/`res_cols` for actual processing resolution (mirrors
  `_Aruco`'s `img_x_y`/`orig_x_y` resize-then-scale-back pattern) — corners
  are always reported in the original capture resolution's coordinates.
- `addQR()` validates `decoder` before constructing/storing anything, so an
  invalid decoder never leaves a stuck registry entry blocking a retry.

`_Aruco` itself needed **zero changes** — it already had everything needed
via the pre-existing `olab_utils.arucoFindPose()`.

### `olab_utils` additions

- `arucoFindPoseGlobal(cameraPose, rvec, tvec, cameraExtrinsics=None)` —
  fills in a previously-unfinished stub. Composes a tag's world-frame pose
  from the camera's own world pose and the tag's pose relative to the
  camera (`rvec`/`tvec`, from `arucoFindPose()`/`cv2.solvePnP`).
  `cameraPose`/`cameraExtrinsics` are `{'position': (x,y,z), 'orientation':
  (roll,pitch,yaw)}` dicts — meters/radians, FLU body convention, ENU world,
  per REP-103. `cameraExtrinsics` (the camera's fixed mount offset relative
  to the vehicle body) defaults to identity. Despite the "aruco" in the
  name (kept for continuity with `arucoFindPose()`), this is generic to any
  single tag's rvec/tvec — used for QR too.
- `arucoFindCameraPoseGlobal(tagPose, rvec, tvec, cameraExtrinsics=None)` —
  the inverse: given a tag's *known* world pose, returns the vehicle body's
  world pose (the precision-landing use case).
- Private `_rpyToMatrix()`/`_matrixToRpy()` helpers and a fixed camera-link
  ↔ optical rotation constant. **`_matrixToRpy()`'s gimbal-lock handling
  (pitch = ±90°) took three review rounds to get right** — worth reading
  its docstring/comments directly if you touch this function: the
  singularity check must threshold on `cos(pitch)` computed *directly from
  matrix entries* (`math.hypot(R[2,1], R[2,2])`), not on `1 - abs(sin(pitch))`,
  which is quadratically (not linearly) insensitive to the true deviation
  from the pole and silently zeroes real roll for any attitude within
  ~0.06° of vertical if used as the threshold input.

### `Camera.setPose()` / `setExtrinsics()`

Simplified to plain state setters (no math), mirroring how
`camera.intrinsics` is stored/read directly rather than through an accessor
method:

- `camera.setPose(x, y, z, roll, pitch, yaw)` → `camera.pose = {'position':
  (x,y,z), 'orientation': (roll,pitch,yaw)}`. `None` until called.
- `camera.setExtrinsics(x, y, z, roll, pitch, yaw)` → `camera.extrinsics =
  {...}`, defaults to identity.
- **No `Camera.tagPoseToWorld()`/`cameraPoseFromTag()` methods** — a
  `postFunction` reads `camera.pose`/`camera.extrinsics` directly and calls
  `olab_utils.arucoFindPoseGlobal()`/`arucoFindCameraPoseGlobal()` itself,
  the same way `arucoFindPose()` is already called directly (not through a
  `Camera` wrapper).

### Docs

`docs/usage_guide.md` has a full "QR Codes" section (right after the
existing barcode section) in the same format as the ArUco/barcode sections:
a `postFunction` example, the `addQR()` call, a stop cell, then a second
example mirroring `aruco_post_poses()` showing `arucoFindPose()`-based
distance and `arucoFindPoseGlobal()`/`arucoFindCameraPoseGlobal()`-based
world-frame composition for QR. That's the canonical worked example —
read it before writing new code against this API.

## Non-goals

- No WeChatQRCode / DNN-based QR detection.
- No automatic disabling or filtering of QR codes from the existing generic
  `_Barcode`/pyzbar path — both can run at once if you start them both.
- No change to 1D barcode handling in `_Barcode`.
- No persistence of camera pose across restarts — `setPose()`/
  `setExtrinsics()` are purely in-memory.

## Filed as follow-ups (not part of this task)

- [#19](https://github.com/optimatorlab/olab_code/issues/19) — a
  `decorate=True/False` option across all CV feature classes (pre-existing
  gap, not QR-specific).
- [#20](https://github.com/optimatorlab/olab_code/issues/20) — a reusable
  `olab_utils` helper consolidating the `*_post_poses()` `postFunction`
  boilerplate.
- [#21](https://github.com/optimatorlab/olab_code/issues/21) — whether/how
  to rename the `arucoFind*` functions, since they're not ArUco-specific.
