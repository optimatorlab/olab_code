# Plan: finish OpenCV 5 support -- `arucoDetectMarkers()` + face detection (YuNet)

Extends issue #9 ("Add support for OpenCV 5"), which is not fully closed by
the already-merged `_Aruco.__init__` fix. Found via the user's manual
testing (step 4 of the original plan) plus a follow-up audit of every
`cv2.*` call in `olab_camera`/`olab_utils` against the real OpenCV 5.0.0
install and the official
[OpenCV 4-to-5 migration wiki](https://github.com/opencv/opencv/wiki/OpenCV-4-to-5-migration).
Design pressure-tested via `grillme`; implementation follows via
`pairwrite`/`pairreview`, same as the rest of this session.

## Scope decision: drop OpenCV <4.7 support entirely

Confirmed via both currently-deployed RPi fleets' actual install docs
(`ofm/docs/installation_companion.md`, `ub_racer/car/{install,README}.md`):
both `pip install "opencv-contrib-python>=4.10.0"` into a venv, never touch
apt-installed system OpenCV for CV code. The "RPi v4.4.0" comment in
`_resolveTrackerFactory()`'s docstring/tests was introduced by a single
verbatim migration commit (`ead687f`) from `ub_code` and never re-verified
-- it's stale. `opencv-contrib-python>=4.10.0` is already both packages'
declared `pyproject.toml` minimum, well past the 4.7 boundary.

**Consequence**: `_resolveArucoDictAndParams()` (already merged) and the new
ArUco-detection fix below drop their deprecated-API fallback branches
entirely -- require `cv2.aruco.getPredefinedDictionary`/`ArucoDetector` to
exist, raise `AttributeError` (not silently limp along on the wrong shape)
if they don't. Same reasoning extends to face detection: no dual-branch
Caffe-vs-TensorFlow-vs-YuNet logic, just YuNet everywhere.

---

## 1. Fix `olab_utils.arucoDetectMarkers()`

**Problem**: `cv2.aruco.detectMarkers(img, arucoDict, parameters=arucoParams)`
(the free function) is called every detection cycle
(`packages/olab_utils/src/olab_utils/__init__.py:417`, called from
`_Aruco._thread_Aruco()`). Removed outright in OpenCV 5.0 (confirmed
empirically: `hasattr(cv2.aruco, 'detectMarkers')` is `False`). Replacement:
`cv2.aruco.ArucoDetector(dict, params).detectMarkers(img)` (confirmed
working). This is why the OpenCV-5 crash the user hit moved from
construction time (fixed) to every detection cycle (not yet fixed).

**Fix**: since we're dropping <4.7 support (see above), no feature-detection
branch is needed here -- `ArucoDetector` is required to exist.

**Revised after review** (superseding this plan's first draft, which
proposed constructing a fresh `ArucoDetector` on every call to
`arucoDetectMarkers()` -- i.e. every detection cycle, since
`_thread_Aruco()`'s `while self.camObject.camOn:` loop calls it at
`fps_target` Hz for as long as detection runs, not just once at `addAruco()`
time). That first draft's justification was "construction is cheap," which
is true but not a reason to prefer reconstruction -- it only defends the
worst case as harmless, it doesn't argue reconstruction is *better* than
caching. The existing codebase's actual established pattern is "resolve
once at `_Aruco.__init__()`, reuse across every frame" -- exactly what
`self.cv2dict`/`self.cv2params` already do. Reconstructing the detector
every frame broke that precedent for no real benefit, which is a legitimate
design smell independent of the measured cost.

**Empirical evidence** (for the record, and to justify the one remaining
per-call construction path below -- not as a justification for the
abandoned every-frame-reconstruction design): measured on the dev machine,
via `timeit`, `cv2.aruco.ArucoDetector(dict, params)` construction costs
~0.7 microseconds/call, versus ~546 microseconds/call for
`detectMarkers()` itself on a blank 640x480 frame (a real frame with
markers to locate would cost more, not less) -- construction is roughly
0.1% of the per-frame cost. That ratio should hold on RPi hardware too,
since construction is a trivial O(1) object wrap independent of image size
or CPU class, while detection is the compute-bound part that scales with
hardware. This number matters for the `countArucoInImage()` fallback path
below (confirms it's not a hot loop concern there either), not as a reason
to prefer per-frame reconstruction over caching.

**Revised design**: `_Aruco.__init__()` builds the detector once, alongside
`self.cv2dict`/`self.cv2params`:

```python
self.arucoDetector = cv2.aruco.ArucoDetector(self.cv2dict, self.cv2params)
```

`arucoDetectMarkers()` gains an optional `detector=` parameter: when given,
it's used directly (no construction); when omitted (`None`, the default),
one is built fresh internally, same as before. This keeps the function's
existing signature backward-compatible for its other caller,
`countArucoInImage()` (`__init__.py:1089`, a one-shot standalone utility --
not a per-frame loop, so per-call construction there is fine, per the
benchmark above), while letting `_Aruco._thread_Aruco()` pass its cached
`self.arucoDetector` and avoid any repeated construction at all:

```python
def arucoDetectMarkers(img, arucoDict, arucoParams, img_x_y=None, orig_x_y=None, detector=None):
    ...
    try:
        if detector is None:
            detector = cv2.aruco.ArucoDetector(arucoDict, arucoParams)
        (corners, ids, rejected) = detector.detectMarkers(img)
        ...
```

`_thread_Aruco()`'s call site becomes
`olab_utils.arucoDetectMarkers(img, self.cv2dict, self.cv2params, img_x_y=img_x_y, orig_x_y=orig_x_y, detector=self.arucoDetector)`.
`arucoDict`/`arucoParams` are still passed through (kept for signature
stability/clarity and because `detector` alone doesn't expose them back to
the caller), even though they're unused internally when `detector` is
given.

**`_resolveArucoDictAndParams()`** (already merged): drop the
`Dictionary_get`/`DetectorParameters_create` fallback branch per the scope
decision above -- becomes a direct call, no `hasattr()` check needed. (Could
even be inlined back into `_Aruco.__init__` now that there's no branching
left to encapsulate, but keeping it as a named function costs nothing and
preserves the existing test file's structure -- writer's call.)

**Docstring cleanup**: `arucoDetectMarkers()`'s docstring
(`__init__.py:394-395,400-405`) still describes/shows the deprecated
`Dictionary_get()`/`DetectorParameters_create()` construction pattern in its
example -- update to match `_resolveArucoDictAndParams()`'s actual (now
sole) code path.

**Tests**: update `test_aruco_dict_resolution.py`'s "falls back to
deprecated API" test (no longer applicable -- replace with an
"AttributeError on cv2.aruco lacking the modern API" test, confirming the
fail-loud behavior). Add coverage for the new detection call's `detector=`
parameter specifically: a fake `detector` object (e.g. recording whether
its `detectMarkers()` was called and asserting `ArucoDetector` itself was
*not* constructed when `detector=` is given, vs. confirming a fresh one
*is* built when `detector=None`/omitted) -- this is the behavior the
caching design actually depends on, not just "does detection work." Also:
a real end-to-end check (already-bundled synthetic ArUco image, if one
exists in `olab_camera`'s test fixtures, or a simple "detects nothing on a
blank image without raising" check) against the actual installed OpenCV 5,
and a check that `_Aruco.__init__` builds `self.arucoDetector` once and it
gets reused (not rebuilt) across multiple `_thread_Aruco()` cycles.

---

## 2. Rewrite face detection to use `cv2.FaceDetectorYN` (YuNet)

**Problem**: `_FaceDetect._thread_FaceDetect()`
(`cv_features.py:1147-1176`) calls `cv2.dnn.readNetFromCaffe()` (the
**default** `dnn='caffe'` path) and `cv2.dnn.readNetFromTensorflow()` (the
`dnn='tensorflow'` path). Per the official migration wiki: "The
`readNetFromDarknet()` and `readNetFromCaffe()` functions have been
removed" in OpenCV 5, with no drop-in replacement (real model conversion to
ONNX required). `readNetFromTensorflow()` still exists but there's no
reason to keep two code paths once we're replacing the default anyway.

