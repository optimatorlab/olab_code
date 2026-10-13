from __future__ import annotations

import json
import threading
from collections import deque

from olab_rf.decoders.process import DecoderProcess
from olab_rf.decoders.remoteid_base import (
    RemoteIdBroadcast,
    safe_float,
    valid_altitude,
    valid_course,
    valid_lat_lon,
)


def droneid_go_command(
    path: str = "droneid-go",
    iface: str | None = None,
    zmq_host: str = "127.0.0.1",
    zmq_port: int = 4224,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build a droneid-go invocation for the WiFi transport.

    Flags follow droneid-go v0.3.1: `-z` enables the ZMQ publisher and
    `-zmqsetting host:port` sets its bind address (default 127.0.0.1:4224).
    `-ble` is never passed: it takes a Sniffle serial port (not a switch),
    and that mode is out of scope here (see `RemoteIdManager`'s
    duplicate-transport guard).
    """
    command = [path]
    if iface:
        command.extend(["-i", iface])
    command.extend(["-z", "-zmqsetting", f"{zmq_host}:{zmq_port}"])
    if extra_args:
        command.extend(extra_args)
    return command


def parse_droneid_go_json(payload: object) -> list[RemoteIdBroadcast]:
    """Parse one droneid-go ZMQ message into zero or more broadcasts.

    droneid-go's own wire schema is undocumented; this is grounded in a real
    downstream consumer (alphafox02/DragonSync's telemetry parser), which
    reads nested ODID message blocks ("Basic ID", "Location/Vector Message",
    "Self-ID Message", "System Message") from either a bare `dict` (one
    drone) or a `list` of such dicts (several). There is no flat `lat`/`lon`/
    `alt` on the wire -- those only exist after DragonSync's own
    normalization. Not yet verified against the real binary's output.
    """
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = [payload]
    else:
        return []
    broadcasts: list[RemoteIdBroadcast] = []
    for item in items:
        broadcast = _parse_item(item)
        if broadcast is not None:
            broadcasts.append(broadcast)
    return broadcasts


def _parse_item(item: object) -> RemoteIdBroadcast | None:
    if not isinstance(item, dict):
        return None

    basic = item.get("Basic ID")
    basic = basic if isinstance(basic, dict) else {}
    location = item.get("Location/Vector Message")
    location = location if isinstance(location, dict) else {}
    self_id_block = item.get("Self-ID Message")
    self_id_block = self_id_block if isinstance(self_id_block, dict) else {}
    system = item.get("System Message")
    system = system if isinstance(system, dict) else {}

    serial = basic.get("id")
    serial = str(serial) if serial else None
    mac = item.get("MAC") or basic.get("MAC")
    mac = str(mac) if mac else None
    if serial is None and mac is None:
        return None

    # The wire's own "transport" field (e.g. droneid-go's Sniffle BLE mode
    # reporting "bluetooth" on a message even though this backend instance
    # is configured/keyed as "wifi") is kept separate from the fixed nominal
    # `transport` below -- see `RemoteIdBroadcast.reported_transport`.
    reported_transport = basic.get("transport")
    reported_transport = str(reported_transport) if reported_transport else None
    lat, lon = valid_lat_lon(location.get("latitude"), location.get("longitude"))
    altitude_m = valid_altitude(location.get("geodetic_altitude"))
    speed_mps = safe_float(location.get("speed"))
    course_deg = valid_course(location.get("direction"))
    operator_lat = safe_float(system.get("operator_lat", system.get("latitude")))
    operator_lon = safe_float(system.get("operator_lon", system.get("longitude")))
    self_id = self_id_block.get("text")

    return RemoteIdBroadcast(
        backend="droneid-go",
        transport="wifi",
        reported_transport=reported_transport,
        serial=serial,
        mac=mac,
        lat=lat,
        lon=lon,
        altitude_m=altitude_m,
        speed_mps=speed_mps,
        course_deg=course_deg,
        operator_lat=operator_lat,
        operator_lon=operator_lon,
        self_id=str(self_id) if self_id else None,
        raw=item,
    )


class DroneidGoBackend:
    """WiFi Remote ID backend wrapping the droneid-go binary.

    Owns a `DecoderProcess` (the droneid-go subprocess, the same way
    `decoders/sdrtrunk.py`'s `SdrTrunkBackend` owns one) plus an internal ZMQ
    SUB background thread that subscribes to droneid-go's own ZMQ PUB socket
    (default port 4224). ZMQ is an implementation detail: `pyzmq` is
    imported lazily and synchronously in `start()`, and no ZMQ type ever
    appears on the public `start()`/`stop()`/`is_running()`/`poll()` surface.
    """

    transport = "wifi"
    name = "droneid-go"

    def __init__(
        self,
        *,
        path: str = "droneid-go",
        iface: str | None = None,
        zmq_host: str = "127.0.0.1",
        zmq_port: int = 4224,
        extra_args: list[str] | None = None,
        queue_maxlen: int = 2000,
        recv_timeout_ms: int = 500,
        join_timeout_s: float = 2.0,
    ) -> None:
        self.path = path
        self.iface = iface
        self.zmq_host = zmq_host
        self.zmq_port = zmq_port
        self.extra_args = list(extra_args or [])
        self.queue_maxlen = queue_maxlen
        self.recv_timeout_ms = recv_timeout_ms
        self.join_timeout_s = join_timeout_s
        self.process = DecoderProcess(
            command=droneid_go_command(
                path,
                iface=iface,
                zmq_host=zmq_host,
                zmq_port=zmq_port,
                extra_args=self.extra_args,
            )
        )
        self.error: str | None = None
        self.dropped_count = 0
        # Deliberately not cleared on start()/stop()/restart: anything still
        # buffered is a real, not-yet-drained broadcast, and a restart isn't
        # meant to discard data that already arrived -- it's meant to
        # recover the subscriber thread. dropped_count is cumulative across
        # restarts for the same reason (see "Re-arm correctness" in the plan).
        self._buffer: deque[str] = deque(maxlen=queue_maxlen)
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            import zmq  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Install olab_rf with the droneid-go extra to use DroneidGoBackend"
            ) from exc
        self.process.start()
        self.error = None
        self._start_subscriber()

    def _start_subscriber(self) -> None:
        # Stop-then-start, mirroring SessionManager._start_voice_auto_poll:
        # tears down any previous thread/socket before arming a fresh one, so
        # a second start() replaces rather than duplicates the subscriber.
        self._stop_subscriber()
        stop_event = threading.Event()
        self._stop_event = stop_event
        thread = threading.Thread(
            target=self._run,
            args=(stop_event,),
            name="olab-rf-droneid-go-zmq",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _run(self, stop_event: threading.Event) -> None:
        import zmq

        # zmq.Context.instance() is the process-wide shared context -- this
        # thread never calls ctx.term(), sidestepping the
        # term()-blocks-until-every-socket-closes ordering problem entirely.
        #
        # Socket creation/connect happen *inside* the try (not before it):
        # a malformed zmq_host, or anything else that makes context.socket()/
        # connect() raise, must still be caught below rather than crashing
        # the thread with self.error left None and the socket (if partially
        # created) never closed.
        socket = None
        try:
            socket = zmq.Context.instance().socket(zmq.SUB)
            socket.setsockopt(zmq.RCVTIMEO, self.recv_timeout_ms)
            # Unfiltered subscription -- a SUB socket with no SUBSCRIBE
            # option receives nothing, silently, forever.
            socket.setsockopt_string(zmq.SUBSCRIBE, "")
            # Slow-joiner note: connecting after droneid-go has already
            # published messages means those specific messages are missed
            # permanently (no replay). Not a practical risk here since this
            # thread connects immediately after we spawn droneid-go
            # ourselves, long before any real broadcast could arrive.
            socket.connect(f"tcp://{self.zmq_host}:{self.zmq_port}")
            while not stop_event.is_set():
                try:
                    message = socket.recv_string()
                except zmq.Again:
                    # RCVTIMEO expiring with nothing to read -- the expected,
                    # every-recv_timeout_ms-when-idle case, not an error.
                    # Must be caught before zmq.ZMQError below, since
                    # zmq.Again is itself a ZMQError subclass.
                    continue
                except zmq.ZMQError as exc:
                    self.error = f"ZMQ error: {exc}"
                    break
                if len(self._buffer) == self._buffer.maxlen:
                    # About to evict the oldest buffered message. Approximate
                    # (a popleft() from poll() landing between this check and
                    # the append below can make room first, over-reporting by
                    # one) -- an upper bound on loss, not an exact count.
                    self.dropped_count += 1
                self._buffer.append(message)
        except Exception as exc:  # noqa: BLE001 -- deliberate catch-all, see below
            # Catch-all: socket setup failing (bad host, connect() raising),
            # or a non-ZMQError from inside the loop (e.g. recv_string()
            # hitting a non-UTF-8 payload and raising UnicodeDecodeError,
            # which is not a ZMQError and would otherwise fall through both
            # except clauses above). Either way this must be captured, not
            # left to kill the thread with self.error still None -- that's
            # exactly the failure backend_status() exists to surface.
            self.error = f"droneid-go subscriber failed: {exc}"
        finally:
            if socket is not None:
                socket.close(linger=0)

    def stop(self) -> None:
        self.process.stop()
        self._stop_subscriber()

    def _stop_subscriber(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.join_timeout_s)

    def is_running(self) -> bool:
        return (
            self.process.is_running()
            and self._thread is not None
            and self._thread.is_alive()
        )

    def stderr_lines(self) -> list[str]:
        return self.process.read_stderr_lines()

    def poll(self) -> list[RemoteIdBroadcast]:
        broadcasts: list[RemoteIdBroadcast] = []
        while self._buffer:
            message = self._buffer.popleft()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            broadcasts.extend(parse_droneid_go_json(payload))
        return broadcasts
