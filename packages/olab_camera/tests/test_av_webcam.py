"""Tests for AVWebcam (packages/olab_camera/src/olab_camera/av_webcam.py).

Monkeypatches CameraUSB.start/stop and Mic.start/stop directly (class
methods) so AVWebcam's own composition/cleanup logic is exercised without
CameraUSB/Mic's internals (cv2.VideoCapture, pyaudio) ever running -- one
level up from olab_audio's test_mic.py's existing style of patching
olab_audio.mic.audio.
"""

import pytest

pytest.importorskip(
    "olab_audio",
    reason="AVWebcam's 'av' extra (olab-audio) is not installed in this env",
)

from olab_audio.mic import Mic
from olab_camera.av_webcam import AVWebcam
from olab_camera.camera_usb import CameraUSB


def _fake_camera_start_ok(self, **kwargs):
    self._last_start_kwargs = kwargs
    self.camOn = True


def _fake_camera_start_fails(self, **kwargs):
    self._last_start_kwargs = kwargs
    self.camOn = False


def _fake_mic_start_ok(self, **kwargs):
    self.micOn = True


def _fake_mic_start_fails(self, **kwargs):
    self.micOn = False


def test_construction_composes_both_devices_with_kwargs(monkeypatch):
    av = AVWebcam(
        camera_device='/dev/video0',
        mic_device=3,
        camera_kwargs={'apiPref': 0},
        mic_kwargs={'channels': 1},
    )

    assert isinstance(av.camera, CameraUSB)
    assert isinstance(av.mic, Mic)
    assert av.camera.device == '/dev/video0'
    assert av.camera.apiPref == 0
    assert av.mic.deviceID == 3
    assert av.mic.channels == 1


def test_start_starts_both_devices(monkeypatch):
    monkeypatch.setattr(CameraUSB, 'start', _fake_camera_start_ok)
    monkeypatch.setattr(Mic, 'start', _fake_mic_start_ok)

    av = AVWebcam(camera_device='/dev/video0', mic_device=3)
    av.start()

    assert av.camera.camOn is True
    assert av.mic.micOn is True


def test_start_cleans_up_camera_when_mic_fails(monkeypatch):
    stop_calls = []
    monkeypatch.setattr(CameraUSB, 'start', _fake_camera_start_ok)
    monkeypatch.setattr(CameraUSB, 'stop', lambda self, **kw: stop_calls.append('camera'))
    monkeypatch.setattr(Mic, 'start', _fake_mic_start_fails)

    av = AVWebcam(camera_device='/dev/video0', mic_device=3)
    with pytest.raises(RuntimeError):
        av.start()

    assert stop_calls == ['camera']


def test_start_never_calls_mic_start_when_camera_fails(monkeypatch):
    mic_start_calls = []
    monkeypatch.setattr(CameraUSB, 'start', _fake_camera_start_fails)
    monkeypatch.setattr(Mic, 'start', lambda self, **kw: mic_start_calls.append(1))

    av = AVWebcam(camera_device='/dev/video0', mic_device=3)
    with pytest.raises(RuntimeError):
        av.start()

    assert mic_start_calls == []


def test_camera_and_mic_attributes_give_working_access_to_normal_apis(monkeypatch):
    monkeypatch.setattr(CameraUSB, 'start', _fake_camera_start_ok)
    monkeypatch.setattr(Mic, 'start', _fake_mic_start_ok)

    av = AVWebcam(camera_device='/dev/video0', mic_device=3)

    received = []
    av.mic.subscribe(lambda deviceID, data: received.append(deviceID))
    av.start()

    # subscribe() registered before start() -- Task 1's persistence
    # guarantee -- drive the callback directly the same way test_mic.py does.
    av.mic._fire_callbacks(b'\x00\x00')
    assert received == [3]


def test_stop_still_attempts_mic_stop_when_camera_stop_and_its_logger_both_raise(monkeypatch):
    """Reviewer finding #2: a raising camera.stop() combined with a raising
    camera.logger.log() must not skip the mic's stop attempt, and
    AVWebcam.stop() must not itself raise."""
    mic_stop_calls = []

    def failing_camera_stop(self, **kw):
        raise RuntimeError('camera stop failed')

    def failing_logger_log(*args, **kwargs):
        raise RuntimeError('logger itself is broken')

    monkeypatch.setattr(CameraUSB, 'start', _fake_camera_start_ok)
    monkeypatch.setattr(CameraUSB, 'stop', failing_camera_stop)
    monkeypatch.setattr(Mic, 'start', _fake_mic_start_ok)
    monkeypatch.setattr(Mic, 'stop', lambda self, **kw: mic_stop_calls.append(1))

    av = AVWebcam(camera_device='/dev/video0', mic_device=3)
    av.camera.logger.log = failing_logger_log
    av.start()

    av.stop()  # must not raise

    assert mic_stop_calls == [1]


def test_stop_still_attempts_camera_stop_when_mic_stop_and_its_excfunc_both_raise(monkeypatch):
    """Reviewer finding #2, mirrored on the mic side: a raising mic.stop()
    combined with a raising mic.excFunc must not prevent the camera's stop
    from being attempted, and AVWebcam.stop() must not itself raise."""
    camera_stop_calls = []

    def failing_mic_stop(self, **kw):
        raise RuntimeError('mic stop failed')

    def raising_excfunc(msg):
        raise RuntimeError('excFunc itself is broken')

    monkeypatch.setattr(CameraUSB, 'start', _fake_camera_start_ok)
    monkeypatch.setattr(CameraUSB, 'stop', lambda self, **kw: camera_stop_calls.append(1))
    monkeypatch.setattr(Mic, 'start', _fake_mic_start_ok)
    monkeypatch.setattr(Mic, 'stop', failing_mic_stop)

    av = AVWebcam(camera_device='/dev/video0', mic_device=3, mic_kwargs={'excFunc': raising_excfunc})
    av.start()

    av.stop()  # must not raise

    assert camera_stop_calls == [1]


def test_start_calls_camera_start_with_no_args(monkeypatch):
    """AVWebcam.start() is basic-capture-only -- it must never implicitly
    start streaming or ROS publishing on the camera (reviewer finding #4)."""
    monkeypatch.setattr(CameraUSB, 'start', _fake_camera_start_ok)
    monkeypatch.setattr(Mic, 'start', _fake_mic_start_ok)

    av = AVWebcam(camera_device='/dev/video0', mic_device=3)
    av.start()

    assert av.camera._last_start_kwargs == {}
