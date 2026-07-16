from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class RtlSdrDevice:
    index: int
    name: str
    serial: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "name": self.name, "serial": self.serial}


@dataclass(slots=True)
class RtlSdrProbe:
    devices: list[RtlSdrDevice]
    errors: list[str]
    tuner: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "devices": [device.to_dict() for device in self.devices],
            "errors": self.errors,
            "tuner": self.tuner,
        }


def list_rtlsdr_devices(rtl_test_path: str = "rtl_test") -> list[str]:
    try:
        result = subprocess.run(
            [rtl_test_path, "-t"],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    output = "\n".join([result.stdout, result.stderr])
    return [line.strip() for line in output.splitlines() if "Found" in line or "Serial" in line]


def probe_rtlsdr_devices(rtl_test_path: str = "rtl_test") -> list[RtlSdrDevice]:
    return probe_rtlsdr(rtl_test_path).devices


def probe_rtlsdr(rtl_test_path: str = "rtl_test") -> RtlSdrProbe:
    try:
        result = subprocess.run(
            [rtl_test_path, "-t"],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return RtlSdrProbe(devices=[], errors=[f"{rtl_test_path} not available"])
    output = "\n".join([result.stdout, result.stderr])
    devices: list[RtlSdrDevice] = []
    errors: list[str] = []
    tuner: str | None = None
    for line in output.splitlines():
        match = re.search(r"Found\s+(\d+)\s+device\(s\):", line)
        if match and match.group(1) == "0":
            return RtlSdrProbe(devices=[], errors=[])
        if "usb_open error" in line or "Failed to open" in line:
            errors.append(line.strip())
        tuner_match = re.search(r"Found\s+(.+?)\s+tuner", line)
        if tuner_match:
            tuner = tuner_match.group(1).strip()
        device_match = re.search(r"^\s*(\d+):\s+(.+?)(?:,\s*SN:\s*(\S+))?\s*$", line)
        if device_match and _is_sane_device_line(device_match.group(2), device_match.group(3)):
            devices.append(
                RtlSdrDevice(
                    index=int(device_match.group(1)),
                    name=device_match.group(2).strip(),
                    serial=device_match.group(3),
                )
            )
    return RtlSdrProbe(devices=devices, errors=errors, tuner=tuner)


def _is_sane_device_line(name: str, serial: str | None) -> bool:
    values = [name, serial or ""]
    return all("\ufffd" not in value and value.isprintable() for value in values)
