# Plan: issues #9, #21, #20 (olab_camera / olab_utils)

Design plan for the first three items of this session (step 4, hardware
verification, follows once this is implemented and reviewed). Produced via
`grillme` interview before any code is written; implementation/review will
follow via `pairwrite`/`pairreview`, same as the prior QR tag support session
(`.pairwork/olab-camera-qr-tag-support.md`).

Order of implementation: **#9 first**, then **#21**, then **#20** (since #20's
helper is named/built on top of #21's new names, and #9 must land before any
`addAruco()` exercise — including #20/#4's hardware tests — works on a fresh
OpenCV 5 venv).

---

## 1. Issue #9 — OpenCV 5 support in `_Aruco.__init__`

**Problem** (`packages/olab_camera/src/olab_camera/cv_features.py:79-86`):

```python
(major, minor, sub) = cv2.__version__.split(".")[:3]
if ((int(major) >= 4) and (int(minor) >= 7)):
    self.cv2dict   = cv2.aruco.getPredefinedDictionary(olab_utils.ARUCO_DICT[idName]['dict'])
    self.cv2params = cv2.aruco.DetectorParameters()
else:
    self.cv2dict   = cv2.aruco.Dictionary_get(olab_utils.ARUCO_DICT[idName]['dict'])
    self.cv2params = cv2.aruco.DetectorParameters_create()
```

OpenCV 5.0 is major=5, minor=0. `minor >= 7` is `False`, so it falls into the
`else` (deprecated-API) branch, which no longer exists in cv2 5.x → crash.
`opencv-contrib-python>=4.10.0` (no upper bound, in both `olab_camera` and
`olab_utils` `pyproject.toml`) resolves to latest (currently 5.0.0.93) on any
fresh install, so this bites any new venv/CI/teammate immediately.

**Fix**: mirror `olab_utils._resolveTrackerFactory()`'s existing pattern for
this exact class of problem (see `packages/olab_utils/src/olab_utils/__init__.py:88-109`
and its docstring/tests) — feature-detect via `hasattr()`, not
`cv2.__version__` string parsing.

**Placement decision**: new helper lives in `olab_utils`, not inline in
`cv_features.py` — same package as `_resolveTrackerFactory()`, same
`cv2_module` injection pattern for testability without needing two real
OpenCV installs, and `olab_utils` already owns `ARUCO_DICT`/aruco-adjacent
constants.

```python
def _resolveArucoDictAndParams(dictID, cv2_module=cv2):
    '''
    Return (dict, params) for the given ARUCO_DICT key, using
    cv2_module.aruco.getPredefinedDictionary()/DetectorParameters() when
    present (OpenCV >=4.7), falling back to the deprecated
    Dictionary_get()/DetectorParameters_create() otherwise.

    Feature-detected via hasattr() (not cv2.__version__ parsing, which broke
    outright on OpenCV 5.x -- see _resolveTrackerFactory() for the same
    fix applied to trackers). `cv2_module` is injectable for testing;
    real callers should never pass it.
    '''
    aruco = cv2_module.aruco
    if hasattr(aruco, 'getPredefinedDictionary'):
        return (aruco.getPredefinedDictionary(dictID), aruco.DetectorParameters())
    return (aruco.Dictionary_get(dictID), aruco.DetectorParameters_create())
```

`_Aruco.__init__` becomes:

```python
(self.cv2dict, self.cv2params) = olab_utils._resolveArucoDictAndParams(
    olab_utils.ARUCO_DICT[idName]['dict'])
```

**Tests**: new `packages/olab_utils/tests/test_aruco_dict_resolution.py`
(or add to `test_trackers.py`'s neighborhood — writer's call), following
`test_trackers.py`'s exact style: a `types.SimpleNamespace` fake `cv2.aruco`
with only the modern methods, one with only the deprecated methods, asserting
the right branch is picked; plus one "real installed cv2" sanity check.

