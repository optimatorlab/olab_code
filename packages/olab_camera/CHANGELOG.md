# Changelog

All notable changes to `olab-camera` are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-10-06

First PyPI release. Camera capture, local recording, and network
streaming (MJPEG/WebSocket/WebRTC) for lab robotics projects, plus ArUco,
barcode/QR, face-detection, and YOLO computer-vision helpers.

Depends on `opencv-python` (not `opencv-contrib-python`) and
`olab-utils>=0.2.0`, closing #70: the `[yolo]`/`[tracking]`/`[rfdetr]`/`[all]`
extras no longer pull in a second, conflicting `cv2` distribution.
`Camera.addROI()`'s classic object trackers other than `'MIL'` (any case)
still require `opencv-contrib-python` to be installed separately -- a
request for an unavailable tracker now logs one clear error naming the
missing dependency instead of crashing or silently breaking later frame
decorations.

`pyzbar` is an opt-in `barcode` extra, not a base dependency: it also needs
the ZBar system library (`libzbar0` on Linux/Raspberry Pi, `brew install
zbar` on macOS), which pip can't install. `addBarcode()` and
`addQR(decoder='pyzbar')` need `pip install "olab-camera[barcode]"` plus that
library; without them they log one clear error and start nothing (#72).
`addQR()`'s default `decoder='cv2'` needs neither.
