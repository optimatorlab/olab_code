from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


RTLSDR_USB_IDS = {"0bda:2832", "0bda:2838"}
DVB_MODULES = {"dvb_usb_rtl28xxu", "rtl2832", "rtl2832_sdr", "dvb_usb_v2", "dvb_core"}


@dataclass(slots=True)
class UsbDevice:
    bus: str
    device: str
    usb_id: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "bus": self.bus,
            "device": self.device,
            "usb_id": self.usb_id,
            "description": self.description,
        }


def list_usb_devices() -> list[UsbDevice]:
    try:
        result = subprocess.run(
            ["lsusb"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return parse_lsusb(result.stdout)


def parse_lsusb(output: str) -> list[UsbDevice]:
    devices: list[UsbDevice] = []
    pattern = re.compile(r"Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+(\S+)\s+(.+)")
    for line in output.splitlines():
        match = pattern.search(line)
        if match:
            devices.append(
                UsbDevice(
                    bus=match.group(1),
                    device=match.group(2),
                    usb_id=match.group(3).lower(),
                    description=match.group(4).strip(),
                )
            )
    return devices


def find_rtlsdr_usb_devices(devices: list[UsbDevice] | None = None) -> list[UsbDevice]:
    devices = devices if devices is not None else list_usb_devices()
    return [device for device in devices if device.usb_id in RTLSDR_USB_IDS]


def loaded_kernel_modules() -> set[str]:
    try:
        result = subprocess.run(
            ["lsmod"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    return parse_lsmod(result.stdout)


def parse_lsmod(output: str) -> set[str]:
    modules: set[str] = set()
    for line in output.splitlines()[1:]:
        parts = line.split()
        if parts:
            modules.add(parts[0])
    return modules


def loaded_dvb_modules(modules: set[str] | None = None) -> list[str]:
    modules = modules if modules is not None else loaded_kernel_modules()
    return sorted(module for module in modules if module in DVB_MODULES)
