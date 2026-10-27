"""A second add*() call for the same idName while the first thread is still
active must be refused (logged, nothing replaced) -- otherwise the first
feature's thread and overlay are orphaned, unreachable by stop().

These tests use the REAL feature classes through the shipped Camera.add*()
methods, stubbed only at the dependency boundary (the face-detector model,
the pyzbar / ultralytics imports, takePhotoLocal). Tests that need the
"thread has not run its first line yet" window hold it open
deterministically by replacing threading.Thread.start.
"""

import sys
import threading
import time
import types

import numpy as np
import pytest

import olab_utils
import olab_camera.camera as camera_module
from olab_camera.camera import Camera


class _RecordingLogger:
    def __init__(self):
        self.calls = []

    def log(self, msgtext, severity=None, **kwargs):
        self.calls.append((msgtext, severity))

    def errors(self):
        return [m for m, sev in self.calls if sev == olab_utils.SEVERITY_ERROR]


def _wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


_STORES = ('aruco', 'qr', 'barcode', 'calibrate', 'facedetect', 'roi', 'timelapse', 'ultralytics')


@pytest.fixture
def cam(monkeypatch):
    class _FakeDetector:
        def detect(self, frame):
            return (0, None)
    monkeypatch.setattr(olab_utils, '_resolveFaceDetector', lambda *a, **k: _FakeDetector())
    monkeypatch.setattr(camera_module, '_pyzbarUnavailableReason', lambda: None)

    pyzbar_module = types.ModuleType('pyzbar')
    pyzbar_module.pyzbar = types.SimpleNamespace(decode=lambda img: [])
    monkeypatch.setitem(sys.modules, 'pyzbar', pyzbar_module)
    ultralytics_module = types.ModuleType('ultralytics')
    ultralytics_module.YOLO = lambda model_name: object()
    monkeypatch.setitem(sys.modules, 'ultralytics', ultralytics_module)

    c = Camera({'res_rows': 480, 'res_cols': 640, 'fps_target': 5})
    c.frameDeque.append(np.zeros((480, 640, 3), dtype=np.uint8))
    c.camOn = True
    c.logger = _RecordingLogger()
    c.takePhotoLocal = lambda **kwargs: None
    yield c

    # Runs even when an assertion failed: end every feature thread.
    c.camOn = False
    for store in _STORES:
        for feature in list(getattr(c, store).values()):
            feature.stop()


def _freeze_threads(monkeypatch):
    """Threads are created but never run, so the window between add*()
    returning and the worker's first line stays open."""
    monkeypatch.setattr(threading.Thread, 'start', lambda self: None)


# (add method, store on Camera, kwargs, idName)
_CASES = [
    ('addAruco',       'aruco',       {'idName': 'DICT_4X4_50'}, 'DICT_4X4_50'),
    ('addQR',          'qr',          {'idName': 'q'}, 'q'),
    ('addBarcode',     'barcode',     {}, 'default'),
    ('addCalibrate',   'calibrate',   {}, 'default'),
    ('addFaceDetect',  'facedetect',  {}, 'default'),
    ('addROI',         'roi',         {'roiTrackerName': 'mil', 'roiBB': (100, 100, 50, 50)}, 'default'),
    ('addTimelapse',   'timelapse',   {'outputDir': 'unused', 'delayStartSec': 30}, 'default'),
    ('addUltralytics', 'ultralytics', {'idName': 'detect', 'model_name': 'x.pt'}, 'detect'),
]


@pytest.mark.parametrize('method,store,kwargs,idName', _CASES, ids=[c[0] for c in _CASES])
def test_second_add_before_the_thread_runs_is_refused(cam, monkeypatch, method, store, kwargs, idName):
    _freeze_threads(monkeypatch)
    getattr(cam, method)(**kwargs)
    first = getattr(cam, store)[idName]
    assert first.isThreadActive          # set by start(), not by the (never-run) thread
    overlays = len(cam.dec['dequeAdd'])

    getattr(cam, method)(**kwargs)

    assert getattr(cam, store)[idName] is first
    assert len(cam.dec['dequeAdd']) == overlays
    assert any('already running' in m for m in cam.logger.errors())


