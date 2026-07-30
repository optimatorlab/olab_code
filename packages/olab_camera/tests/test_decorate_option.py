"""Tests for issue #19: decorate=True/False on the 6 CV feature classes
(_Aruco, _Barcode, _QRCode, _FaceDetect, _ROI, _Ultralytics). For each, calls
the real Camera.addX() public method (not the _X class directly) and checks
whether the feature's decoration overlay got registered into
camObject.dec['active'] -- decorate=False must skip registration entirely
(decorationID stays None, no active entry), and decorate=True -- both
omitted (default) and passed explicitly -- must preserve current/legacy
behavior (decorationID set, exactly one matching active entry).

_Calibrate and _Timelapse are explicitly out of scope for issue #19 -- see
.pairwork/decorate-option-cv-features.md's Plan section for why.

_Barcode, _ROI, and _Ultralytics each depend on something not guaranteed
present on a clean core install / offline CI (native libzbar via pyzbar,
an OpenCV-contrib legacy tracker, and the optional `ultralytics` extra +
network access to download a model, respectively) -- `ultralytics` is
confirmed not installed in this repo's own dev venv. Each of those three
gets its dependency-construction boundary monkeypatched before the real
add*() call, so these tests run deterministically everywhere.
"""

import sys
import time
import types

import numpy as np
import pytest

import olab_utils
from olab_camera.camera import Camera


def _make_camera_with_frame(img):
    cam = Camera({'res_rows': img.shape[0], 'res_cols': img.shape[1], 'fps_target': 5})
    cam.frameDeque.append(img)
    cam.camOn = True
    return cam


def _stop_feature_and_drain_decorations(cam, featureDict, idName, wait=0.1):
    """Real cleanup for decoration-queue tests -- .stop() is what enqueues
    the dequeRemove entry (real decorationID, or harmlessly None), and
    cam.camOn=False is what actually terminates the daemon thread loop
    (the loop only checks camObject.camOn, not isThreadActive). Neither
    alone is sufficient; see .pairwork/decorate-option-cv-features.md's
    Plan section for the round-2 review finding this came from."""
    featureDict[idName].stop()
    cam.camOn = False
    time.sleep(wait)
    cam.manageDecorationsDeque()


def _active_ids(cam):
    return {d['decorationID'] for d in cam.dec['active']}


@pytest.fixture
def img():
    return np.zeros((480, 640, 3), dtype=np.uint8)


# decorate= omitted (default True) vs. passed explicitly -- both must
# preserve current/legacy behavior identically.
_TRUE_CASES = pytest.mark.parametrize('decorate_kwargs', [{}, {'decorate': True}], ids=['default', 'explicit-true'])


# ─── _Aruco ──────────────────────────────────────────────────────────────

def test_addAruco_decorate_false_skips_registration(img):
    cam = _make_camera_with_frame(img)
    cam.addAruco(idName='DICT_4X4_50', fps_target=10, decorate=False)
    cam.manageDecorationsDeque()

    feature = cam.aruco['DICT_4X4_50']
    assert feature.decorationID is None
    assert not cam.dec['dequeAdd']
    assert feature.decorationID not in _active_ids(cam)
    assert not cam.dec['active']

    _stop_feature_and_drain_decorations(cam, cam.aruco, 'DICT_4X4_50')
    assert not cam.dec['active']


@_TRUE_CASES
def test_addAruco_decorate_true_preserves_registration(img, decorate_kwargs):
    cam = _make_camera_with_frame(img)
    cam.addAruco(idName='DICT_4X4_50', fps_target=10, **decorate_kwargs)
    cam.manageDecorationsDeque()

    feature = cam.aruco['DICT_4X4_50']
    assert isinstance(feature.decorationID, int)
    assert feature.decorationID in _active_ids(cam)
    assert len(cam.dec['active']) == 1

    _stop_feature_and_drain_decorations(cam, cam.aruco, 'DICT_4X4_50')
    assert not cam.dec['active']


# ─── _Barcode ────────────────────────────────────────────────────────────

@pytest.fixture
def fake_pyzbar(monkeypatch):
    fake_pyzbar_mod = types.SimpleNamespace(decode=lambda img: [])
    fake_pkg = types.SimpleNamespace(pyzbar=fake_pyzbar_mod)
    monkeypatch.setitem(sys.modules, 'pyzbar', fake_pkg)
    monkeypatch.setitem(sys.modules, 'pyzbar.pyzbar', fake_pyzbar_mod)


def test_addBarcode_decorate_false_skips_registration(img, fake_pyzbar):
    cam = _make_camera_with_frame(img)
    cam.addBarcode(fps_target=10, decorate=False)
    cam.manageDecorationsDeque()

    feature = cam.barcode['default']
    assert feature.decorationID is None
    assert not cam.dec['dequeAdd']
    assert feature.decorationID not in _active_ids(cam)
    assert not cam.dec['active']

    _stop_feature_and_drain_decorations(cam, cam.barcode, 'default')
    assert not cam.dec['active']


@_TRUE_CASES
def test_addBarcode_decorate_true_preserves_registration(img, fake_pyzbar, decorate_kwargs):
    cam = _make_camera_with_frame(img)
    cam.addBarcode(fps_target=10, **decorate_kwargs)
    cam.manageDecorationsDeque()

    feature = cam.barcode['default']
    assert isinstance(feature.decorationID, int)
    assert feature.decorationID in _active_ids(cam)
    assert len(cam.dec['active']) == 1

    _stop_feature_and_drain_decorations(cam, cam.barcode, 'default')
    assert not cam.dec['active']


# ─── _QRCode (decoder='cv2' -- avoids the pyzbar decoder path entirely) ────

