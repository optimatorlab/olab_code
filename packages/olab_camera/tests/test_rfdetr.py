"""Offline contracts for the optional local RF-DETR camera feature."""

import os
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from olab_camera.camera import Camera
from olab_camera.cv_features import _RFDETR
import olab_utils


def test_add_rfdetr_missing_local_weights_never_registers_or_imports():
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 1})
    cam.addRFDETR('local', weights_path='/no/such/rfdetr-checkpoint.pth')
    assert cam.rfdetr == {}


def test_normalized_result_keeps_native_detection_and_tracker_ids():
    feature = _RFDETR.__new__(_RFDETR)
    feature.tracker = object()
    detections = SimpleNamespace(
        xyxy=np.array([[1.2, 2.8, 8.9, 9.1]]),
        class_id=np.array([3]), confidence=np.array([0.75]),
        tracker_id=np.array([-1]), mask=np.array([[[True, False], [False, True]]]),
        data={'class_name': np.array(['widget'], dtype=object)},
    )
    result = feature._normalise(detections)
    assert result['class'] == ['widget']
    assert result['class_id'] == [3]
    assert result['class_conf'] == [0.75]
    assert result['xyxy'] == [[1, 2, 8, 9]]
    assert result['track_id'] == [-1]
    assert result['detections'] is detections
    assert np.array_equal(result['masks'][0], detections.mask[0])


@pytest.mark.parametrize('kwargs', [
    {'task': 'pose'}, {'model_variant': 'xlarge'}, {'tracker': 'sort'},
])
def test_add_rfdetr_rejects_invalid_public_choices(kwargs, tmp_path):
    path = tmp_path / 'weights.pth'
    path.touch()
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 1})
    cam.addRFDETR('local', weights_path=path, **kwargs)
    assert cam.rfdetr == {}


def test_constructor_is_atomic_when_tracker_setup_fails(monkeypatch, tmp_path):
    path = tmp_path / 'weights.pth'
    path.touch()
    monkeypatch.setitem(sys.modules, 'rfdetr', SimpleNamespace(RFDETRSmall=lambda **kwargs: object()))
    monkeypatch.setitem(sys.modules, 'trackers', SimpleNamespace(ByteTrackTracker=lambda **kwargs: (_ for _ in ()).throw(RuntimeError('no tracker'))))
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 1})
    feature = _RFDETR(cam, 'local', 'detect', 'small', str(path), 8, 8, 1, None, None, (0, 0, 0), .25, 'bytetrack', True, True, False)
    assert not hasattr(feature, 'model')


def test_constructor_forces_explicit_cpu_device_by_default(monkeypatch, tmp_path):
    path = tmp_path / 'weights.pth'
    path.touch()
    received = {}
    monkeypatch.setitem(sys.modules, 'rfdetr', SimpleNamespace(RFDETRSmall=lambda **kwargs: received.update(kwargs) or object()))
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 1})
    _RFDETR(cam, 'local', 'detect', 'small', str(path), 8, 8, 1, None, None, (0, 0, 0), .25, None, True, True, False)
    assert received['device'] == 'cpu'


def test_automatic_rfdetr_drawing_defaults_to_boxes_and_labels(monkeypatch, tmp_path):
    path = tmp_path / 'weights.pth'
    path.touch()
    monkeypatch.setitem(sys.modules, 'rfdetr', SimpleNamespace(RFDETRSmall=lambda **kwargs: object()))
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 1})
    feature = _RFDETR(cam, 'local', 'detect', 'small', str(path), 8, 8, 1,
                       None, None, (0, 0, 0), .25, None, None, None, False)
    assert feature.drawBox is True
    assert feature.drawLabel is True


def test_stop_waits_for_in_flight_predict(monkeypatch, tmp_path):
    path = tmp_path / 'weights.pth'
    path.touch()
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()

    def predict(*args, **kwargs):
        entered.set()
        release.wait(timeout=2)
        return SimpleNamespace(xyxy=np.empty((0, 4)), class_id=np.array([]),
                               confidence=np.array([]), tracker_id=None,
                               mask=None, data={'class_name': np.array([])})

    monkeypatch.setitem(sys.modules, 'rfdetr', SimpleNamespace(
        RFDETRSmall=lambda **kwargs: SimpleNamespace(predict=predict)))
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 20})
    cam.frameDeque.append(np.zeros((8, 8, 3), dtype=np.uint8))
    cam.fps['capture'].actual = 100
    cam.camOn = True
    feature = _RFDETR(cam, 'blocked', 'detect', 'small', str(path), 8, 8, 20,
                      None, None, (0, 0, 0), .25, None, True, True, False)
    feature.start()
    assert entered.wait(timeout=1)
    stopper = threading.Thread(target=lambda: (feature.stop(), stopped.set()))
    stopper.start()
    assert not stopped.wait(timeout=.05)
    release.set()
    stopper.join(timeout=1)
    assert stopped.is_set()
    assert feature._thread is None


