from __future__ import annotations

import shutil
import os
from pathlib import Path

from olab_rf.receivers import ToolCheck
from olab_rf.receivers import check_tool, find_rtlsdr_usb_devices, loaded_dvb_modules, probe_rtlsdr


def environment_check(
    tool_paths: dict[str, str] | None = None,
    sdrtrunk_paths: dict[str, str | None] | None = None,
    skip_rtlsdr_probe_reason: str | None = None,
    config_path: str | None = None,
) -> dict[str, object]:
    tool_paths = tool_paths or {}
    sdrtrunk_paths = sdrtrunk_paths or {}
    checks = [
        _check_configured_tool("readsb", tool_paths.get("readsb")),
        _check_configured_tool("rtl_ais", tool_paths.get("rtl_ais")),
        _check_configured_tool("rtl_power", tool_paths.get("rtl_power")),
        _check_configured_tool("rtl_fm", tool_paths.get("rtl_fm")),
        _check_configured_tool("rtl_sdr", tool_paths.get("rtl_sdr")),
        check_tool("rtl_tcp"),
        check_tool("rtl_test"),
    ]
    sdrtrunk = {
        "launcher": _check_configured_tool("sdrtrunk", sdrtrunk_paths.get("launcher_path")),
        "java": _check_configured_tool("java", sdrtrunk_paths.get("java_path")),
        "working_directory": _check_path(
            sdrtrunk_paths.get("working_directory"), expect_directory=True
        ),
        "profile": _check_path(sdrtrunk_paths.get("profile_path"), expect_directory=False),
        "jmbe": _check_path(sdrtrunk_paths.get("jmbe_path"), expect_directory=None),
    }
    if skip_rtlsdr_probe_reason:
        rtlsdr_probe = _SkippedProbe(skip_rtlsdr_probe_reason)
    else:
        rtlsdr_probe = probe_rtlsdr()
    devices = rtlsdr_probe.devices
    usb_devices = find_rtlsdr_usb_devices()
    dvb_modules = loaded_dvb_modules()
    warnings = []
    serials = [device.serial for device in devices if device.serial]
    if len(serials) != len(set(serials)):
        warnings.append("Duplicate RTL-SDR serials detected; assign unique serials for stable config.")
    if usb_devices and not any(check.name == "rtl_test" and check.found for check in checks):
        warnings.append("RTL-SDR USB hardware is present, but rtl_test is not installed or not on PATH.")
    for check in checks:
        if check.found and check.executable is False:
            warnings.append(f"{check.name} exists at {check.path}, but is not executable.")
    if dvb_modules:
        warnings.append(
            "Kernel DVB modules are loaded for the RTL-SDR; SDR tools may need a blacklist setup."
        )
    if rtlsdr_probe.errors:
        warnings.extend(rtlsdr_probe.errors)
    return {
        "context": {
            "cwd": str(Path.cwd()),
            "config_path": config_path,
            "local_config_found": Path("olab_rf.yaml").exists(),
        },
        "kernel_dvb_modules": dvb_modules,
        "rtlsdr_probe": rtlsdr_probe.to_dict(),
        "rtlsdr_usb_devices": [device.to_dict() for device in usb_devices],
        "tools": [check.to_dict() for check in checks],
        "sdrtrunk": {
            "launcher": sdrtrunk["launcher"].to_dict(),
            "java": sdrtrunk["java"].to_dict(),
            "working_directory": sdrtrunk["working_directory"],
            "profile": sdrtrunk["profile"],
            "jmbe": sdrtrunk["jmbe"],
        },
        "rtlsdr_devices": [device.to_dict() for device in devices],
        "warnings": warnings,
    }


def _check_configured_tool(name: str, configured_path: str | None) -> ToolCheck:
    if not configured_path:
        return check_tool(name)
    path = Path(configured_path)
    if path.exists():
        return ToolCheck(
            name=name,
            found=True,
            path=str(path),
            executable=path.is_file() and os.access(path, os.X_OK),
        )
    resolved = shutil.which(configured_path)
    return ToolCheck(
        name=name,
        found=resolved is not None,
        path=resolved or configured_path,
        executable=Path(resolved).is_file() if resolved else None,
    )


def _check_path(path_value: str | None, *, expect_directory: bool | None) -> dict[str, object]:
    if not path_value:
        return {"configured": False, "path": None, "found": False, "valid_type": None}
    path = Path(path_value).expanduser()
    exists = path.exists()
    valid_type = (
        path.is_dir()
        if expect_directory is True
        else path.is_file()
        if expect_directory is False
        else exists
    )
    return {
        "configured": True,
        "path": str(path),
        "found": exists,
        "valid_type": valid_type if exists else False,
    }


class _SkippedProbe:
    def __init__(self, reason: str):
        self.devices = []
        self.errors = []
        self.reason = reason

    def to_dict(self) -> dict[str, object]:
        return {"devices": [], "errors": [], "tuner": None, "skipped": self.reason}
