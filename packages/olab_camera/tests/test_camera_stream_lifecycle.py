"""Regression tests for Camera.stopStream() actually stopping the MJPEG
server (olab_code#35). stopStream() used to only flip self.keepStreaming
and clear bookkeeping -- it never called server.shutdown(), so the
listening socket stayed bound forever, blocking port reuse. See
.pairwork/fix-stopstream-server-shutdown.md for the full design history
(three rounds of review found and fixed two real startup/replacement
races, then twice-corrected the "is serve_forever() actually running yet"
signal used to make calling shutdown() safe).

Uses CameraUSB as the concrete Camera subclass under test -- same pattern
as test_camera_ssl_defer.py -- since these tests exercise base Camera
behavior (startStream()/stopStream()), not anything USB-specific.
"""

import functools
import socket
import threading
import time
from pathlib import Path

import pytest

from olab_camera import CameraUSB
from olab_camera.streaming import StreamingServer
import olab_camera.camera as camera_module


PARAM_DICT = {'res_rows': 480, 'res_cols': 640, 'fps_target': 30, 'outputPort': 8000}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # startStream(protocol='mjpeg') lazily auto-generates a self-signed cert
    # under ~/.olab_camera/ssl on first use (see Camera._ensureSslPath()) --
    # redirect that to a throwaway tmp_path rather than touching the real
    # home directory or (as an explicit sslPath would) skipping generation
    # entirely and leaving no cert files for load_cert_chain() to find.
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)


def _make_camera(tmp_path):
    return CameraUSB(paramDict=dict(PARAM_DICT))


def _wait_for_mjpeg_server(cam, timeout=2.0):
    deadline = time.monotonic() + timeout
    while cam._mjpegServer is None and time.monotonic() < deadline:
        time.sleep(0.001)
    return cam._mjpegServer


def _assert_port_bindable(port, timeout=2.0):
    """The actual regression check: try to bind a plain socket to `port`.
    Retries briefly since server_close() and the OS releasing the socket
    aren't necessarily observable in the exact same instant."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('', port))
            s.close()
            return
        except OSError as e:
            last_error = e
            s.close()
            time.sleep(0.01)
    raise AssertionError(f'port {port} never became bindable: {last_error}')


def test_stopStream_releases_the_mjpeg_port_for_immediate_reuse(tmp_path):
    """The actual regression, end to end, with a real socket."""
    cam = _make_camera(tmp_path)
    cam.startStream(0, protocol='mjpeg')

    server = _wait_for_mjpeg_server(cam)
    assert server is not None
    port = server.server_address[1]

    cam.stopStream()

    _assert_port_bindable(port)


def test_loopback_bound_mjpeg_advertises_override_and_releases_port(tmp_path):
    """A private playground stream must bind only to loopback, advertise the
    requested hostname, and leave no listener after stop."""
    cam = _make_camera(tmp_path)
    cam.startStream(0, protocol='mjpeg', bindHost='127.0.0.1', advertisedHost='localhost')

    server = _wait_for_mjpeg_server(cam)
    assert server is not None
    port = server.server_address[1]
    assert server.server_address[0] == '127.0.0.1'
    # `startStream(0, ...)` records the literal caller-supplied port before
    # the worker receives the OS-assigned ephemeral port. Verify the new
    # advertised-host behavior without asserting an impossible URL port.
    assert cam.streamURL.startswith('https://localhost:')
    assert cam.streamURL.endswith('/stream.mjpg')

    cam.stopStream()

    _assert_port_bindable(port)


def test_stopStream_closes_all_interface_listener_after_public_bind(tmp_path):
    """The explicit LAN-visible binding is gone after stop, not just hidden
    from playground state."""
    cam = _make_camera(tmp_path)
    cam.startStream(0, protocol='mjpeg', bindHost='0.0.0.0', advertisedHost='192.0.2.10')

    server = _wait_for_mjpeg_server(cam)
    assert server is not None
    port = server.server_address[1]
    assert server.server_address[0] == '0.0.0.0'

    cam.stopStream()

    _assert_port_bindable(port)


def test_mjpegServer_is_none_after_stopStream(tmp_path):
    cam = _make_camera(tmp_path)
    cam.startStream(0, protocol='mjpeg')
    assert _wait_for_mjpeg_server(cam) is not None

    cam.stopStream()

    assert cam._mjpegServer is None


def test_stopStream_on_never_started_camera_does_not_raise_or_hang(tmp_path):
    cam = _make_camera(tmp_path)
    cam.stopStream()  # never started -- must be a safe no-op
    assert cam._mjpegServer is None

    cam.startStream(0, protocol='mjpeg')
    assert _wait_for_mjpeg_server(cam) is not None
    cam.stopStream()
    cam.stopStream()  # already stopped -- must also be a safe no-op
    assert cam._mjpegServer is None


class _DelayedInitServer(StreamingServer):
    """Test-only subclass: waits on a gate before super().__init__() ever
    runs, so a test can deterministically call stopStream() before the
    background thread has any chance to construct/publish its server --
    proving the round-1 stop-before-publish race is handled, rather than
    relying on a timing-dependent sleep."""

    def __init__(self, *args, gate, **kwargs):
        gate.wait()
        super().__init__(*args, **kwargs)


def _get_free_port():
    """port=0 can't be used for this test -- StreamingServer's __init__ is
    gated and hasn't run yet (so there's no bound socket to read an
    OS-assigned port off of) when stopStream() needs to be called. Grab a
    genuinely free port up front instead of a hardcoded constant, to avoid
    any collision risk."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_stopStream_during_delayed_mjpeg_startup_does_not_leak_the_port(tmp_path, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(camera_module, 'StreamingServer',
        functools.partial(_DelayedInitServer, gate=gate))

    port = _get_free_port()
    cam = _make_camera(tmp_path)
    cam.startStream(port, protocol='mjpeg')

    # stopStream() runs while the background thread is still blocked
    # before even constructing StreamingServer.
    cam.stopStream()
    assert cam._mjpegServer is None

    gate.set()  # let the delayed construction/publish-check proceed

    # The generation check must see this attempt as stale and close its
    # just-bound socket immediately without ever calling serve_forever().
    _assert_port_bindable(port)
    assert cam._mjpegServer is None