def test_addQR_decorate_false_skips_registration(img):
    cam = _make_camera_with_frame(img)
    cam.addQR(idName='default', decoder='cv2', fps_target=10, decorate=False)
    cam.manageDecorationsDeque()

    feature = cam.qr['default']
    assert feature.decorationID is None
    assert not cam.dec['dequeAdd']
    assert feature.decorationID not in _active_ids(cam)
    assert not cam.dec['active']

    _stop_feature_and_drain_decorations(cam, cam.qr, 'default')
    assert not cam.dec['active']


@_TRUE_CASES
def test_addQR_decorate_true_preserves_registration(img, decorate_kwargs):
    cam = _make_camera_with_frame(img)
    cam.addQR(idName='default', decoder='cv2', fps_target=10, **decorate_kwargs)
    cam.manageDecorationsDeque()

    feature = cam.qr['default']
    assert isinstance(feature.decorationID, int)
    assert feature.decorationID in _active_ids(cam)
    assert len(cam.dec['active']) == 1

    _stop_feature_and_drain_decorations(cam, cam.qr, 'default')
    assert not cam.dec['active']


# ─── _FaceDetect ────────────────────────────────────────────────────────

@pytest.fixture
def fake_face_detector(monkeypatch):
    class _FakeDetector:
        def detect(self, frame):
            return (0, None)
    monkeypatch.setattr(olab_utils, '_resolveFaceDetector', lambda *a, **k: _FakeDetector())


def test_addFaceDetect_decorate_false_skips_registration(img, fake_face_detector):
    cam = _make_camera_with_frame(img)
    cam.addFaceDetect(fps_target=10, decorate=False)
    cam.manageDecorationsDeque()

    feature = cam.facedetect['default']
    assert feature.decorationID is None
    assert not cam.dec['dequeAdd']
    assert feature.decorationID not in _active_ids(cam)
    assert not cam.dec['active']

    _stop_feature_and_drain_decorations(cam, cam.facedetect, 'default')
    assert not cam.dec['active']


@_TRUE_CASES
def test_addFaceDetect_decorate_true_preserves_registration(img, fake_face_detector, decorate_kwargs):
    cam = _make_camera_with_frame(img)
    cam.addFaceDetect(fps_target=10, **decorate_kwargs)
    cam.manageDecorationsDeque()

    feature = cam.facedetect['default']
    assert isinstance(feature.decorationID, int)
    assert feature.decorationID in _active_ids(cam)
    assert len(cam.dec['active']) == 1

    _stop_feature_and_drain_decorations(cam, cam.facedetect, 'default')
    assert not cam.dec['active']


# ─── _ROI ───────────────────────────────────────────────────────────────

@pytest.fixture
def fake_roi_tracker(monkeypatch):
    class _FakeTracker:
        def init(self, frame, bb):
            return True

    monkeypatch.setitem(olab_utils.OPENCV_OBJECT_TRACKERS, 'faketracker', _FakeTracker)


def test_addROI_decorate_false_skips_registration(img, fake_roi_tracker):
    cam = _make_camera_with_frame(img)
    cam.addROI(roiTrackerName='faketracker', roiBB=(10, 10, 20, 20), fps_target=10, decorate=False)
    cam.manageDecorationsDeque()

    feature = cam.roi['default']
    assert feature.decorationID is None
    assert not cam.dec['dequeAdd']
    assert feature.decorationID not in _active_ids(cam)
    assert not cam.dec['active']

    _stop_feature_and_drain_decorations(cam, cam.roi, 'default')
    assert not cam.dec['active']


@_TRUE_CASES
def test_addROI_decorate_true_preserves_registration(img, fake_roi_tracker, decorate_kwargs):
    cam = _make_camera_with_frame(img)
    cam.addROI(roiTrackerName='faketracker', roiBB=(10, 10, 20, 20), fps_target=10, **decorate_kwargs)
    cam.manageDecorationsDeque()

    feature = cam.roi['default']
    assert isinstance(feature.decorationID, int)
    assert feature.decorationID in _active_ids(cam)
    assert len(cam.dec['active']) == 1

    _stop_feature_and_drain_decorations(cam, cam.roi, 'default')
    assert not cam.dec['active']


# ─── _Ultralytics ───────────────────────────────────────────────────────

@pytest.fixture
def fake_ultralytics(monkeypatch):
    class _FakeYOLO:
        def __init__(self, model_name):
            self.model_name = model_name

        def __call__(self, *args, **kwargs):
            return []

    fake_mod = types.SimpleNamespace(YOLO=_FakeYOLO)
    monkeypatch.setitem(sys.modules, 'ultralytics', fake_mod)


def test_addUltralytics_decorate_false_skips_registration(img, fake_ultralytics):
    cam = _make_camera_with_frame(img)
    cam.addUltralytics(idName='detect', model_name='fake.pt', fps_target=10, decorate=False)
    cam.manageDecorationsDeque()

    feature = cam.ultralytics['detect']
    assert feature.decorationID is None
    assert not cam.dec['dequeAdd']
    assert feature.decorationID not in _active_ids(cam)
    assert not cam.dec['active']

    _stop_feature_and_drain_decorations(cam, cam.ultralytics, 'detect')
    assert not cam.dec['active']


@_TRUE_CASES
def test_addUltralytics_decorate_true_preserves_registration(img, fake_ultralytics, decorate_kwargs):
    cam = _make_camera_with_frame(img)
    cam.addUltralytics(idName='detect', model_name='fake.pt', fps_target=10, **decorate_kwargs)
    cam.manageDecorationsDeque()

    feature = cam.ultralytics['detect']
    assert isinstance(feature.decorationID, int)
    assert feature.decorationID in _active_ids(cam)
    assert len(cam.dec['active']) == 1

    _stop_feature_and_drain_decorations(cam, cam.ultralytics, 'detect')
    assert not cam.dec['active']
