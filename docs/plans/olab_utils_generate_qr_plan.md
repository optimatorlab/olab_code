# olab_utils: generateQR() -- printable QR tag generation

## Goal

Add a function to `olab_utils` that generates printable QR-code tag images,
for use as physical fiducials with `olab_camera`'s existing QR detection/pose
pipeline (`_QRCode` in `cv_features.py`, `olab_utils.arucoFindPose()` /
`arucoFindPoseGlobal()` / `arucoFindCameraPoseGlobal()`, per
`docs/plans/olab_camera_qr_tag_support_plan.md`, which is already implemented
per current git status).

## Context from codebase exploration

- `olab_utils` is a single flat `src/olab_utils/__init__.py` (~1200 lines,
  functions like `arucoFindPose`, `decorateQR`, `drawText`, `inches2meters`/
  `meters2inches`) with no submodules. No QR/ArUco *generation* helper exists
  today anywhere in the repo.
- The only existing QR-image-generation code is test-only:
  `test_qr_and_pose.py::_synthetic_qr_image()`, which hand-rolls a QR bitmap
  via `cv2.QRCodeEncoder` (a transitive dependency via
  `opencv-contrib-python`, already installed) specifically so the detector
  tests don't depend on the same generator they're validating decoding
  against. This stays untouched and independent -- `generateQR()` is a
  separate, real implementation using the `qrcode` PyPI package, not a
  refactor of that test helper.
- `_QRCode`'s `decoder='cv2'` path (recommended for pose) relies on
  `cv2.QRCodeDetector`'s corner order being anchored to the QR's own
  finder-pattern frame -- verified in `test_qr_and_pose.py`. Those corners
  bound the QR symbol itself, not its quiet zone -- this is why
  `tag_size_inches` (below) must mean the symbol's own size, matching how
  `usage_guide.md`'s existing `TAG_SIZE_INCHES` example already builds
  `objectPoints` for `arucoFindPose()`.
- `olab_utils.inches2meters()`/`meters2inches()` and the `TAG_SIZE_INCHES`
  convention in `usage_guide.md` establish that physical tag sizes are
  specified in inches elsewhere in this codebase; `generateQR()` follows the
  same convention (`tag_size_inches` + `dpi`, rather than raw pixel/module
  sizing).
- `olab_utils.drawText()` already exists (`cv2.putText` wrapper) and is
  reused here for the optional label, rather than adding a second
  text-drawing helper.

## Decisions

### Dependency
- Add `qrcode` (PyPI) as a new dependency of `olab_utils` (`pyproject.toml`).
  `qrcode`'s default image factory requires Pillow -- add that too if not
  already pulled in transitively.
- This is a deliberate divergence from the test suite's `cv2.QRCodeEncoder`
  path: `qrcode` gives explicit control over error-correction level,
  box_size/border (module-level sizing), and fill/back colors that
  `cv2.QRCodeEncoder` doesn't expose as cleanly.

### Signature
```python
def generateQR(payload, tag_size_inches, dpi=300, ecc='H', border=4,
               fill_color='black', back_color='white', label=None,
               logo=None, logo_scale=0.2, outputFile=None):
    '''Returns a BGR numpy image (uint8) containing the generated QR tag.'''
```
- `payload` (str): the data to encode. String-only -- matches every existing
  QR payload use in this codebase (`_QRCode`/`decorateQR`/`usage_guide.md`
  all treat payload as text); no bytes support.
- `tag_size_inches` (float): physical size of **the QR symbol itself**,
  excluding its quiet zone -- i.e. exactly the dimension a caller should pass
  into `arucoFindPose()`'s `objectPoints` for this printed tag, with no
  additional adjustment for the quiet zone.
- `dpi` (int, default 300): used with `tag_size_inches` to compute the
  per-module pixel size (`box_size`) needed so the QR symbol prints at
  exactly `tag_size_inches` at that DPI.
- `ecc` (str, default `'H'`, one of `'L'/'M'/'Q'/'H'`): QR error-correction
  level. Defaults to `'H'` (~30% recoverable) for resilience of a printed tag
  viewed at an angle/distance/partial occlusion; exposed so a caller can
  trade this off against payload capacity/code density.
- `border` (int, default 4): quiet-zone width in modules, `qrcode`'s own
  convention/default (the QR standard's minimum recommended quiet zone).
- `fill_color`/`back_color` (default `'black'`/`'white'`): passed through to
  `qrcode`'s image generation. Configurable (unlike ArUco generation
  elsewhere, which has none) -- caller is responsible for choosing a
  combination with enough contrast to scan reliably.
- `label` (str or None, default None): if given, this text is drawn (via
  `olab_utils.drawText`) on additional canvas appended **below** the sized
  QR square -- i.e. outside/after `tag_size_inches` and the quiet zone, not
  inside it. Total output image height exceeds the `tag_size_inches`-derived
  square whenever a label is given; width matches.
- `outputFile` (str/path or None, default None): if given, the result is
  also written to disk via `cv2.imwrite()`. The function always returns the
  image regardless.
- `logo` (path/str, numpy array, or None, default None): if given, an image
  composited centered over the finished QR symbol before the label step. See
  "Logo embedding" below.