**Verification environment**: to actually exercise the OpenCV 5.x branch (the
"real installed cv2" sanity check above, and any scratch venv used to confirm
the fix), install **`opencv-contrib-python`**, not plain `opencv-python` —
`cv2.aruco` only exists in the contrib build, both packages install into the
same `cv2` namespace, and having both installed in one venv causes import
conflicts. This matches `packages/olab_camera/pyproject.toml` and
`packages/olab_utils/pyproject.toml`'s existing declared dependency
(`opencv-contrib-python>=4.10.0`) — any test/scratch venv should mirror that,
not substitute the plain package. Applies equally to step 4's hardware-test
venv.

---

## 2. Issue #21 — rename `arucoFindPose()`/`arucoFindPoseGlobal()`/`arucoFindCameraPoseGlobal()`

**Decision: deprecate and rename** (option 3 from the issue), not leave-as-is
or alias-only, based on a downstream-repo check done during this interview:

```
grep -rn "arucoFindPose\b|arucoFindPoseGlobal|arucoFindCameraPoseGlobal" \
  ub_racer arbotix_private warehouse_drone ofm IE-482-582 --include=*.py --include=*.ipynb
```

Results:
- **`ub_racer`, `arbotix_private`, `warehouse_drone`**: zero hits. These are
  the repos the pin-policy memory (`feedback_olab_code_pin_policy`) says float
  on `@main` — a rename is safe for them (nothing to break).
- **`ofm`**: hits only in `ofm/tmp/*.ipynb` (gitignored scratch notebooks,
  only `arucoFindPose`). `ofm` is **SHA-pinned** per the same policy memory —
  a main-branch rename has zero effect until that pin is deliberately bumped,
  at which point the deprecation shim (below) keeps the old name working
  anyway.
- **`IE-482-582`**: hits are in `ub_utils.py`, a pre-migration (`ub_code`-era)
  local copy, not an `olab_code` package consumer — irrelevant to this rename.

So the risk this issue worried about (a breaking rename bought without
checking) is now checked and low. Still shipping a compatibility shim rather
than a hard break, since it's nearly free and this session isn't the place to
force every consumer to update immediately.

**New names** (exactly as proposed in the issue):
- `arucoFindPose` → `findTagPose`
- `arucoFindPoseGlobal` → `findTagPoseGlobal`
- `arucoFindCameraPoseGlobal` → `findCameraPoseGlobal`

**Mechanics**: the implementation (docstrings, body) moves to the new names.
Old names become thin wrappers:

```python
def arucoFindPose(*args, **kwargs):
    '''Deprecated alias for findTagPose() -- see issue #21. Kept working indefinitely.'''
    warnings.warn(
        "olab_utils.arucoFindPose() is deprecated; use findTagPose() instead "
        "(not ArUco-specific -- see issue #21).",
        DeprecationWarning, stacklevel=2)
    return findTagPose(*args, **kwargs)
```

(same shape for the other two). **No planned removal date** — per this
session's decision, given the risk is already low, these stay indefinitely
rather than requiring a tracked future-removal issue.

**Docs**: update `packages/olab_camera/docs/usage_guide.md`'s `aruco_post_poses()`/
`qr_post_poses()` examples and the line-365 NOTE to reference the new names
(the NOTE itself becomes largely redundant once names are generic, but should
mention the old names still work, deprecated). This doc will also change
substantially under #20 (below) — reasonable to do both edits together.

**Tests**: existing `test_arucoFindPose_*`/`test_arucoFindPoseGlobal_*` tests
in `packages/olab_utils/tests/` get renamed/retargeted to call `findTagPose`/
`findTagPoseGlobal`/`findCameraPoseGlobal` directly; add one small test per
old name asserting it still works and emits `DeprecationWarning`
(`pytest.warns(DeprecationWarning)`).

---

## 3. Issue #20 — reusable `postFunction` pose helper

