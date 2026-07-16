# olab_utils

Shared utility helpers for lab robotics projects: a `Logger` class, ArUco
detection/pose/drawing helpers, barcode/QR and face-detection decoration,
image-stitching and file-listing utilities, simple frame-drawing helpers
(circle/line/text/arrow), unit conversions, and small networking helpers
(`checkPort`, `getIP`, `findOpenPort`).

Migrated from `~/Projects/ub_code/ub_utils` (a flat, non-`src/`-layout
single-file module — `ub_code` never had automated tests) per
[`docs/plans/olab_packages_reorg_plan.md`](../../docs/plans/olab_packages_reorg_plan.md),
Migration sequence step 4. [`olab_camera`](../olab_camera/) depends on this
package (`import olab_utils`); it is not `olab_camera`-specific and can be
used standalone.

## Installing

Normal installation (no `olab_code` checkout required):

```bash
pip install "olab-utils @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_utils"
```

Once release wheels exist, prefer pinning the release's exact URL and
SHA-256 hash instead of a git reference.

**Local development**, against an `olab_code` checkout:

```bash
pip install -e "packages/olab_utils"
```

## Usage

```python
import olab_utils

logger = olab_utils.Logger()
port = olab_utils.findOpenPort(8000, options=range(8000, 8040))
meters = olab_utils.inches2meters(4.25)
```

See the module's own docstrings for the full function/class reference —
most functions are small, self-documenting helpers grouped by area (ArUco,
drawing/decoration, image files, networking, unit conversion).