**The wiki's own recommendation** for new projects: `cv2.FaceDetectorYN`
(YuNet) -- "no contrib required." Confirmed present with an identical
`create()` signature on both our declared minimum (OpenCV 4.10.0, per
official docs) and the installed OpenCV 5.0.0 (empirically) -- one code
path, no version branching, covers our entire supported range.

### Public API changes (`Camera.addFaceDetect()`)

Current: `addFaceDetect(self, res_rows=None, res_cols=None, fps_target=5, postFunction=None, postFunctionArgs={}, color=(0,255,255), conf_threshold=0.7, dnn='caffe', device='cpu', modelPath=None)`

- **`dnn=` removed entirely** (breaking). Confirmed real callers:
  `ofm/ofm/vehicle/camera_services.py:486-487`
  (`cam.addFaceDetect(dnn=args.get('dnn', 'caffe'), ...)`, driven by a
  remote `requestSetFaceDetect` command) and
  `arbotix_private/client/client/scripts/client.py:312-317`
  (`addFaceDetect(..., dnn=dnn, device=device, ...)`). Both repos get a
  `gh issue create` filed against them describing the needed update, **after**
  this olab_code change is implemented and approved/merged (per user
  instruction -- so the issue can reference the actual landed change).
  `ofm` is SHA-pinned (per the pin-policy memory) so this doesn't break it
  immediately; `arbotix_private`'s pin wasn't found in this pass (no
  `olab_code` reference in its `requirements.txt` -- worth flagging to
  whoever files/handles that issue, not blocking here).
