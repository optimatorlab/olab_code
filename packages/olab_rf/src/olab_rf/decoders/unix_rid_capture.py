from __future__ import annotations

import json

from olab_rf.decoders.process import DecoderProcess
from olab_rf.decoders.remoteid_base import (
    RemoteIdBroadcast,
    safe_float,
    valid_altitude,
    valid_course,
    valid_lat_lon,
)


def unix_rid_capture_command(
    path: str = "rid_capture",
    hci_device: str = "hci0",
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build a BLE-only unix_rid_capture invocation.

    No `-w` (WiFi interface) flag is ever passed here: this project's
    vendored build patches `CMakeLists.txt` to `set(enable_pcap 0)`, which
    compiles the WiFi/pcap code path (including the hardcoded-default
    -interface-open-or-abort behavior) out of the binary entirely, so
    `unix_rid_capture` never attempts to open any WiFi device regardless of
    flags passed here.
    """
    command = [path, "-b", hci_device]
    if extra_args:
        command.extend(extra_args)
    return command


def parse_unix_rid_capture_json(line: str) -> RemoteIdBroadcast | None:
    """Parse one unix_rid_capture stdout JSON line into a `RemoteIdBroadcast`.

    Schema grounded directly in `rid_capture.c`'s `parse_odid()`: a flat JSON
    object with keys `mac`, `rssi`, `operator`, `uav id`, `uav latitude`,
    `uav longitude`, `uav altitude`, `uav heading`, `uav speed`, `seconds`,
    `base latitude`, `base longitude`, `unix time`, `self id`. High
    confidence here since it's read from the actual (unmaintained but
    stable) upstream source, not a third party's downstream integration.
    """
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    mac = payload.get("mac")
    mac = str(mac) if mac else None
    serial = payload.get("uav id")
    serial = str(serial) if serial else None
    if mac is None and serial is None:
        return None

    lat, lon = valid_lat_lon(payload.get("uav latitude"), payload.get("uav longitude"))
    altitude_m = valid_altitude(payload.get("uav altitude"))
    speed_mps = safe_float(payload.get("uav speed"))
    course_deg = valid_course(payload.get("uav heading"))
    operator_lat = safe_float(payload.get("base latitude"))
    operator_lon = safe_float(payload.get("base longitude"))
    self_id = payload.get("self id")

    return RemoteIdBroadcast(
        backend="unix_rid_capture",
        transport="ble",
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
        raw=payload,
    )


class UnixRidCaptureBackend:
    """BLE Remote ID backend wrapping the unix_rid_capture binary.

    Owns a `DecoderProcess` (stdout JSON-lines), the same shape
    `decoders/sdrtrunk.py`'s `SdrTrunkBackend` uses for owning a subprocess
    -- not `decoders/readsb.py`/`decoders/rtl_ais.py`, which are pure
    command-builder/parse functions with no process ownership of their own.

    Known limitation: `is_running()` only reflects whether the subprocess is
    alive. On a Bluetooth 5.2 adapter the controller rejects the binary's
    legacy LE scan commands (HCI 0x0c), yet the process keeps running and
    prints `rx packets 0`, so this backend looks healthy while scanning
    nothing. See docs/install.md.
    """

    transport = "ble"
    name = "unix_rid_capture"

    def __init__(
        self,
        *,
        path: str = "rid_capture",
        hci_device: str = "hci0",
        extra_args: list[str] | None = None,
    ) -> None:
        self.path = path
        self.hci_device = hci_device
        self.extra_args = list(extra_args or [])
        self.process = DecoderProcess(
            command=unix_rid_capture_command(
                path, hci_device=hci_device, extra_args=self.extra_args
            )
        )

    def start(self) -> None:
        self.process.start()

    def stop(self) -> None:
        self.process.stop()

    def is_running(self) -> bool:
        return self.process.is_running()

    def stderr_lines(self) -> list[str]:
        return self.process.read_stderr_lines()

    def poll(self) -> list[RemoteIdBroadcast]:
        broadcasts: list[RemoteIdBroadcast] = []
        for line in self.process.read_stdout_lines():
            broadcast = parse_unix_rid_capture_json(line)
            if broadcast is not None:
                broadcasts.append(broadcast)
        return broadcasts