**Signature** (issue's own proposal, confirmed as-is):

```python
def findTagPoses(corners_list, ids_or_data, tag_size, cameraMatrix, dist,
                  cameraPose=None, cameraExtrinsics=None, flags=cv2.SOLVEPNP_IPPE_SQUARE):
```

- `corners_list` — list of per-detection corner arrays (`camera.aruco[idName].deque[0]['corners']`
  or `camera.qr[idName].deque[0]['corners']` — both are the same shape today).
- `ids_or_data` — parallel list: ArUco numeric ids or QR payload strings.
- `tag_size` — single scalar, **meters**, applied to every detection in this
  call (matches how `aruco_post_poses()`/`qr_post_poses()` use one
  `TAG_SIZE_INCHES` today — no mixed-size support, that's out of scope here).
  Builds `objPoints` internally from `tag_size` exactly like the existing
  hand-rolled examples do.
- `cameraMatrix`, `dist` — as today, from `camera.intrinsics[res]`.
- `cameraPose`, `cameraExtrinsics` — optional; when `cameraPose` is given,
  also compute world-frame pose via `findTagPoseGlobal()`.
- `flags` — passed through to `findTagPose()`/`cv2.solvePnP()`, default
  unchanged (`cv2.SOLVEPNP_IPPE_SQUARE`).

**Return shape**: list of dicts, one per **successful** detection (see
failure handling below):

```python
{
    'id': ...,              # from ids_or_data[i]
    'rvec': ...,             # raw solvePnP output
    'tvec': ...,              # raw solvePnP output (x, y, z in meters, camera-optical frame)
    'distance': ...,         # Euclidean norm of tvec (meters) -- straight-line range to tag
    'worldPosition': ...,     # from findTagPoseGlobal(), or None if cameraPose not given
    'worldOrientation': ...,  # from findTagPoseGlobal(), or None if cameraPose not given
}
```

`tvec` already carries the raw per-axis (x, y, z) camera-frame offsets (as
today's hand-rolled examples print via `meters2inches(tvecs[0/1/2])`), so
per-axis distance is available via `tvec` directly — no separate field
needed. `distance` is the Euclidean norm (`np.linalg.norm(tvec)`), the
straight-line range, not just z-depth.

**Failure handling**: a per-detection `cv2.solvePnP()` failure (`ret=False`,
from `findTagPose()`) is **silently skipped** — that detection is simply
omitted from the returned list. This matches today's `if (ret):` guard in
`aruco_post_poses()`/`qr_post_poses()`, which just skips printing on failure.
Callers who need failures surfaced (e.g. to keep index alignment with
`corners_list`) aren't served by this helper's return shape — not a
requirement raised by the issue or the existing examples, so out of scope.

**Uses #21's new names internally** (`findTagPose()`, `findTagPoseGlobal()`),
not the deprecated aliases.

**Docs**: `packages/olab_camera/docs/usage_guide.md`'s `aruco_post_poses()`
and `qr_post_poses()` examples get rewritten to call `olab_utils.findTagPoses()`
instead of hand-rolling the loop/objPoints/solvePnP/compose steps — this is
the actual boilerplate reduction the issue asks for. The `TAG_SIZE_INCHES`/
`inches2meters()` conversion stays in the example (helper takes meters).

**Tests**: new tests in `packages/olab_utils/tests/` covering: basic
single/multi-detection pose recovery, world-pose composition when
`cameraPose` given vs `None`, a `ret=False` detection being skipped rather
than crashing or producing a `None` entry, and (reusing existing fixtures
from `test_utils` where present) that results match calling `findTagPose()`/
`findTagPoseGlobal()` directly for the same inputs.

---

## 4. (Not part of this design doc) Hardware verification

Once #9/#21/#20 are implemented and reviewed: update the gitignored
`examples/qr_hardware_test.ipynb` to call the new `findTagPoses()` helper
instead of its hand-rolled callback, and verify accurate distance/world-position
for both QR (`addQR()`) and ArUco (`addAruco()`) against a calibrated camera
and known tag sizes. Camera calibration status TBD — ask before assuming it's
already done.

---

## Open items for the writer (not blocking, but worth flagging in pairwrite handoff)

- Where exactly the new `test_aruco_dict_resolution.py`-equivalent tests for
  #9 live (own file vs. added to `test_trackers.py`'s neighborhood) — writer's
  call, no strong preference expressed.
- Whether `_resolveArucoDictAndParams()` should be private (leading
  underscore, like `_resolveTrackerFactory()`) or public — recommend private,
  matching the existing pattern exactly (it's an internal implementation
  detail of `_Aruco.__init__`, not something callers need directly).