def test_force_replacement_then_stop_frees_both_ports(tmp_path):
    cam = _make_camera(tmp_path)
    cam.startStream(0, protocol='mjpeg')
    first_server = _wait_for_mjpeg_server(cam)
    assert first_server is not None
    first_port = first_server.server_address[1]

    cam.startStream(0, protocol='mjpeg', force=True)
    # Wait for the *replacement* to publish (a different server object).
    deadline = time.monotonic() + 2.0
    while (cam._mjpegServer is None or cam._mjpegServer is first_server) and time.monotonic() < deadline:
        time.sleep(0.001)
    second_server = cam._mjpegServer
    assert second_server is not None
    assert second_server is not first_server
    second_port = second_server.server_address[1]

    cam.stopStream()

    assert cam._mjpegServer is None
    _assert_port_bindable(first_port)
    _assert_port_bindable(second_port)


class _GatedServer(StreamingServer):
    """Test-only subclass: gates the *first* service_actions() call --
    which BaseServer.serve_forever() only ever invokes from inside its own
    loop, after it has already entered and completed one select(). This
    proves stopStream() genuinely waits for serve_forever() to be running
    before calling shutdown() (the documented-unsafe alternative), rather
    than a timing race that happens to usually work.

    Deliberately does NOT override serve_forever() itself -- an earlier
    version of this fixture did, and manually invoked service_actions()
    before delegating to the real loop, which silently reintroduced the
    exact same "signal fires before the loop is running" bug into the
    test harness. Gating inside service_actions() instead means the gate
    can only ever be reached by the stdlib's own loop, never manually."""

    def __init__(self, *args, entered_event, gate, **kwargs):
        self._entered_event = entered_event
        self._gate = gate
        super().__init__(*args, **kwargs)

    def service_actions(self):
        self._entered_event.set()
        self._gate.wait()
        super().service_actions()


def test_stopStream_waits_for_serve_forever_before_calling_shutdown(tmp_path, monkeypatch):
    entered = threading.Event()
    gate = threading.Event()
    monkeypatch.setattr(camera_module, 'StreamingServer',
        functools.partial(_GatedServer, entered_event=entered, gate=gate))

    cam = _make_camera(tmp_path)
    cam.startStream(0, protocol='mjpeg')

    server = _wait_for_mjpeg_server(cam)
    assert server is not None
    port = server.server_address[1]

    assert entered.wait(timeout=2.0)  # service_actions() has been reached and is gated

    shutdown_called = threading.Event()
    real_shutdown = server.shutdown
    def _spy_shutdown():
        shutdown_called.set()
        real_shutdown()
    server.shutdown = _spy_shutdown

    stop_thread = threading.Thread(target=cam.stopStream)
    stop_thread.start()

    time.sleep(0.1)  # give stopStream() a moment -- it must still be blocked
    assert not shutdown_called.is_set()
    assert stop_thread.is_alive()

    gate.set()  # release -- lets the real serve_forever() loop (and serving_event) proceed
    stop_thread.join(timeout=3.0)

    assert not stop_thread.is_alive()
    assert shutdown_called.is_set()
    _assert_port_bindable(port)