- **New `model_name=` parameter** replaces `dnn=`'s role, chosen for
  consistency with both (a) `cv2.FaceDetectorYN.create()`'s own first
  positional argument, literally named `model` (a file path) -- "we're just
  providing a wrapper to their work," per the user -- and (b)
  `addUltralytics()`'s existing `model_name` convention
  (`camera.py:745`, `cv_features.py:1675`) elsewhere in this same package.
  Default: `'face_detection_yunet_2023mar.onnx'` (fp32, higher accuracy,
  matches the old default's bias toward the more-accurate option). Callers
  needing lower resource usage (e.g. Raspberry Pi) can pass
  `model_name='face_detection_yunet_2023mar_int8.onnx'`. Resolved against
  `modelPath` exactly like today's hardcoded filenames are.
- **`device='cpu'|'gpu'`** kept as-is (not part of the OpenCV-5 problem,
  no confirmed breakage) -- now maps directly to `FaceDetectorYN.create()`'s
  `backend_id`/`target_id` constructor params instead of the old
  post-hoc `net.setPreferableBackend()`/`setPreferableTarget()` calls.
  Incidentally fixes a latent bug in the current code
  (`cv_features.py:1173`: `net.setPreferableBackend(cv2.dnn.DNN_TARGET_CPU)`
  passes a *target* constant to a *backend*-setting call) --
  `'cpu'` -> `backend_id=cv2.dnn.DNN_BACKEND_DEFAULT, target_id=cv2.dnn.DNN_TARGET_CPU`;
  `'gpu'` -> `backend_id=cv2.dnn.DNN_BACKEND_CUDA, target_id=cv2.dnn.DNN_TARGET_CUDA`.
- **`conf_threshold`** kept as the public parameter name (consistent with
  `addUltralytics()`'s own `conf_threshold`), mapped internally to
  `FaceDetectorYN.create()`'s `score_threshold` argument.

### Detector construction (fixed per reviewer finding, round 1)

`cv2.FaceDetectorYN.create(model, config, input_size, score_threshold=..., nms_threshold=..., top_k=..., backend_id=..., target_id=...)`
**requires `input_size` as its 3rd positional argument at creation time** --
confirmed via the installed binding's own docstring. The plan's first draft
said to call `detector.setInputSize(img_x_y)` *after* `create()`, which
cannot work: `create()` itself would already need a value for the required
`input_size` parameter before that call could even be made. Fixed: pass
`input_size=img_x_y` directly to `create()`; no separate `setInputSize()`
call afterward (nothing to update it for later, given the already-documented
out-of-scope "camera resolution changes mid-run" gap -- `img_x_y` is fixed
for the thread's lifetime, same as `self.cv2dict`/`self.cv2params`/
`self.arucoDetector` are for `_Aruco`).

**New `olab_utils` helpers**, mirroring `_resolveArucoDictAndParams()`'s
existing pattern (injectable `cv2_module` for testing, called once and
cached by the owning `_*Detect` class) rather than inlining this directly
into `_FaceDetect`, both for consistency with the established convention
and because it's what makes the deterministic tests below possible without
needing the real bundled ONNX model:

```python
def _resolveFaceDetector(modelFile, input_size, score_threshold, backend_id, target_id, cv2_module=cv2):
    '''Build a cv2.FaceDetectorYN. `cv2_module` is injectable for testing.'''
    return cv2_module.FaceDetectorYN.create(
        modelFile, '', input_size,
        score_threshold=score_threshold,
        backend_id=backend_id, target_id=target_id)


def detectFaces(img, detector, img_x_y=None, orig_x_y=None):
    '''
    Run detector.detect(img) and convert YuNet's raw [num_faces, 15] output
    (or None, when nothing is detected) into (confidence, corners,
    landmarks) -- corners in the existing [(x1,y1),(x2,y2)] int-pixel shape
    (decorateFaceDetect()'s expected format), landmarks as 5 (x,y) int
    tuples per face (right eye, left eye, nose tip, right mouth corner,
    left mouth corner). Coordinates are scaled from the (possibly
    downscaled) processing resolution back to the original capture
    resolution when img_x_y != orig_x_y, same xscale/yscale approach as
    arucoDetectMarkers(). Deterministic rounding: int(round(v)) applied
    after scaling, for both corners and landmarks -- YuNet returns floats
    regardless of whether any scaling is needed, and decorateFaceDetect()'s
    cv2.rectangle() call requires integral pixel coordinates (the
    pre-existing corner contract: the old SSD-based code already computed
    int() bbox coordinates before publishing).
    '''
    (ret, faces) = detector.detect(img)
    if faces is None:
        return ([], [], [])
    ...
```

Handles the no-detection case explicitly (`faces is None`, confirmed as
`FaceDetectorYN.detect()`'s behavior when nothing is found, not an empty
array) by publishing empty, parallel `confidence`/`corners`/`landmarks`
lists -- `decorateFaceDetect()`'s existing `for i in range(0, len(corners))`
loop already handles empty lists gracefully, so no separate empty-case
handling needed there.

`_FaceDetect.__init__` calls `olab_utils._resolveFaceDetector(...)` once
(mirroring `_Aruco.__init__`'s `self.arucoDetector` caching) and stores
`self.faceDetector`; `_thread_FaceDetect()` calls
`olab_utils.detectFaces(img, self.faceDetector, img_x_y=img_x_y, orig_x_y=orig_x_y)`
each cycle.

### Detection output format

`FaceDetectorYN.detect(image)` returns `(retval, faces)` where `faces` is
`None` (no detections) or a `[num_faces, 15]` array per face:
`[x, y, w, h, x_re, y_re, x_le, y_le, x_nt, y_nt, x_rmc, y_rmc, x_lmc, y_lmc, score]`
(bbox top-left + width/height, right eye, left eye, nose tip, right mouth
corner, left mouth corner, score) -- confirmed via the installed OpenCV
5.0.0's own docstring.

- **`corners`**: converted to the existing `[(x1,y1),(x2,y2)]` shape
  (`decorateFaceDetect()`'s expected format,
  `olab_utils/__init__.py:821-847`) from `(x,y,w,h)` -- `(x1,y1) = (x,y)`,
  `(x2,y2) = (x+w, y+h)`, each coordinate `int(round(...))` after scaling
  (see above). **Contract unchanged from today** (int pixel pairs) even
  though the underlying values are now floats pre-conversion -- zero
  downstream impact on existing `postFunction`/decoration code.
- **`confidence`**: the score column (index 14), same as today.
- **New `landmarks` field** added to `deque[0]`
  (`{'confidence': [...], 'corners': [...], 'landmarks': [...], 'color': ...}`):
  purely additive, per user's explicit choice. `landmarks[i]` is a list of
  5 `(x, y)` tuples per face, in the order YuNet returns them (right eye,
  left eye, nose tip, right mouth corner, left mouth corner). **Not drawn**
  by `decorateFaceDetect()` in this task -- that stays bounding-box-only,
  matching today's behavior; drawing landmark points is a natural future
  enhancement but not required here (keeps this task's diff focused).

### Processing-resolution fix (`res_rows`/`res_cols`)

Per user's explicit choice to fix this now: `_thread_FaceDetect` currently
calls `self.camObject.getFrameCopy()` with **no** `resOption`, silently
ignoring the `res_rows`/`res_cols` constructor args entirely (unlike
`_Aruco`/`_QRCode`, which both resize to a processing resolution and scale
corners back). Fix, mirroring `_Aruco._thread_Aruco()`'s
`img_x_y`/`orig_x_y` pattern (and `_QRCode._scaleCorners()`'s scaling math,
though implemented directly here rather than mechanically reused, since the
bbox+5-landmark-point shape differs from `_QRCode`'s corner-array shape):

- `img_x_y = (self.res_cols, self.res_rows)`,
  `orig_x_y = (self.camObject.res_cols, self.camObject.res_rows)`.
- `getFrameCopy(resOption=img_x_y if img_x_y != orig_x_y else None)`.
- After detection, if `img_x_y != orig_x_y`: scale every `(x, y)` pair in
  both `corners` and `landmarks` by `xscale = orig_x_y[0]/img_x_y[0]`,
  `yscale = orig_x_y[1]/img_x_y[1]`, so reported coordinates are always in
  the original capture resolution's coordinate system, same contract as
  `_Aruco`/`_QRCode`.
- `detector`'s `input_size` is set once via `_resolveFaceDetector(..., img_x_y, ...)`
  at `_FaceDetect.__init__` time (see "Detector construction" above --
  passed into `FaceDetectorYN.create()` directly, not a separate
  post-creation `setInputSize()` call). **Known gap, explicitly out of
  scope**: if the *camera's own* native resolution changes mid-run,
  `input_size` becomes stale -- same already-acknowledged,
  not-currently-handled class of gap called out in a commented-out `FIXME`
  in `_thread_Aruco()`'s own docstring ("This won't work if cam resolution
  has changed"). Not solved here, not solved elsewhere in this codebase
  today.

### Model files

- **Bundle two new files** under
  `packages/olab_camera/src/olab_camera/cv2_dnn_models/`:
  `face_detection_yunet_2023mar.onnx` (232,589 bytes) and
  `face_detection_yunet_2023mar_int8.onnx` (100,416 bytes). Source: OpenCV
  Zoo (`github.com/opencv/opencv_zoo`, `models/face_detection_yunet/`),
  MIT-licensed. Files are Git-LFS-backed upstream -- fetch via
  `media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/<filename>`
  (confirmed working, correct byte-for-byte sizes match the LFS pointer's
  declared size), not the plain `raw.githubusercontent.com` URL (which
  serves a 131-byte LFS pointer file, not the binary). Commit as normal
  binary blobs, same as the existing bundled Caffe/TensorFlow model files
  (no Git LFS in this repo) -- both new files together (~325 KB) are far
  smaller than the ~8 MB of files they replace.
- **Delete** the old bundled files, now unused:
  `deploy.prototxt`, `opencv_face_detector.pbtxt`,
  `opencv_face_detector_uint8.pb`, `res10_300x300_ssd_iter_140000_fp16.caffemodel`.
- **Not bundled now, tracked separately**:
  `face_detection_yunet_2026may.onnx` (dynamic input shape, OpenCV-5.x
  oriented) -- risks reintroducing version-specific behavior differences
  against our declared 4.10.0 minimum. File a `gh issue create` on
  `olab_code` itself (this repo) tracking evaluation/support for it later;
  no implementation now.

**Added per reviewer finding, round 1 -- clean-wheel packaging
verification**: `cv2_dnn_models/`'s binary files are runtime package data,
and local `pytest` runs against an editable install don't prove a real
(non-editable) wheel actually ships them -- `pip install -e` symlinks/
points at the source tree directly, so a packaging misconfiguration
(missing `[tool.hatch.build]` include rule, `.gitignore`/`MANIFEST`-style
exclusion, etc.) could pass every existing test while still shipping a
broken release wheel. Checked during `grillme`: `python -m build --wheel
packages/olab_camera` (against the *current*, pre-this-task tree) already
includes all four existing `cv2_dnn_models/*` binary files with no extra
`pyproject.toml` config beyond the existing bare `packages = ["src/olab_camera"]`
-- hatchling's default wheel-building behavior includes non-`.py` files
under a declared package directory. So no `pyproject.toml` change is
expected to be *needed* for the new `.onnx` files, but "should work by
default, per today's file layout" isn't the same as "verified for this
task's actual change" -- add this as an explicit Stage 2 verification step,
per `CONTRIBUTING.md`'s own pre-commit checklist item 1 ("Build and install
each touched package fresh"):
1. `python -m build --wheel packages/olab_camera` after the model-file
   swap.
2. Inspect the built wheel's contents directly (e.g. `zipfile.ZipFile(...).namelist()`)
   and assert: both new `.onnx` files present at their expected paths and
   sizes; all four old Caffe/TensorFlow files absent.
3. `pip install` that wheel into a fresh venv (not editable) with the
   package's own declared dependencies, and construct
   `Camera().addFaceDetect(model_name=...)` for both bundled `model_name`
   values against it, confirming both resolve and load correctly from the
   installed (non-source-tree) location.
Record the exact commands and pass/fail output in this pairwork file's Test
Results, not just "wheel builds successfully."

### Docs

- `packages/olab_camera/docs/usage_guide.md`'s "Face Detection" section
  (around line 433): update the `camera.addFaceDetect(...)` example --
  drop `dnn='caffe'`, note `model_name=` (default omitted /
  `'face_detection_yunet_2023mar.onnx'` shown as the explicit default),
  and update `postFaceDetect()`'s example to show the new `landmarks` key
  is available (not necessarily printed in the minimal example, but
  mentioned).
- `Camera.addFaceDetect()`'s docstring (`camera.py:621-644`) and
  `_FaceDetect`'s class/`__init__` docstrings (`cv_features.py:1015-1058`):
  update to describe YuNet instead of Caffe/TensorFlow, the new
  `model_name` parameter, and the `landmarks` output field.

### Tests

No existing tests cover face detection at all (`grep` for
`FaceDetect`/`facedetect` in `packages/olab_camera/tests/` returns nothing)
-- this is net-new coverage, not a rewrite of existing tests.

**Decided**: API-contract/no-crash checks only, no real face photo bundled
-- mirrors `test_qr_and_pose.py`'s approach (synthetically-generated images
only), sidestepping any licensing/consent question about a checked-in photo
of a real person. Real-detection accuracy is validated in the manual
hardware-verification step instead, same as ArUco/QR pose accuracy was
(this repo's established pattern for things that need an actual
camera/subject to meaningfully test).

**Added per reviewer finding, round 1 -- deterministic tests independent of
the real bundled model**: because `detectFaces()`'s YuNet-output-to-
`(confidence, corners, landmarks)` conversion is its own pure function
(see above), it's directly unit-testable with a fake `detector` object
(`detector.detect(img)` returning a hand-built `[N, 15]` array with
fractional coordinates, or `None`) -- no real ONNX model needed for this
part. Tests, in `packages/olab_utils/tests/`:
- Fractional-coordinate input at a non-1:1 `img_x_y`/`orig_x_y` scale:
  assert `corners`/`landmarks` come back as `int`s, correctly scaled
  (cross-checked by hand-computing the expected scaled+rounded values, same
  style as `test_find_tag_poses.py`'s cross-checks).
  assert `img_x_y == orig_x_y` (no scaling requested) still returns `int`s
  (YuNet's floats always need conversion, scaling or not).
- `detector.detect()` returning `None` (confirmed empirically as YuNet's
  actual no-detection behavior on the installed OpenCV 5.0.0 -- returns
  `(1, None)`, not `(1, empty array)`) -> `detectFaces()` returns
  `([], [], [])`, no exception.
- `_resolveFaceDetector()`: fake `cv2_module.FaceDetectorYN.create` (a
  `types.SimpleNamespace`-based fake, matching `test_trackers.py`'s/
  `test_aruco_dict_resolution.py`'s style) asserting it's called with
  `input_size` equal to the requested `img_x_y` (this is the check that
  would have caught this plan's own round-1 `create()`/`setInputSize()`
  bug) and with `score_threshold`/`backend_id`/`target_id` matching what
  was passed in.
- `decorateFaceDetect()` fed the `corners`/`confidence` shape
  `detectFaces()` actually produces (via a small integration-style test,
  not just the existing docstring-only coverage) -- confirms
  `cv2.rectangle()` doesn't raise on the now-float-derived-but-int-
  converted corners, closing the "can `_decorate()` consume the published
  corners without a drawing error" gap the reviewer flagged.

At minimum, still: construct `_FaceDetect`/call `addFaceDetect()` against
the real OpenCV 5.0.0 install and the real bundled model (both `model_name`
values) and confirm it doesn't crash (mirrors the manual `addAruco()` check
already done for issue #9) -- this is the check that would have caught the
original `readNetFromCaffe()` break, and now also the `create()`/
`input_size` bug this same round of feedback caught, had either existed
before this session's testing found them.

**Added per reviewer finding, round 2 -- fail-fast construction, and a
real worker-cycle test (not just the helper-level unit tests above)**:

*Fail-fast behavior.* `_FaceDetect.__init__` currently (pre-this-task, and
still true of the round-1 plan draft) wraps its entire body in one
`try/except Exception` that only logs and returns -- if `_resolveFaceDetector()`
fails (missing/corrupt packaged model, unsupported OpenCV build, invalid
custom `modelPath`, unavailable GPU device), `__init__` "succeeds" anyway
with `self.faceDetector` never set, `Camera.addFaceDetect()` still assigns
that broken object into `self.facedetect[idName]` and calls `.start()` on
it, and the thread then fails every single cycle in a noisy loop instead of
failing once at startup. **Fix**: `_FaceDetect.__init__` drops the
broad try/except entirely -- there is no genuinely optional/tolerable setup
step in it (unlike, hypothetically, a class with some non-essential
best-effort initialization), so every failure there should propagate.
`Camera.addFaceDetect()` already has its own `try/except Exception as e:
self.logger.log(...)` wrapping the `_FaceDetect(...)` construction + `.start()`
call -- when the constructor now raises, `self.facedetect[idName] = _FaceDetect(...)`
never completes its assignment (the right-hand side raises first), so
`camera.facedetect` gets **no entry at all**, `.start()` is never reached,
and `addFaceDetect()`'s existing catch logs exactly one actionable error.
This mirrors `addQR()`'s already-established pattern for invalid-decoder
rejection (`test_addQR_unknown_decoder_does_not_raise_and_does_not_register`) --
same "constructor failure -> no registry entry, one clean log line" contract,
just via natural exception propagation instead of a pre-validation check
(there's no cheap pre-validation possible here -- whether the model loads
successfully is exactly what construction has to determine). This is a
deliberate, scoped deviation from `_FaceDetect.__init__`'s specific old
error-swallowing behavior; not touching `_Aruco`'s or other feature
classes' `__init__` error handling, which is out of scope here.

Test: monkeypatch `olab_utils._resolveFaceDetector` to raise; call
`cam.addFaceDetect(...)`; assert `'default' not in cam.facedetect` (no
partial/broken entry) and that `addFaceDetect()` itself doesn't raise
(caught by its own existing try/except).

*Real worker-cycle test.* The helper-level fractional-coordinate tests
above prove `detectFaces()`'s own scaling/type contract in isolation, but
don't exercise `_thread_FaceDetect()`'s actual call path -- they can't by
themselves prove the acceptance criterion that `res_rows`/`res_cols`
actually take effect end-to-end. Added, mirroring
`test_qr_and_pose.py`'s `_make_camera_with_frame()`/`_stop_feature_thread()`
pattern and its existing
`test_addQR_processes_at_a_lower_resolution_and_scales_corners_back()`
test structure: construct a real `Camera` with a synthetic frame at a known
resolution, monkeypatch `olab_utils._resolveFaceDetector` to return a fake
detector (avoiding any dependency on the real bundled model or an actual
face for this specific test) whose `.detect(img)` records the received
image's shape and returns one fixed fractional-coordinate detection in the
*processing*-resolution frame, and wrap/spy on `Camera.getFrameCopy` to
record its call arguments while still delegating to the real
implementation. Run `cam.addFaceDetect(res_rows=<smaller>, res_cols=<smaller>, ...)`
for one real detection cycle (`time.sleep()`, then stop -- same pattern as
existing QR/ArUco tests), then assert: `getFrameCopy` was called with
`resOption=(res_cols, res_rows)`; the frame the fake detector actually
received has that resized shape; and the published
`cam.facedetect['default'].deque[0]` corners/landmarks are the fake
detection's coordinates scaled back to the *original* capture resolution,
matching hand-computed expected values (same "assert scaled-back values
exceed the processing-resolution bounds" check style as the QR test cited
above).

**Cosmetic note, not a defect**: `FaceDetectorYN.create()`/`.detect()` both
print `WARN: ... setPreferableTarget Targets are not supported by the new
graph engine for now` on this installed OpenCV 5.0.0, regardless of which
`backend_id`/`target_id` are passed (confirmed empirically for both the
default and explicit-CPU cases) -- detection still succeeds despite it.
Same class of benign native-OpenCV-logging noise already documented for
QR's ECI warning in `usage_guide.md`; not addressed by this task unless the
reviewer wants it suppressed the same way.

At minimum: construct `_FaceDetect`/call `addFaceDetect()` against the real
OpenCV 5.0.0 install and confirm it doesn't crash (mirrors the manual
`addAruco()` check already done for issue #9) -- this is the check that
would have caught the original `readNetFromCaffe()` break had it existed
before this session's manual testing found it.

---

## 3. Cross-repo follow-up (not part of this repo's diff)

- After this `olab_code` change is implemented and approved/merged:
  - `gh issue create` on `optimatorlab/ofm` describing the `dnn=` removal
    and the `camera_services.py:486-487` call site that needs updating
    (drop `dnn=args.get('dnn', 'caffe')`, note `model_name=` if the remote
    command protocol wants to expose model choice at all -- that's ofm's
    call, not this task's).
  - `gh issue create` on `optimatorlab/arbotix_private` describing the same
    for `client/client/scripts/client.py:312-317`.
- File now (doesn't depend on the fix landing first):
  - `gh issue create` on `optimatorlab/olab_code` tracking future
    evaluation/support for `face_detection_yunet_2026may.onnx`'s
    dynamic-input-shape variant.

## Affected Files (for the pairwork task)

- `packages/olab_utils/src/olab_utils/__init__.py` (`arucoDetectMarkers()`,
  `_resolveArucoDictAndParams()` simplification, docstrings)
- `packages/olab_utils/tests/test_aruco_dict_resolution.py` (update for
  dropped fallback branch)
- `packages/olab_camera/src/olab_camera/cv_features.py` (`_FaceDetect`
  rewrite)
- `packages/olab_camera/src/olab_camera/camera.py` (`addFaceDetect()`
  signature/docstring)
- `packages/olab_camera/src/olab_camera/cv2_dnn_models/` (delete 4 old
  files, add 2 new `.onnx` files)
- `packages/olab_camera/tests/` (new face-detection test file)
- `packages/olab_camera/docs/usage_guide.md` (Face Detection section)

## Non-goals

- No mixed OpenCV <4.7 support anywhere touched by this task (see scope
  decision above).
- No landmark-point decoration/drawing (bounding box only, as today).
- No fix for the pre-existing "camera resolution changes mid-run" gap
  (acknowledged, not solved, consistent with the rest of this codebase).
- No implementation of `face_detection_yunet_2026may.onnx` support --
  tracked as a future `olab_code` issue only.
- Cross-repo (`ofm`, `arbotix_private`) code changes are explicitly out of
  scope for this task -- issues only, filed after merge.
