"""Regression tests for StreamingServer's per-connection TLS handshake wrapping.

Covers the fix for a hang where wrapping the *listening* socket with TLS made
accept() perform the handshake synchronously in the main accept loop, so a
single stalled/failed client handshake blocked every other client -- from any
source IP -- from connecting at all. These tests use a minimal one-shot
handler with no Camera dependency; camera.py's actual _thread_stream_mjpeg()
wiring is exercised only manually (see the pairwork file's Test Results)."""

import socket
import ssl
import threading
import time
from http import server as http_server

import pytest

from olab_camera.streaming import StreamingServer
from olab_camera.tls import generate_self_signed_cert


class _OneShotHandler(http_server.BaseHTTPRequestHandler):
    """No Camera/camObject dependency -- just proves a request round-trips."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, format, *args):
        pass  # keep test output quiet


class _EventSignalingServer(StreamingServer):
    """Test-only subclass: sets `entered_event` the instant a connection's
    process_request_thread() begins, before any TLS work happens. This is a
    deterministic proof that a given connection has already left the shared
    accept loop and is now running on its own per-connection thread (which is
    exactly what the fix is supposed to guarantee) -- as opposed to inferring
    it indirectly via a fixed `time.sleep()`, which can't rule out the accept
    loop still being the one blocked under scheduler load."""

    def __init__(self, *args, entered_event, **kwargs):
        self._entered_event = entered_event
        super().__init__(*args, **kwargs)

    def process_request_thread(self, request, client_address):
        self._entered_event.set()
        super().process_request_thread(request, client_address)


class _RecordingLogger:
    """`log_handshake_failure` stub that also signals a `threading.Event`.

    A failed/timed-out handshake's socket teardown (e.g. a fatal TLS alert
    sent to the peer from inside `wrap_socket()`) can be observed by the
    client *before* `process_request_thread()`'s except/finally block even
    runs `log_handshake_failure(...)` -- so waiting for the peer to see the
    connection close is not a valid synchronization point for asserting on
    what was logged. This records the call and sets `event` in the same
    call, so tests can `event.wait()` for the callback itself to have run,
    independent of anything observable on the socket."""

    def __init__(self):
        self.calls = []
        self.event = threading.Event()

    def __call__(self, client_address, exc):
        self.calls.append((client_address, exc))
        self.event.set()


def _make_server_ssl_context(tmp_path):
    cert_path = tmp_path / 'ca.crt'
    key_path  = tmp_path / 'ca.key'
    generate_self_signed_cert(cert_path, key_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


def _make_client_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _start_server(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3.0)


def _wait_until_peer_closes(sock, timeout=3.0):
    """Poll until the peer closes the connection (empty recv) or drops it (OSError)."""
    deadline = time.monotonic() + timeout
    sock.settimeout(0.1)
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            return True
        if chunk == b'':
            return True
    return False


def _https_get(port, timeout=3.0):
    """Perform one real TLS GET against 127.0.0.1:port, return the raw response bytes."""
    client_ctx = _make_client_ssl_context()
    raw = socket.create_connection(('127.0.0.1', port), timeout=timeout)
    wrapped = None
    try:
        wrapped = client_ctx.wrap_socket(raw, server_hostname='127.0.0.1')
        wrapped.settimeout(timeout)
        wrapped.sendall(b'GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
        data = b''
        while True:
            chunk = wrapped.recv(4096)
            if not chunk:
                break
            data += chunk
        return data
    finally:
        (wrapped or raw).close()


# --- (a) core regression: stalled handshake must not block other clients ---

def test_stalled_handshake_does_not_block_other_clients(tmp_path):
    ssl_context = _make_server_ssl_context(tmp_path)
    entered = threading.Event()
    server = _EventSignalingServer(
        ('127.0.0.1', 0), _OneShotHandler, ssl_context=ssl_context,
        handshake_timeout=5.0, entered_event=entered)
    thread = _start_server(server)
    port = server.server_address[1]

    stalled = socket.create_connection(('127.0.0.1', port), timeout=5.0)
    try:
        # Deterministic proof the stalled connection has already left the
        # accept loop and is now blocked (if at all) in its own
        # per-connection thread -- only the stalled connection exists yet,
        # so this event can only have been set by it, not the healthy
        # client opened below.
        assert entered.wait(timeout=3.0), 'stalled connection never reached its own per-connection thread'

        deadline = time.monotonic() + 3.0
        response = _https_get(port, timeout=3.0)
        assert time.monotonic() < deadline, 'a well-behaved client should not be delayed by the stalled one'
        assert response.split(b'\r\n', 1)[0].split(b' ')[1] == b'200'
    finally:
        stalled.close()
        _stop_server(server, thread)


# --- (b) handshake-failure logging/cleanup: fast, deterministic SSLError path ---

def test_garbage_handshake_bytes_logged_and_cleaned_up_server_keeps_running(tmp_path):
    ssl_context = _make_server_ssl_context(tmp_path)
    logger = _RecordingLogger()
    server = StreamingServer(
        ('127.0.0.1', 0), _OneShotHandler, ssl_context=ssl_context,
        log_handshake_failure=logger)
    thread = _start_server(server)
    port = server.server_address[1]

    try:
        bad = socket.create_connection(('127.0.0.1', port), timeout=3.0)
        bad.sendall(b'this is not a TLS ClientHello')

        # Synchronize on the callback itself having run -- not on the peer
        # observing the connection close, which can happen earlier (see
        # _RecordingLogger's docstring).
        assert logger.event.wait(timeout=3.0), 'log_handshake_failure was never called'
        assert len(logger.calls) == 1
        addr, exc = logger.calls[0]
        assert addr[0] == '127.0.0.1'
        assert isinstance(exc, Exception)

        closed = _wait_until_peer_closes(bad, timeout=3.0)
        bad.close()
        assert closed, 'server should close the connection after a failed handshake'

        response = _https_get(port, timeout=3.0)   # server must still accept other clients
        assert response.split(b'\r\n', 1)[0].split(b' ')[1] == b'200'
    finally:
        _stop_server(server, thread)


def test_silent_connection_times_out_and_is_logged(tmp_path):
    ssl_context = _make_server_ssl_context(tmp_path)
    logger = _RecordingLogger()
    server = StreamingServer(
        ('127.0.0.1', 0), _OneShotHandler, ssl_context=ssl_context,
        log_handshake_failure=logger,
        handshake_timeout=0.5)
    thread = _start_server(server)
    port = server.server_address[1]

    try:
        silent = socket.create_connection(('127.0.0.1', port), timeout=3.0)

        assert logger.event.wait(timeout=3.0), 'log_handshake_failure was never called'
        assert len(logger.calls) == 1

        closed = _wait_until_peer_closes(silent, timeout=3.0)
        silent.close()
        assert closed, 'a silent connection should be dropped once handshake_timeout elapses'
    finally:
        _stop_server(server, thread)


# --- (c) plain-HTTP backward compatibility: no ssl_context, unchanged behavior ---

def test_plain_http_backward_compatible_no_ssl_context():
    server = StreamingServer(('127.0.0.1', 0), _OneShotHandler)
    thread = _start_server(server)
    port = server.server_address[1]

    try:
        raw = socket.create_connection(('127.0.0.1', port), timeout=3.0)
        try:
            raw.sendall(b'GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
            data = b''
            while True:
                chunk = raw.recv(4096)
                if not chunk:
                    break
                data += chunk
        finally:
            raw.close()
        assert data.split(b'\r\n', 1)[0].split(b' ')[1] == b'200'
    finally:
        _stop_server(server, thread)


# --- (d) a raising log_handshake_failure callback must not break cleanup ---

def test_raising_log_callback_does_not_break_cleanup_or_server(tmp_path):
    ssl_context = _make_server_ssl_context(tmp_path)

    def _raising_logger(addr, e):
        raise RuntimeError('boom')

    excepthook_calls = []
    orig_hook = threading.excepthook
    threading.excepthook = excepthook_calls.append

    server = StreamingServer(
        ('127.0.0.1', 0), _OneShotHandler, ssl_context=ssl_context,
        log_handshake_failure=_raising_logger)
    thread = _start_server(server)
    port = server.server_address[1]

    try:
        bad = socket.create_connection(('127.0.0.1', port), timeout=3.0)
        bad.sendall(b'not a client hello')
        closed = _wait_until_peer_closes(bad, timeout=3.0)
        bad.close()
        assert closed, 'socket must still be closed even though the logging callback raised'

        response = _https_get(port, timeout=3.0)   # server keeps accepting connections
        assert response.split(b'\r\n', 1)[0].split(b' ')[1] == b'200'
    finally:
        _stop_server(server, thread)
        threading.excepthook = orig_hook

    assert excepthook_calls == [], 'the raising callback must not escape the worker thread'


# --- (e) constructor validation ---

@pytest.mark.parametrize('bad_timeout', [0, -1, -0.5, None, float('nan'), float('inf'), float('-inf'), True, False])
def test_invalid_handshake_timeout_rejected_when_ssl_context_set(tmp_path, bad_timeout):
    ssl_context = _make_server_ssl_context(tmp_path)
    with pytest.raises(ValueError):
        StreamingServer(('127.0.0.1', 0), _OneShotHandler, ssl_context=ssl_context, handshake_timeout=bad_timeout)


@pytest.mark.parametrize('bad_ssl_context', ['not-a-context', 123, {}])
def test_invalid_ssl_context_type_rejected(bad_ssl_context):
    with pytest.raises(ValueError):
        StreamingServer(('127.0.0.1', 0), _OneShotHandler, ssl_context=bad_ssl_context)


@pytest.mark.parametrize('bad_callback', ['not-callable', 123])
def test_invalid_log_handshake_failure_rejected(bad_callback):
    with pytest.raises(ValueError):
        StreamingServer(('127.0.0.1', 0), _OneShotHandler, log_handshake_failure=bad_callback)


@pytest.mark.parametrize('any_timeout', [0, -1, None, float('nan'), float('inf'), True])
def test_handshake_timeout_unvalidated_when_ssl_context_none(any_timeout):
    # No ssl_context -> handshake_timeout is not even inspected; construction succeeds,
    # matching the plain-HTTP backward-compatibility guarantee in (c).
    server = StreamingServer(('127.0.0.1', 0), _OneShotHandler, handshake_timeout=any_timeout)
    server.server_close()


def test_invalid_construction_leaves_no_socket_bound(tmp_path):
    ssl_context = _make_server_ssl_context(tmp_path)

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()

    with pytest.raises(ValueError):
        StreamingServer(('127.0.0.1', port), _OneShotHandler, ssl_context=ssl_context, handshake_timeout=0)

    # Validation raises before super().__init__() binds -- the port must still be free.
    server = StreamingServer(('127.0.0.1', port), _OneShotHandler)
    server.server_close()


def test_valid_construction_with_full_tls_config_succeeds_and_shuts_down_cleanly(tmp_path):
    ssl_context = _make_server_ssl_context(tmp_path)
    server = StreamingServer(
        ('127.0.0.1', 0), _OneShotHandler, ssl_context=ssl_context,
        log_handshake_failure=lambda addr, e: None, handshake_timeout=5.0)
    thread = _start_server(server)
    _stop_server(server, thread)
