"""Narrow MJPEG startup regression coverage."""

import io
import threading

import numpy as np

from olab_camera.streaming import StreamingHandler
from olab_camera import CameraUSB
import olab_camera.camera as camera_module


class _WakeThenFrameCondition:
    def __init__(self, camera):
        self._camera = camera
        self.wait_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def wait(self, _timeout):
        self.wait_calls += 1
        if self.wait_calls == 2:
            self._camera.frameDeque.append(np.zeros((2, 2, 3), dtype=np.uint8))
        return True


class _StartupCamera:
    def __init__(self):
        self.ipAllowlist = []
        self.ipBlocklist = []
        self.keepStreaming = True
        self.frameDeque = []
        self.res_rows = 2
        self.res_cols = 2
        self.fps = {'stream': object()}
        self.condition = _WakeThenFrameCondition(self)
        self.stream_clients = 0
        self.decorated = 0

    def streamIncr(self, delta):
        self.stream_clients += delta

    def getFrameCopy(self):
        return self.frameDeque[-1].tobytes()

    def decorateFrame(self, _frame):
        self.decorated += 1
        self.keepStreaming = False

    def calcFramerate(self, _fps, _name):
        pass


def test_mjpeg_empty_startup_wakeup_waits_for_later_frame_without_index_error():
    camera = _StartupCamera()
    handler = object.__new__(StreamingHandler)
    handler.camObject = camera
    handler.path = '/stream.mjpg'
    handler.client_address = ('127.0.0.1', 10000)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda _status: None
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None

    handler.do_GET()

    assert camera.condition.wait_calls == 2
    assert camera.decorated == 1
    assert b'--FRAME\r\n' in handler.wfile.getvalue()


def test_startStream_threads_explicit_bind_host_and_advertised_host(monkeypatch):
    """The new arguments must reach the MJPEG worker and direct URL without
    changing the default public API's worker-start machinery."""
    captured = []

    class _Thread:
        def __init__(self, *, target, args):
            captured.append((target, args))
            self.daemon = False

        def start(self):
            pass

    monkeypatch.setattr(camera_module.threading, 'Thread', _Thread)
    cam = CameraUSB(paramDict={'res_rows': 2, 'res_cols': 2, 'fps_target': 30, 'outputPort': 8000})
    cam.startStream(8123, protocol='mjpeg', bindHost='127.0.0.1', advertisedHost='camera.example')

    assert cam.streamURL == 'https://camera.example:8123/stream.mjpg'
    assert captured == [(cam._thread_stream_mjpeg, (8123, 1, '127.0.0.1'))]
