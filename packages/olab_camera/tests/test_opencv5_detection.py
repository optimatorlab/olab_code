"""Camera-level tests for the two OpenCV-5 fixes completing issue #9:
_Aruco's cached ArUco detector, and _FaceDetect's YuNet (cv2.FaceDetectorYN)
rewrite -- fail-fast construction and the res_rows/res_cols processing-
resolution fix, exercised through a real _thread_FaceDetect() cycle (not
just the pure-function-level tests in packages/olab_utils/tests/)."""

import time

import numpy as np
import pytest

import olab_utils
from olab_camera.camera import Camera


def _make_camera_with_frame(img):
    cam = Camera({'res_rows': img.shape[0], 'res_cols': img.shape[1], 'fps_target': 5})
    cam.frameDeque.append(img)
    cam.camOn = True
    return cam


def _stop_feature_thread(cam, featureDict, idName, wait=0.3):
    featureDict[idName].isThreadActive = False
    cam.camOn = False
    time.sleep(wait)


# ─── _Aruco: cached ArucoDetector ───────────────────────────────────────────

def test_addAruco_builds_detector_once_and_reuses_it_across_cycles():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cam = _make_camera_with_frame(img)

    cam.addAruco(idName='DICT_4X4_50', fps_target=10)
    time.sleep(1.0)
    detector_after_first_cycles = cam.aruco['DICT_4X4_50'].arucoDetector
    time.sleep(0.5)
    detector_after_more_cycles = cam.aruco['DICT_4X4_50'].arucoDetector
    _stop_feature_thread(cam, cam.aruco, 'DICT_4X4_50')

    # Same object identity -- never rebuilt across detection cycles.
    assert detector_after_first_cycles is detector_after_more_cycles


# ─── _FaceDetect: fail-fast construction ────────────────────────────────────

def test_addFaceDetect_resolver_failure_leaves_no_entry_and_does_not_raise(monkeypatch):
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cam = _make_camera_with_frame(img)

    def _raise(*args, **kwargs):
        raise RuntimeError('simulated model-load failure')

    monkeypatch.setattr(olab_utils, '_resolveFaceDetector', _raise)

    cam.addFaceDetect(fps_target=5)   # must not raise
    assert 'default' not in cam.facedetect
    cam.camOn = False


# ─── _FaceDetect: real worker-cycle test (res_rows/res_cols + scaling) ──────

def test_addFaceDetect_processes_at_a_lower_resolution_and_scales_result_back(monkeypatch):
    orig_h, orig_w = 480, 640
    img = np.zeros((orig_h, orig_w, 3), dtype=np.uint8)
    cam = _make_camera_with_frame(img)

    proc_w, proc_h = orig_w // 2, orig_h // 2

    getFrameCopy_calls = []
    real_getFrameCopy = cam.getFrameCopy

    def _spy_getFrameCopy(*args, **kwargs):
        getFrameCopy_calls.append(kwargs)
        return real_getFrameCopy(*args, **kwargs)

    monkeypatch.setattr(cam, 'getFrameCopy', _spy_getFrameCopy)

    class _FakeDetector:
        def __init__(self):
            self.detectedShapes = []

        def detect(self, frame):
            self.detectedShapes.append(frame.shape)
            # One fixed detection, in the *processing*-resolution frame:
            # bbox (10, 10, w=20, h=20); landmarks all at the bbox center
            # for simplicity (only the scaling math is under test here).
            cx, cy = 20.0, 20.0
            face = [10.0, 10.0, 20.0, 20.0,
                    cx, cy, cx, cy, cx, cy, cx, cy, cx, cy,
                    0.95]
            return (1, np.array([face]))

    fake_detector = _FakeDetector()
    monkeypatch.setattr(olab_utils, '_resolveFaceDetector', lambda *a, **k: fake_detector)

    cam.addFaceDetect(fps_target=10, res_rows=proc_h, res_cols=proc_w)
    time.sleep(1.5)
    d = cam.facedetect['default'].deque[0]
    _stop_feature_thread(cam, cam.facedetect, 'default')

    # getFrameCopy() must have been called with resOption=(proc_w, proc_h).
    assert any(call.get('resOption') == (proc_w, proc_h) for call in getFrameCopy_calls)

    # The fake detector must have received a frame actually resized to the
    # processing resolution, not the original capture resolution.
    assert fake_detector.detectedShapes
    assert fake_detector.detectedShapes[0][:2] == (proc_h, proc_w)

    # Published corners/landmarks must be scaled back to the *original*
    # capture resolution: xscale = yscale = 2.0 here (proc is exactly half).
    assert d['confidence'] == [pytest.approx(0.95)]
    assert d['corners'] == [[(20, 20), (60, 60)]]
    assert d['landmarks'] == [[(40, 40)] * 5]
