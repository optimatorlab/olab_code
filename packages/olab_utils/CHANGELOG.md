# Changelog

All notable changes to `olab-utils` are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-10-07

Replaced the `opencv-contrib-python` dependency with plain `opencv-python`
(closes #70): installing `olab-camera[yolo]`/`[tracking]`/`[rfdetr]`/`[all]`
alongside `olab-utils` no longer installs two conflicting `cv2` distributions
that silently clobber each other. ArUco, QR, and face-detection code paths
are unaffected -- none of them actually needed contrib.

**Breaking (0.x):** `OPENCV_OBJECT_TRACKERS` no longer unconditionally
contains all 7 classic OpenCV object trackers; it now only contains the
trackers the installed `cv2` actually provides. On a clean `opencv-python`
install that's just `{'mil': ...}`; installing `opencv-contrib-python`
yourself still gets you all 7 (`csrt`, `kcf`, `boosting`, `mil`, `tld`,
`medianflow`, `mosse`), subject to the two-distribution conflict this release
exists to avoid -- uninstall both `opencv-python` and `opencv-contrib-python`
first, then install only `opencv-contrib-python`, if you want them.
Previously, importing `olab_utils` under plain `opencv-python` crashed
outright (`AttributeError` building this table at import time); it now
imports cleanly and simply has a smaller table.

**Upgrading from a 0.1.0 install:** 0.1.0 depended on `opencv-contrib-python`.
Upgrading in place (or re-running an editable install) can leave both
`opencv-python` and `opencv-contrib-python` installed side by side, sharing
the same `cv2/` files -- uninstalling either one afterward then deletes `cv2`
entirely. For a clean switch:
```
pip uninstall -y opencv-python opencv-contrib-python
pip install "opencv-python>=4.10.0"
```

**Release ordering:** `olab-camera` depends on `olab-utils>=0.2.0` as of its
next change; that floor is only installable from real PyPI once this
`olab-utils` 0.2.0 is actually released (tagged/published), per
`CONTRIBUTING.md`'s release-ordering rule -- release this one first.

## [0.1.0] - 2026-10-06

First PyPI release. Shared utility helpers for lab robotics projects:
`Logger`, ArUco detection/pose/drawing helpers, barcode/QR and
face-detection decoration, image-stitching and file-listing utilities,
frame-drawing helpers, unit conversions, and small networking helpers.