- `logo_scale` (float, default 0.2): logo size as a fraction of the QR
  symbol's own width/height (e.g. `0.2` = 20%). Only meaningful when `logo`
  is given.
- Return: BGR `uint8` numpy array (matches `cv2`/`olab_utils` image
  conventions used everywhere else, e.g. `decorateQR`, `arucoDrawDetections`).

### Logo embedding
- Rationale: with `ecc='H'` (~30% recoverable), a centered logo covering up
  to roughly that fraction of the QR's area still reliably scans -- a
  standard technique for QR codes with branding/identifying imagery baked
  in.
- `logo_scale` is capped internally (clamped, not silently ignored -- raise
  if the caller passes something above the cap, e.g. `> 0.3`) since larger
  overlays risk breaking decodability regardless of `ecc`. The cap is
  checked against `logo_scale` directly, independent of `ecc`, since
  `ecc` values below `'H'` have even less recovery budget -- a caller using
  a lower ECC with a nontrivial `logo_scale` is relying entirely on the
  post-composite verification step (below) to catch a bad combination.
- `logo` accepts either a file path (str/Path, loaded via `cv2.imread`,
  including alpha channel if present) or an in-memory numpy image (BGR or
  BGRA) -- matches how images are already passed around this codebase
  (`cv2` arrays), rather than requiring PIL objects.
- The logo is resized to `logo_scale * (QR symbol pixel size)` (preserving
  aspect ratio, fit within that box) and composited centered on the QR,
  with a solid `back_color` backing square behind it (sized to the logo's
  bounding box, plus a small fixed margin) so the logo has clean contrast
  against the surrounding modules regardless of the logo's own background
  -- an alpha channel in `logo`, if present, is respected when compositing
  onto that backing square.
- After compositing, the result is decoded via `cv2.QRCodeDetector` and
  checked against `payload`; if it doesn't round-trip, raise immediately
  (before any `label`/`outputFile` step) rather than returning or saving an
  unscannable tag. This verification only runs when `logo` is given --
  a logo-free tag is not re-verified (consistent with the "let exceptions
  propagate, don't add unneeded validation" approach elsewhere in this
  plan).

### Error handling
- No try/except wrapper -- matches other standalone (non-class,
  non-`camObject`) helpers like `arucoFindPose()`/`inches2meters()`, none of
  which catch exceptions (there's no `camObject.logger` to report through
  outside a class context). Invalid input (e.g. payload too long for the
  chosen version/ECC combination) raises naturally from `qrcode`.

### Placement
- Added directly to `src/olab_utils/__init__.py`, alongside the other
  QR/ArUco-adjacent helpers (`decorateQR`, `drawText`) -- no new submodule,
  consistent with the package's current flat-file layout.

### Sizing implementation detail
- Build the `qrcode.QRCode(error_correction=..., border=border)` object with
  `fit=True` first to determine the required version/module count for
  `payload`, then compute `box_size = round(dpi * tag_size_inches /
  module_count)` so the rendered QR symbol (excluding quiet zone) lands as
  close as possible to `tag_size_inches` at `dpi`, before generating the
  final image.

## Non-goals
- No batch/sheet generation (multiple tags laid out on one printable page)
  -- single tag per call only; callers loop themselves if they need several.
- No refactor of `test_qr_and_pose.py`'s `_synthetic_qr_image()` to use this
  new function -- that test helper stays independent by design.
- No bytes/non-string payload support.
- No PDF or other print-ready output format -- image only (numpy array, or
  whatever `cv2.imwrite()` can write via `outputFile`'s extension).
- No automatic `logo_scale`/`ecc` auto-tuning (e.g. searching for the largest
  logo that still decodes) -- the cap plus post-composite verification is
  the only safety net; a caller who wants a bigger logo picks a larger
  `ecc`/smaller `logo_scale` themselves and re-runs.

## Implementation outline
1. `pyproject.toml` (olab_utils): add `qrcode` (and `Pillow` if needed) to
   `dependencies`.
2. `src/olab_utils/__init__.py`: add `generateQR()` per the signature above,
   including the logo compositing + post-composite verification step.
3. Tests (new, in `packages/olab_utils/tests/`): round-trip test generating a
   tag with `generateQR()` and decoding it back with `cv2.QRCodeDetector`
   (independent of the existing `_synthetic_qr_image` test helper); a test
   asserting the rendered QR square's pixel dimensions match
   `tag_size_inches * dpi` within rounding; a test for the optional `label`
   appending extra canvas without shrinking/altering the QR square itself;
   a test with a `logo` at a safe `logo_scale` that still round-trips; a
   test that an oversized `logo_scale` (above the cap) raises; a test with a
   payload/ECC/logo_scale combination deliberately chosen to break
   decodability, asserting `generateQR()` raises rather than returning a
   bad tag.
4. Optionally: add a short "Generate a QR tag" section to
   `packages/olab_camera/docs/usage_guide.md`, mirroring the existing
   `TAG_SIZE_INCHES` example, showing `generateQR()` feeding directly into
   the existing pose-estimation example.
