# Changelog

All notable changes to `olab-camera` are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- `Camera.addFaceDetect()`, `addBarcode()`, `addCalibrate()`, `addROI()`,
  `addTimelapse()` and `addUltralytics()` now refuse a second call for the
  same `idName` while that feature's thread is still active (logging an
  error, like `addAruco()`/`addQR()`), instead of silently replacing the
  entry. Previously the first thread (and its overlay, where the feature
  has one) kept running with nothing pointing to them, so `stop()` could
  never remove them -- e.g. pressing a "set face detection" button twice
  left an undeletable box on the stream, and a second `addTimelapse()`
  during its start delay left the first one taking photos forever.
  Calling `add*()` again after `stop()`, after the feature finished by
  itself (calibration done/timeout, timelapse time limit), or after a
  failed construction (e.g. `addROI()` with a bad `roiBB`) is accepted and
  replaces the old entry; a failed construction's stray overlay is removed.
- A feature's `isThreadActive` is now `True` as soon as `start()` launches
  its thread (previously only once the thread had run its first line, and
  for `addTimelapse()` only after `delayStartSec`). This applies to
  `addAruco()`/`addQR()` too. As a result a `stop()` issued right after
  `add*()`, during a timelapse's start delay, or during a calibration's
  wait between images now takes effect instead of being overwritten.

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
