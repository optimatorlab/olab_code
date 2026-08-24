"""Deterministic contracts for detector-agnostic local tracker fan-out."""

import numpy as np
from olab_camera.camera import Camera


class _FakeBackend:
    def __init__(self, name, fail=False, mutate=False):
        self.name, self.fail, self.mutate, self.calls = name, fail, mutate, []

    def update(self, payload, frame=None, timestamp=None):
        self.calls.append((payload, frame, timestamp))
        if self.fail:
            raise RuntimeError('expected backend failure')
        if self.mutate:
            payload['xyxy'][:] = 999
        return {
            'xyxy': payload['xyxy'].tolist(),
            'track_id': (np.arange(len(payload['xyxy']), dtype=int) + 10).tolist(),
            **{name: value.tolist() if isinstance(value, np.ndarray) else value
               for name, value in payload.items() if name != 'xyxy'},
        }


def _camera_with_fake_backends(monkeypatch):
    made = {}
    def make_backend(algorithm, fps):
        made[algorithm] = _FakeBackend(algorithm, fail=algorithm == 'ocsort')
        return made[algorithm]
    monkeypatch.setattr('olab_camera.tracking._make_backend', make_backend)
    camera = Camera({'res_rows': 8, 'res_cols': 8, 'fps_target': 20})
    camera.addTracker('sort', 'sort', decorate=False)
    camera.addTracker('byte', 'bytetrack', decorate=False)
    camera.addTracker('oc', 'ocsort', decorate=False)
    return camera, made


def test_fanout_isolated_and_continues_after_backend_failure(monkeypatch):
    camera, made = _camera_with_fake_backends(monkeypatch)
    payload = {'xyxy': [[1, 2, 5, 6]], 'class_id': [4], 'class': ['widget'], 'class_conf': [.8]}
    results = camera.updateTrackers(payload, ('sort', 'oc', 'byte'), frame=np.zeros((8, 8, 3), np.uint8), timestamp=1.0)
    assert results['sort']['track_id'] == [10]
    assert results['oc'] is None
    assert results['byte']['class'] == ['widget']
    assert all(len(made[name].calls) == 1 for name in ('sort', 'ocsort', 'bytetrack'))
    assert payload['xyxy'] == [[1, 2, 5, 6]]


def test_each_backend_gets_an_independent_payload_and_immutable_frame(monkeypatch):
    camera, made = _camera_with_fake_backends(monkeypatch)
    made['sort'].mutate = True
    frame = np.zeros((8, 8, 3), np.uint8)
    results = camera.updateTrackers({'xyxy': [[1, 2, 5, 6]]}, ('sort', 'byte'), frame=frame)
    assert results['sort']['xyxy'] == [[999.0, 999.0, 999.0, 999.0]]
    assert results['byte']['xyxy'] == [[1.0, 2.0, 5.0, 6.0]]
    assert not made['bytetrack'].calls[0][1].flags.writeable
    assert frame.tolist() == np.zeros((8, 8, 3), np.uint8).tolist()


def test_invalid_payload_or_selection_advances_no_tracker(monkeypatch):
    camera, made = _camera_with_fake_backends(monkeypatch)
    assert camera.updateTrackers({'xyxy': [[1, 2, 3]]}, ('sort', 'byte')) == {'sort': None, 'byte': None}
    assert camera.updateTrackers({'xyxy': [[1, 2, 3, 4]]}, ('sort', 'sort')) == {'sort': None}
    assert not made['sort'].calls and not made['bytetrack'].calls


def test_backward_timestamp_is_atomic_and_stop_clears_registry(monkeypatch):
    camera, made = _camera_with_fake_backends(monkeypatch)
    payload = {'xyxy': [[1, 2, 3, 4]]}
    camera.updateTrackers(payload, ('sort', 'byte'), timestamp=2.0)
    assert camera.updateTrackers(payload, ('sort', 'byte'), timestamp=1.0) == {'sort': None, 'byte': None}
    assert len(made['sort'].calls) == len(made['bytetrack'].calls) == 1
    camera._stopTrackers()
    assert camera.trackers == {}


def test_failed_readd_keeps_old_tracker_and_callback_failure_is_contained(monkeypatch):
    camera, made = _camera_with_fake_backends(monkeypatch)
    old = camera.trackers['sort']

    def fail_make_backend(algorithm, fps):
        raise RuntimeError('expected construction failure')

    monkeypatch.setattr('olab_camera.tracking._make_backend', fail_make_backend)
    camera.addTracker('sort', 'sort', decorate=False)
    assert camera.trackers['sort'] is old
    monkeypatch.setattr('olab_camera.tracking._make_backend',
                        lambda algorithm, fps: _FakeBackend(algorithm))
    camera.addTracker('callback', 'sort', postFunction=lambda args: (_ for _ in ()).throw(RuntimeError('callback')), decorate=False)
    results = camera.updateTrackers({'xyxy': [[1, 2, 3, 4]]}, ('callback', 'byte'))
    assert results == {'callback': None, 'byte': {'xyxy': [[1.0, 2.0, 3.0, 4.0]], 'track_id': [10]}}
    assert camera.trackers['callback'].deque[0]['track_id'] == [10]