def test_feature_callback_arguments_are_isolated(monkeypatch, tmp_path):
    path = tmp_path / 'weights.pth'
    path.touch()
    monkeypatch.setitem(sys.modules, 'rfdetr', SimpleNamespace(RFDETRSmall=lambda **kwargs: object()))
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 1})
    first = _RFDETR(cam, 'first', 'detect', 'small', str(path), 8, 8, 1, None, None, (0, 0, 0), .25, None, True, True, False)
    second = _RFDETR(cam, 'second', 'detect', 'small', str(path), 8, 8, 1, None, None, (0, 0, 0), .25, None, True, True, False)
    assert first.postFunctionArgs == {'idName': 'first'}
    assert second.postFunctionArgs == {'idName': 'second'}
    assert first.postFunctionArgs is not second.postFunctionArgs


def test_add_rfdetr_passes_absolute_weights_path(monkeypatch, tmp_path):
    weights = tmp_path / 'rf-detr-small.pth'
    weights.touch()
    received = []
    class FakeFeature:
        def __init__(self, *args):
            received.append(args[4]); self.model = object(); self.isThreadActive = False
        def start(self): pass
    monkeypatch.setattr('olab_camera.camera._RFDETR', FakeFeature)
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 1})
    cam.addRFDETR('local', weights_path=weights)
    assert received == [os.path.abspath(weights)]


def test_add_rfdetr_resolves_relative_weights_in_shared_models_dir(monkeypatch, tmp_path):
    models_dir = tmp_path / 'Projects' / 'olab_models'
    models_dir.mkdir(parents=True)
    weights = models_dir / 'rf-detr-small.pth'
    weights.touch()
    received = []
    class FakeFeature:
        def __init__(self, *args): received.append(args[4]); self.model = object(); self.isThreadActive = False
        def start(self): pass
    monkeypatch.setattr('olab_camera.camera._RFDETR', FakeFeature)
    monkeypatch.setattr('olab_camera.camera.os.path.expanduser', lambda value: str(tmp_path / 'Projects' / 'olab_models'))
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 1})
    cam.addRFDETR('local', weights_path='rf-detr-small.pth')
    assert received == [str(weights)]


def test_decorate_rfdetr_handles_empty_detection_and_mask():
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    olab_utils.decorateRFDETR(image, {'class': [], 'xyxy': [], 'masks': []})
    olab_utils.decorateRFDETR(image, {'class': ['x'], 'class_conf': [.5], 'track_id': [2], 'xyxy': [[0, 0, 3, 3]], 'masks': [np.eye(4, dtype=bool)]}, maskOutline=True)
    assert image.any()


def test_readd_stops_active_feature_before_replacing(monkeypatch, tmp_path):
    weights = tmp_path / 'weights.pth'
    weights.touch()
    stopped = []
    old = SimpleNamespace(isThreadActive=True, stop=lambda: stopped.append(True))
    class FakeFeature:
        def __init__(self, *args): self.model = object(); self.isThreadActive = False
        def start(self): pass
    monkeypatch.setattr('olab_camera.camera._RFDETR', FakeFeature)
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 1})
    cam.rfdetr['same'] = old
    cam.addRFDETR('same', weights_path=weights)
    assert stopped == [True]
    assert isinstance(cam.rfdetr['same'], FakeFeature)


def test_decoration_lifecycle_with_fake_local_model(monkeypatch, tmp_path):
    weights = tmp_path / 'weights.pth'
    weights.touch()
    empty = lambda: SimpleNamespace(xyxy=np.empty((0, 4)), class_id=np.array([]), confidence=np.array([]), tracker_id=None, mask=None, data={'class_name': np.array([])})
    monkeypatch.setitem(sys.modules, 'rfdetr', SimpleNamespace(RFDETRSmall=lambda **kwargs: SimpleNamespace(predict=lambda *args, **kwargs: empty())))
    cam = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 20})
    cam.frameDeque.append(np.zeros((8, 8, 3), dtype=np.uint8))
    cam.camOn = True
    cam.addRFDETR('decorated', weights_path=weights)
    cam.manageDecorationsDeque()
    feature = cam.rfdetr['decorated']
    assert feature.decorationID is not None
    assert any(item['decorationID'] == feature.decorationID for item in cam.dec['active'])
    feature.stop()
    cam.camOn = False
    time.sleep(.05)
    cam.manageDecorationsDeque()
    assert not cam.dec['active']
