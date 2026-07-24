"""Tests for olab_utils._resolveFaceDetector()/detectFaces() -- the
cv2.FaceDetectorYN (YuNet)-based replacement for the removed
cv2.dnn.readNetFromCaffe()/readNetFromTensorflow() face detection path
(issue #9, OpenCV 5 support). These tests are deterministic and don't need
the real bundled ONNX model -- detectFaces()'s output-conversion logic is a
pure function, tested here with fake detector objects."""

import types

import numpy as np
import pytest

import olab_utils


class _FakeYuNetDetector:
    '''A fake cv2.FaceDetectorYN, returning a fixed (or overridden) faces array.'''
    def __init__(self, faces):
        self.faces = faces
        self.detectCalls = []

    def detect(self, img):
        self.detectCalls.append(img)
        return (1, self.faces)


def _one_face_row(x, y, w, h, score=0.9):
    # [x, y, w, h, x_re, y_re, x_le, y_le, x_nt, y_nt, x_rmc, y_rmc, x_lmc, y_lmc, score]
    return [x, y, w, h,
            x + 0.3 * w, y + 0.3 * h,   # right eye
            x + 0.7 * w, y + 0.3 * h,   # left eye
            x + 0.5 * w, y + 0.5 * h,   # nose tip
            x + 0.35 * w, y + 0.75 * h,  # right mouth corner
            x + 0.65 * w, y + 0.75 * h,  # left mouth corner
            score]


def test_detectFaces_no_detections_returns_empty_lists():
    detector = _FakeYuNetDetector(None)
    (confidence, corners, landmarks) = olab_utils.detectFaces(np.zeros((10, 10)), detector)
    assert (confidence, corners, landmarks) == ([], [], [])


def test_detectFaces_no_scaling_still_returns_ints():
    faces = np.array([_one_face_row(10.4, 20.6, 30.5, 40.5)])
    detector = _FakeYuNetDetector(faces)

    (confidence, corners, landmarks) = olab_utils.detectFaces(
        np.zeros((100, 100)), detector, img_x_y=(100, 100), orig_x_y=(100, 100))

    assert confidence == [pytest.approx(0.9)]
    assert len(corners) == 1
    for pt in corners[0]:
        assert isinstance(pt[0], int) and isinstance(pt[1], int)
    assert len(landmarks) == 1
    assert len(landmarks[0]) == 5
    for pt in landmarks[0]:
        assert isinstance(pt[0], int) and isinstance(pt[1], int)


def test_detectFaces_scales_and_rounds_correctly():
    # A single face at (10, 20, w=30, h=40) in a 100x100 processing frame,
    # scaled back to a 200x100 original capture frame (2x width, 1x height).
    faces = np.array([_one_face_row(10.0, 20.0, 30.0, 40.0)])
    detector = _FakeYuNetDetector(faces)

    (confidence, corners, landmarks) = olab_utils.detectFaces(
        np.zeros((100, 100)), detector, img_x_y=(100, 100), orig_x_y=(200, 100))

    # bbox: (10,20) -> (20,20); (40,60) -> (80,60)
    assert corners == [[(20, 20), (80, 60)]]

    # Hand-compute expected landmark scaling for cross-check.
    xscale, yscale = 2.0, 1.0
    expected = [
        (int(round((10 + 0.3 * 30) * xscale)), int(round((20 + 0.3 * 40) * yscale))),
        (int(round((10 + 0.7 * 30) * xscale)), int(round((20 + 0.3 * 40) * yscale))),
        (int(round((10 + 0.5 * 30) * xscale)), int(round((20 + 0.5 * 40) * yscale))),
        (int(round((10 + 0.35 * 30) * xscale)), int(round((20 + 0.75 * 40) * yscale))),
        (int(round((10 + 0.65 * 30) * xscale)), int(round((20 + 0.75 * 40) * yscale))),
    ]
    assert landmarks == [expected]


def test_resolve_face_detector_passes_expected_arguments_to_create():
    calls = []

    class _FakeFaceDetectorYN:
        @staticmethod
        def create(*args, **kwargs):
            calls.append((args, kwargs))
            return 'fake-detector'

    cv2_module = types.SimpleNamespace(FaceDetectorYN=_FakeFaceDetectorYN)

    result = olab_utils._resolveFaceDetector(
        '/some/path/model.onnx', (640, 480), 0.7,
        backend_id=0, target_id=0, cv2_module=cv2_module)

    assert result == 'fake-detector'
    assert len(calls) == 1
    (args, kwargs) = calls[0]
    assert args == ('/some/path/model.onnx', '', (640, 480))
    assert kwargs['score_threshold'] == 0.7
    assert kwargs['backend_id'] == 0
    assert kwargs['target_id'] == 0


def test_decorateFaceDetect_consumes_detectFaces_output_without_error():
    faces = np.array([_one_face_row(10.0, 20.0, 30.0, 40.0)])
    detector = _FakeYuNetDetector(faces)
    (confidence, corners, landmarks) = olab_utils.detectFaces(np.zeros((100, 100)), detector)

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    olab_utils.decorateFaceDetect(img, confidence, corners, color=(0, 255, 255))
    # No exception raised == success; also confirm it actually drew something.
    assert img.sum() > 0