def test_addFaceDetect_second_call_keeps_first_and_stop_removes_its_overlay(cam):
    cam.addFaceDetect(fps_target=10)
    first = cam.facedetect['default']

    cam.addFaceDetect(fps_target=10)

    assert cam.facedetect['default'] is first
    assert any('already running' in m for m in cam.logger.errors())
    cam.manageDecorationsDeque()
    assert len(cam.dec['active']) == 1

    first.stop()
    cam.manageDecorationsDeque()
    assert not cam.dec['active']


def test_stop_before_the_thread_runs_sticks_and_readd_is_accepted(cam, monkeypatch):
    started = []
    real_start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, 'start', lambda self: started.append(self))
    cam.addFaceDetect(fps_target=10)
    first = cam.facedetect['default']
    first.stop()                          # before the worker's first line
    assert not first.isThreadActive

    monkeypatch.setattr(threading.Thread, 'start', real_start)
    started[0].start()
    # The thread must wind down on its own (it used to re-set the flag and loop forever).
    assert _wait_until(lambda: not started[0].is_alive(), timeout=5)
    assert not first.isThreadActive

    cam.addFaceDetect(fps_target=10)
    assert cam.facedetect['default'] is not first
    assert not any('already running' in m for m in cam.logger.errors())


def test_addROI_retry_after_a_failed_construction_replaces_it_and_clears_its_overlay(cam, monkeypatch):
    _freeze_threads(monkeypatch)
    cam.addROI(roiTrackerName='mil', roiBB='bad')
    broken = cam.roi['default']
    cam.logger.calls.clear()

    cam.addROI(roiTrackerName='mil', roiBB=(100, 100, 50, 50))

    new = cam.roi['default']
    assert new is not broken
    assert not cam.logger.errors()
    cam.manageDecorationsDeque()
    assert [d['function'] for d in cam.dec['active']] == [new._decorate]


def test_addUltralytics_retry_after_a_failed_model_load_replaces_it(cam, monkeypatch):
    _freeze_threads(monkeypatch)
    calls = []

    def flaky_yolo(model_name):
        calls.append(model_name)
        if len(calls) == 1:
            raise RuntimeError('model load failed')
        return object()
    monkeypatch.setattr(sys.modules['ultralytics'], 'YOLO', flaky_yolo)

    cam.addUltralytics(idName='detect', model_name='x.pt')
    broken = cam.ultralytics['detect']
    cam.logger.calls.clear()

    cam.addUltralytics(idName='detect', model_name='x.pt')

    assert cam.ultralytics['detect'] is not broken
    assert not cam.logger.errors()


def test_addCalibrate_after_the_calibration_ended_by_itself_is_accepted(cam):
    cam.addCalibrate(secBetweenImages=0.05, timeoutSec=0.2)
    first = cam.calibrate['default']
    assert _wait_until(lambda: not first.isThreadActive)

    cam.addCalibrate(secBetweenImages=0.05, timeoutSec=0.2)

    assert cam.calibrate['default'] is not first
    assert not any('already running' in m for m in cam.logger.errors())


def test_addTimelapse_after_the_time_limit_was_reached_is_accepted(cam, tmp_path):
    cam.addTimelapse(outputDir=str(tmp_path), secBetwPhotos=0.05, timeLimitSec=0.2)
    first = cam.timelapse['default']
    assert _wait_until(lambda: not first.isThreadActive)

    cam.addTimelapse(outputDir=str(tmp_path), secBetwPhotos=0.05, timeLimitSec=0.2)

    assert cam.timelapse['default'] is not first
    assert not any('already running' in m for m in cam.logger.errors())


def test_calibrate_stop_during_the_wait_between_images_sticks(cam):
    cam.addCalibrate(secBetweenImages=0.4, timeoutSec=30)
    first = cam.calibrate['default']
    # The placeholder deque entry has img_x_y == (); a real iteration fills it,
    # so the thread is now in (or about to enter) its inter-image sleep.
    assert _wait_until(lambda: first.deque and first.deque[-1].get('img_x_y'))

    first.stop()
    time.sleep(0.7)                        # longer than the sleep

    assert not first.isThreadActive        # the loop used to set it back from camOn
