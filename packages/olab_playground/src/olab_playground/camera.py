"""Camera integration for the general :mod:`olab_playground` package.

This is deliberately a small, dependency-free control plane.  It owns one
camera session and serves static browser assets; it is not a NATS service, a
Node application, or a generic Python execution endpoint.
"""

from __future__ import annotations

import argparse
import ipaddress
import inspect
import importlib.util
import json
import math
import os
import re
import secrets
import socket
import ssl
import subprocess
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
import olab_utils
import cv2

from olab_camera import (
    AVWebcam,
    CameraBosonDual,
    CameraGazebo,
    CameraOpenMV,
    CameraPi,
    CameraPi2,
    CameraROS,
    CameraRealSense,
    CameraUSB,
    CameraWebSocket,
)
from olab_camera.tls import ensure_local_cert, generate_self_signed_cert


BACKENDS = {
    "CameraUSB": CameraUSB,
    "CameraBosonDual": CameraBosonDual,
    "CameraPi": CameraPi,
    "CameraPi2": CameraPi2,
    "CameraGazebo": CameraGazebo,
    "CameraROS": CameraROS,
    "CameraRealSense": CameraRealSense,
    "CameraWebSocket": CameraWebSocket,
    "CameraOpenMV": CameraOpenMV,
    "AVWebcam": AVWebcam,
}
DISABLED_PLAYGROUND_BACKENDS = {"CameraGazebo", "CameraPi", "CameraPi2"}
BOSONDUAL_GUIDE = {
    "resolutions": ("720p60", "1080p60"),
}
# Matches CameraBosonDual's own defaults (resolution='720p60', fourcc=('M','J','P','G'),
# apiPref=cv2.CAP_V4L2) -- discovery must open/configure the capture dongle the same way
# CameraBosonDual does, or it never produces a frame at all (confirmed against real
# hardware; see discover_boson_dual()). Kept as separate literals, not imported from
# camera_boson_dual.py, matching this file's existing precedent for guided-form-owned
# domain knowledge (REALSENSE_GUIDE/OPENMV_GUIDE) -- update by hand if those defaults change.
_BOSONDUAL_PROBE_RESOLUTION = (720, 1280, 60)  # (res_rows, res_cols, fps)
_BOSONDUAL_PROBE_FOURCC = ("M", "J", "P", "G")
REALSENSE_GUIDE = {
    "color": {"res_rows": 480, "res_cols": 640, "fps_target": 30},
    "depth": {"res_rows": None, "res_cols": None, "framerate": None},
    "streamSources": ("color", "depth"),
    "depthColorSchemes": tuple(range(10)),
}
OPENMV_GUIDE = {
    "genx_histogram_preview": {
        "label": "Histogram preview",
        "capabilities": ("frame preview",),
        "resolution": (320, 320),
        "rateKey": "histogram_rate_hz",
        "rateLabel": "Histogram rate (Hz)",
        "settings": {
            "histogram_rate_hz": {"default": 50, "minimum": 20, "maximum": 350},
            "baseline_brightness": {"default": 128, "minimum": 0, "maximum": 255},
            "contrast": {"default": 16, "minimum": 1},
            "bias_preset": {"default": "default", "choices": ("default", "low_light", "active_marker", "low_noise", "high_speed")},
            "anti_flicker": {"default": "off", "choices": ("off", "50hz", "60hz")},
            "hot_pixel_calibration": {"default": "off", "choices": ("off", "auto")},
            "display_palette": {"default": "grayscale", "choices": ("grayscale", "turbo")},
        },
    },
    "genx_histogram_regions": {
        "label": "Histogram movement regions",
        "capabilities": ("frame preview", "movement regions"),
        "resolution": (320, 320),
        "rateKey": "histogram_rate_hz",
        "rateLabel": "Histogram rate (Hz)",
        "settings": {
            "histogram_rate_hz": {"default": 100, "minimum": 20, "maximum": 350},
            "report_rate_hz": {"default": 25, "minimum": 1, "maximum": 100},
            "pixels_threshold": {"default": 20, "minimum": 1},
            "area_threshold": {"default": 9, "minimum": 1},
            "max_regions": {"default": 3, "minimum": 1},
            "display_palette": {"default": "grayscale", "choices": ("grayscale", "turbo")},
        },
    },
    "genx_raw_events": {
        "label": "Raw events",
        "capabilities": ("raw events", "preview frames"),
        "resolution": (320, 320),
        "rateKey": "preview_rate_hz",
        "rateLabel": "Preview rate (Hz)",
        "settings": {
            "preview_rate_hz": {"default": 30, "minimum": 1, "maximum": 120},
            "preview_enabled": {"default": True, "type": "boolean"},
            "event_buffer_size": {"default": 8192, "choices": (1024, 2048, 4096, 8192, 16384, 32768, 65536)},
            "callback_queue_size": {"default": 8, "minimum": 1},
        },
    },
}
FEATURES = (
    "addAruco", "addQR", "addBarcode", "addCalibrate", "addFaceDetect",
    "addROI", "addTimelapse", "addUltralytics", "addRFDETR", "addTracker",
    "addCircle", "addText",
)
PYTHON_ONLY = {"logger", "pubCamStatusFunction", "postFunction", "postFunctionArgs",
               "postPostFunction", "rs_module", "device_class", "device_kwargs"}
STRUCTURED = {"paramDict", "profile_kwargs", "ids_of_interest", "configOverrides",
              "color", "roiBB", "pattern_size", "camera_kwargs", "mic_kwargs"}
FEATURE_STORES = {
    "addAruco": "aruco", "addQR": "qr", "addBarcode": "barcode",
    "addCalibrate": "calibrate", "addFaceDetect": "facedetect", "addROI": "roi",
    "addTimelapse": "timelapse", "addUltralytics": "ultralytics",
    "addRFDETR": "rfdetr", "addTracker": "trackers",
}
CHOICES = {
    "protocol": ("mjpeg", "websocket", "webrtc"),
    "algorithm": ("sort", "bytetrack", "ocsort", "botsort"),
    "task": ("detect", "segment"),
    "model_variant": ("nano", "small", "medium", "large"),
    "device": ("cpu", "gpu"),
    "streamSource": ("color", "depth"),
    "decoder": ("cv2", "pyzbar"),
}
# CHOICES["device"] means CPU/GPU for addUltralytics/addRFDETR, but camera
# backends (CameraUSB, CameraBosonDual) have their own "device" parameter
# meaning a video source path -- never offer the CPU/GPU dropdown for those.
BACKEND_SCHEMA_EXCLUDED_CHOICES = frozenset({"device"})
OPTIONAL_HINTS = {
    "CameraRealSense": ("pyrealsense2", "realsense"),
    "CameraOpenMV": ("openmv", "openmv"),
    "CameraROS": ("rospy", "ros"),
    "AVWebcam": ("olab_audio", "av"),
    "websocket": ("websockets", "websocket"),
    "webrtc": ("aiortc", "webrtc"),
}
CAMERA_USB_HELP = {
    "device": "Video source path or URL. Common local values: /dev/video0 or /dev/video1; RTSP/HTTP URLs also work.",
    "res_rows": "Optional processing height. Leave blank to use the camera's current resolution.",
    "res_cols": "Optional processing width. Leave blank to use the camera's current resolution.",
    "fps_target": "Requested capture FPS. Common values: 15, 30, or 60; hardware may negotiate a different value.",
    "framerate": "Optional start-time FPS override. Leave unset to use the camera setting.",
    "apiPref": "OpenCV capture backend. CAP_ANY is usually right; use CAP_V4L2 for Linux USB cameras only when needed.",
    "sslPath": "Directory containing ca.crt and ca.key for HTTPS/WSS stream serving.",
    "resolution": "Board output preset -- '720p60' or '1080p60'. Must match what the RHP-BOS-DS-IF board is already configured to output (via its Windows GUI/SBUS); this setting only tells the capture dongle what to request, it does not configure the board.",
    "imgTopic": "ROS raw Image publishing topic; only relevant when ROS initialization is enabled.",
    "compImgTopic": "ROS CompressedImage publishing topic; only relevant when ROS initialization is enabled.",
    "ipAllowlist": "Optional IP addresses allowed to view the stream.",
    "ipBlocklist": "IP addresses denied from viewing the stream.",
    "pattern_size": "Checkerboard interior corners as (columns, rows). A board with 7 by 9 squares has 6 by 8 interior corners, so enter [6, 8].",
    "square_size": "Physical size of one checkerboard square, in meters. 0.0254 is one inch.",
}
ARUCO_CALLBACKS = {
    "none": {"label": "No callback", "args": {}},
    "log_detections": {
        "label": "Log detected marker IDs",
        "args": {"includeCenters": {"default": True, "type": "boolean"}},
    },
    "camera_pose": {"label": "Report camera-frame tag pose", "args": {"tagSize": {"default": 1.0, "type": "number"}, "unit": {"default": "inches", "type": "choice"}, "outputUnit": {"default": "meters", "type": "choice"}}},
}
DETECTION_CALLBACKS = {"none", "report_detections"}
GUIDED_CALLBACKS = {"none", "report_results"}
ULTRALYTICS_TASK_SUFFIXES = {
    "detect": "", "segment": "-seg", "classify": "-cls",
    "pose": "-pose", "obb": "-obb", "track": "",
}
RFDETR_SUFFIXES = {"detect": ("rf-detr-", ".pth"), "segment": ("rf-detr-seg-", ".pt")}


def _ultralytics_model_task(value: str) -> str | None:
    """Return the supported task family for one local YOLO checkpoint name."""
    match = re.fullmatch(r"yolo(?:11|26)[a-z0-9]*?(?:-(seg|cls|pose|obb))?\.(?:pt|onnx)", Path(value).name.lower())
    if match is None:
        return None
    return {None: "detect", "seg": "segment", "cls": "classify", "pose": "pose", "obb": "obb"}[match.group(1)]


def _rfdetr_checkpoint_name(task: str, variant: str) -> str:
    prefix, suffix = RFDETR_SUFFIXES[task]
    return f"{prefix}{variant}{suffix}"


def _log_aruco_detections(args: dict[str, Any]) -> None:
    """Locally log the latest ArUco IDs; selected only through the UI catalog."""
    camera = args["_playground_camera"]
    result = camera.aruco[args["idName"]].deque[-1]
    output = {"ids": _jsonable(result['ids'])}
    if args.get("includeCenters"):
        output["centers"] = _jsonable(result['centers'])
    args["_playground_session"]._publish_feature_output(args["_feature_key"], output)


def _aruco_camera_pose(args: dict[str, Any]) -> None:
    camera, session = args["_playground_camera"], args["_playground_session"]
    resolution = args["_resolution"]
    intrinsics = camera.intrinsics.get(resolution)
    if intrinsics is None:
        session._publish_feature_output(args["_feature_key"], {"error": f"No intrinsics for {resolution}"})
        return
    size_m = args["tagSize"] * (0.0254 if args["unit"] == "inches" else 1.0)
    result = camera.aruco[args["idName"]].deque[-1]
    poses = olab_utils.findTagPoses(result["corners"], result["ids"], size_m, intrinsics["matrix"], intrinsics["dist"])
    session._publish_feature_output(args["_feature_key"], {"resolution": resolution, "tagSizeMeters": size_m, "outputUnit": args["outputUnit"], "poses": _jsonable(poses)})


def _report_detections(args: dict[str, Any]) -> None:
    camera = args["_playground_camera"]
    feature = getattr(camera, args["_store"])[args["_id"]]
    args["_playground_session"]._publish_feature_output(args["_feature_key"], _browser_feature_output(feature.deque[-1]))


def _playground_detector_callback(args: dict[str, Any]) -> None:
    """Publish a bounded detector snapshot and/or forward it to named trackers."""
    session, camera = args["_playground_session"], args["_playground_camera"]
    store, identifier = args["_store"], args["_id"]
    try:
        result = getattr(camera, store)[identifier].deque[-1]
    except (AttributeError, KeyError, IndexError):
        return
    if args.get("_report"):
        session._publish_feature_output(args["_feature_key"], _browser_feature_output(result))
    trackers = args.get("_trackers", ())
    if trackers:
        payload = session._tracker_payload(args["_source"], result)
        camera.updateTrackers(payload, trackers)


def _playground_feature_callback(args: dict[str, Any]) -> None:
    """Compose a fixed browser callback with optional safe tracker forwarding."""
    callback = args.get("_callback")
    if callback is not None:
        callback(args)
    trackers = args.get("_trackers", ())
    if trackers:
        camera = args["_playground_camera"]
        store, identifier = args["_store"], args["_id"]
        try:
            result = getattr(camera, store)[identifier].deque[-1]
        except (AttributeError, KeyError, IndexError):
            return
        camera.updateTrackers(args["_playground_session"]._tracker_payload(args["_source"], result), trackers)


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (deque, list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    if value is inspect.Parameter.empty:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _browser_feature_output(value: Any) -> Any:
    """Return a compact browser view without detector pixel arrays/objects."""
    if not isinstance(value, dict):
        return _jsonable(value)
    output = dict(value)
    mask_items = next((output[key] for key in ("masks_data", "masks", "masks_xy")
                       if key in output and output[key] is not None and len(output[key])), None)
    if mask_items is not None:
        output.pop("masks_data", None)
        output.pop("masks_xy", None)
        output.pop("masks", None)
        output["maskCount"] = len(mask_items)
        output["masksOmitted"] = True
    detections = output.pop("detections", None)
    if detections is not None:
        output["detectionsOmitted"] = True
    return _jsonable(output)


def _schema(callable_object: Any, exclude_choices: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Describe a callable without ever evaluating untrusted user input.

    `exclude_choices` suppresses `CHOICES` lookups for specific parameter
    names on this call only. `CHOICES` is keyed by bare parameter name and
    shared across every schema `_schema()` builds (camera backends and
    feature methods alike) -- e.g. `CHOICES["device"]` means CPU/GPU for
    `addUltralytics`/`addRFDETR`, but a camera backend's own `device`
    parameter (a video source path) must never render that dropdown. Pass
    the colliding names here for a given call site rather than changing
    `CHOICES` itself, which other, non-colliding call sites still rely on.
    """
    rows = []
    for parameter in inspect.signature(callable_object).parameters.values():
        if parameter.name in {"self", "args", "kwargs"} or parameter.kind in {
            parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD,
        }:
            continue
        default = None if parameter.default is inspect.Parameter.empty else _jsonable(parameter.default)
        rows.append({
            "name": parameter.name,
            "required": parameter.default is inspect.Parameter.empty,
            "default": default,
            "structured": parameter.name in STRUCTURED,
            "pythonOnly": parameter.name in PYTHON_ONLY,
            "choices": None if parameter.name in exclude_choices else CHOICES.get(parameter.name),
            "annotation": str(parameter.annotation) if parameter.annotation is not inspect.Parameter.empty else "",
            "help": CAMERA_USB_HELP.get(parameter.name, "See the olab_camera API documentation for this argument."),
        })
    return rows


def _literal(value: Any) -> str:
    return repr(value)


def _python_call(target: str, kwargs: dict[str, Any]) -> str:
    """Render a readable public-API call, rather than a dict expansion."""
    if not kwargs:
        return f"{target}()"
    arguments = ",\n".join(f"    {name}={_literal(value)}" for name, value in kwargs.items())
    return f"{target}(\n{arguments}\n)"


class PlaygroundSession:
    """One logical camera/AVWebcam session, guarded as a lifecycle unit."""

    def __init__(self, stream_ports: range, control_port: int, model_root: Path, output_root: Path,
                 camera_ssl_path: Path):
        self._lock = threading.RLock()
        self._ports = [port for port in stream_ports if port != control_port]
        self._used_ports: set[int] = set()
        self._camera: Any = None
        self._backend: str | None = None
        self._features: dict[str, Any] = {}
        self._feature_outputs: dict[str, Any] = {}
        self._decorations: dict[str, int] = {}
        self._calls: list[str] = []
        self._last_error: str | None = None
        self.model_root, self.output_root = model_root.resolve(), output_root.resolve()
        self.calibration_root = self.output_root / "calibrations"
        self.camera_ssl_path = str(camera_ssl_path)

    def schema(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"backends": {}, "features": {}, "primaryIP": olab_utils.getIP(), "streamPort": self._ports[0] if self._ports else None, "streamPortRange": f"{self._ports[0]}–{self._ports[-1]}" if self._ports else "unavailable"}
        for name, cls in BACKENDS.items():
            dependency = OPTIONAL_HINTS.get(name)
            disabled = name in DISABLED_PLAYGROUND_BACKENDS
            available = not disabled and (dependency is None or importlib.util.find_spec(dependency[0]) is not None)
            hint = "disabled in this playground" if disabled else (None if available else f'pip install "olab-camera[{dependency[1]}]"')
            payload["backends"][name] = {"constructor": _schema(cls, exclude_choices=BACKEND_SCHEMA_EXCLUDED_CHOICES), "start": _schema(getattr(cls, "start", lambda: None), exclude_choices=BACKEND_SCHEMA_EXCLUDED_CHOICES), "available": available, "hint": hint}
        for name in FEATURES:
            payload["features"][name] = _schema(getattr(CameraUSB, name))
        payload["aruco"] = {
            "dictionaries": sorted(olab_utils.ARUCO_DICT),
            "drawingDefaults": _jsonable(olab_utils.ARUCO_DRAWING_DEFAULTS),
            "callbacks": ARUCO_CALLBACKS,
        }
        ultralytics_available = importlib.util.find_spec("ultralytics") is not None
        ultra_models = {task: [] for task in ULTRALYTICS_TASK_SUFFIXES}
        for path in self._local_model_paths((".pt", ".onnx")):
            task = _ultralytics_model_task(path)
            if task is not None:
                ultra_models[task].append(path)
                if task == "detect":
                    ultra_models["track"].append(path)
        rfdetr_models = {task: {variant: [] for variant in CHOICES["model_variant"]} for task in RFDETR_SUFFIXES}
        for path in self._local_model_paths((".pth", ".pt")):
            for task in RFDETR_SUFFIXES:
                for variant in CHOICES["model_variant"]:
                    if Path(path).name == _rfdetr_checkpoint_name(task, variant):
                        rfdetr_models[task][variant].append(path)
        payload["models"] = {
            "ultralytics": {"available": ultralytics_available, "hint": 'pip install "olab-camera[yolo]"', "tasks": ultra_models},
            "rfdetr": rfdetr_models,
            "face": ["face_detection_yunet_2023mar.onnx", "face_detection_yunet_2023mar_int8.onnx"],
        }
        payload["guidedBackends"] = {"realsense": REALSENSE_GUIDE, "openmv": OPENMV_GUIDE, "bosonDual": BOSONDUAL_GUIDE}
        payload["callbacks"] = {"detector": sorted(GUIDED_CALLBACKS)}
        payload["stream"] = _schema(CameraUSB.startStream)
        payload["protocols"] = {name: {"available": importlib.util.find_spec(module) is not None, "hint": f'pip install "olab-camera[{extra}]"'} for name, (module, extra) in OPTIONAL_HINTS.items() if name in {"websocket", "webrtc"}}
        return payload

    def _local_model_paths(self, suffixes: tuple[str, ...]) -> list[str]:
        if not self.model_root.is_dir():
            return []
        return [str(path.relative_to(self.model_root)) for path in sorted(self.model_root.rglob("*"))
                if path.is_file() and path.suffix.lower() in suffixes]

    def _local_model_path(self, value: Any, suffixes: tuple[str, ...]) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("choose an existing local model")
        path = (self.model_root / value).resolve()
        if self.model_root not in path.parents or not path.is_file() or path.suffix.lower() not in suffixes:
            raise ValueError("model must be an existing file under the configured local model root")
        return str(path)

    @staticmethod
    def _positive_int(value: Any, name: str, *, required: bool = False) -> int | None:
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    def _prepare_realsense(self, init: dict[str, Any], start: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        allowed_init = {"paramDict", "serial_number", "enableDepth", "enableIMU", "streamSource",
                        "depth_res_rows", "depth_res_cols", "depth_framerate", "alignDepthToColor",
                        "enableDepthFilters", "depth_color_scheme", "imu_accel_rate", "imu_gyro_rate"}
        allowed_start = {"res_rows", "res_cols", "framerate", "depth_res_rows", "depth_res_cols", "depth_framerate"}
        if not set(init).issubset(allowed_init) or not set(start).issubset(allowed_start):
            raise ValueError("unsupported RealSense playground setting")
        init, start = dict(init), dict(start)
        param_dict = init.get("paramDict", {})
        if not isinstance(param_dict, dict) or set(param_dict) - {"res_rows", "res_cols", "fps_target", "outputPort"}:
            raise ValueError("RealSense color settings are invalid")
        color = {**REALSENSE_GUIDE["color"], **param_dict}
        for name in ("res_rows", "res_cols", "fps_target"):
            self._positive_int(color[name], f"color {name}", required=True)
        init["paramDict"] = color
        serial = init.get("serial_number")
        if serial is not None and (not isinstance(serial, str) or not serial.strip()):
            raise ValueError("RealSense serial number must be blank or a nonempty string")
        if serial is not None:
            init["serial_number"] = serial.strip()
        for name in ("enableDepth", "enableIMU", "alignDepthToColor", "enableDepthFilters"):
            if name in init and not isinstance(init[name], bool):
                raise ValueError(f"RealSense {name} must be true or false")
        source = init.get("streamSource", "color")
        if source not in REALSENSE_GUIDE["streamSources"]:
            raise ValueError("choose color or depth preview")
        if source == "depth" and not init.get("enableDepth", False):
            raise ValueError("depth preview requires depth to be enabled")
        scheme = init.get("depth_color_scheme")
        if scheme is not None:
            if source != "depth" or scheme not in REALSENSE_GUIDE["depthColorSchemes"]:
                raise ValueError("depth color scheme requires depth preview")
        for name in ("depth_res_rows", "depth_res_cols", "depth_framerate"):
            if name in init:
                self._positive_int(init[name], name)
            if name in start:
                self._positive_int(start[name], name)
        for name in ("imu_accel_rate", "imu_gyro_rate"):
            if name in init:
                self._positive_int(init[name], name)
                if not init.get("enableIMU", False):
                    raise ValueError(f"{name} requires IMU to be enabled")
        return init, start

    def _prepare_openmv(self, init: dict[str, Any], start: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if set(init) - {"devicePort", "profile", "profile_kwargs"} or set(start) - {"res_rows", "res_cols", "framerate"}:
            raise ValueError("unsupported OpenMV playground setting")
        init, start = dict(init), dict(start)
        port = init.get("devicePort")
        if not isinstance(port, str) or not port.strip():
            raise ValueError("OpenMV device path must be a nonempty string")
        port = port.strip()
        if not port.startswith("/dev/") or "\x00" in port:
            raise ValueError("OpenMV device path must be a local /dev/ serial path")
        init["devicePort"] = port
        profile_name = init.get("profile")
        profile = OPENMV_GUIDE.get(profile_name)
        if profile is None:
            raise ValueError("choose a supported OpenMV profile")
        kwargs = init.get("profile_kwargs", {})
        if not isinstance(kwargs, dict) or set(kwargs) - set(profile["settings"]):
            raise ValueError("OpenMV profile settings are invalid")
        cleaned = {}
        for name, value in kwargs.items():
            spec = profile["settings"][name]
            if spec.get("type") == "boolean":
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be true or false")
            elif "choices" in spec:
                if value not in spec["choices"]:
                    raise ValueError(f"{name} is not supported by the selected profile")
            else:
                self._positive_int(value, name, required=True)
                if value < spec.get("minimum", 1) or value > spec.get("maximum", value):
                    raise ValueError(f"{name} is outside the supported range")
            cleaned[name] = value
        if profile_name == "genx_histogram_regions" and cleaned.get("report_rate_hz", profile["settings"]["report_rate_hz"]["default"]) > cleaned.get("histogram_rate_hz", profile["settings"]["histogram_rate_hz"]["default"]):
            raise ValueError("report rate cannot exceed histogram rate")
        for name, value in start.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"OpenMV {name} must be an integer")
            expected = profile["resolution"][0 if name == "res_rows" else 1] if name in {"res_rows", "res_cols"} else None
            if expected is None or value != expected:
                raise ValueError(f"OpenMV {name} is owned by the selected profile")
        init["profile_kwargs"] = cleaned
        return init, start

    @staticmethod
    def _color(kwargs: dict[str, Any]) -> None:
        color = kwargs.get("color")
        if not isinstance(color, list) or len(color) != 3 or any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255 for value in color):
            raise ValueError("drawing color must be three BGR values from 0 to 255")
        kwargs["color"] = tuple(color)

    def _tracker_payload(self, source: str, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"xyxy": []}
        if source in {"addAruco", "addQR", "addBarcode"}:
            raw_labels = result.get("ids") if source == "addAruco" else result.get("data") if source == "addQR" else result.get("codeTypes")
            if raw_labels is None:
                return {"xyxy": []}
            labels = np.asarray(raw_labels).reshape(-1).tolist() if source == "addAruco" else list(raw_labels)
            boxes, matched_labels = [], []
            for corners, label in zip(result.get("corners", []), labels):
                if source == "addAruco":
                    try:
                        label = int(label)
                    except (TypeError, ValueError):
                        continue
                try:
                    points = np.asarray(corners, dtype=float).reshape(-1, 2)
                except (TypeError, ValueError):
                    continue
                if points.size and np.isfinite(points).all():
                    boxes.append([float(points[:, 0].min()), float(points[:, 1].min()), float(points[:, 0].max()), float(points[:, 1].max())])
                    matched_labels.append(label)
            if not boxes:
                return {"xyxy": []}
            payload = {"xyxy": boxes, "class": [str(label) for label in matched_labels], "class_conf": [1.0] * len(boxes)}
            if source == "addAruco":
                payload["class_id"] = matched_labels
            return payload
        payload = {"xyxy": result.get("xyxy", [])}
        for output, input_name in (("class", "class"), ("class_id", "class_id"), ("class_conf", "class_conf")):
            if input_name in result:
                payload[output] = result[input_name]
        if source == "addUltralytics" and result.get("masks_data"):
            payload["masks"] = result["masks_data"]
        elif result.get("masks"):
            payload["masks"] = result["masks"]
        return payload

    def _prepare_guided_detector(self, name: str, kwargs: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        kwargs = dict(kwargs)
        callback = kwargs.pop("playgroundCallback", "none")
        tracker_names = kwargs.pop("playgroundTrackers", [])
        if callback not in GUIDED_CALLBACKS or not isinstance(tracker_names, list) or any(not isinstance(item, str) for item in tracker_names):
            raise ValueError("choose supported local callback and tracker options")
        self._color(kwargs)
        identifier = kwargs.get("idName", "default")
        store = FEATURE_STORES[name]
        if name == "addFaceDetect":
            model_path = kwargs.get("modelPath")
            if model_path is not None:
                directory = (self.model_root / model_path).resolve()
                if self.model_root not in directory.parents or not directory.is_dir():
                    raise ValueError("custom face model directory must be under the local model root")
                kwargs["modelPath"] = str(directory)
            if kwargs.get("model_name") not in {"face_detection_yunet_2023mar.onnx", "face_detection_yunet_2023mar_int8.onnx"}:
                raise ValueError("choose a bundled YuNet face model")
        elif name == "addUltralytics":
            if importlib.util.find_spec("ultralytics") is None:
                raise RuntimeError('Ultralytics is not installed; install with pip install "olab-camera[yolo]" before starting this feature')
            task = kwargs.get("idName")
            if task not in {"detect", "segment", "classify", "pose", "obb", "track"}:
                raise ValueError("choose a supported Ultralytics task")
            if task == "track" and tracker_names:
                raise ValueError("Ultralytics native track cannot also forward to a playground tracker")
            if kwargs.get("device", "cpu") not in {"cpu", "cuda:0"}:
                raise ValueError("Ultralytics device must be cpu or cuda:0")
            kwargs.setdefault("device", "cpu")
            model_name = self._local_model_path(kwargs.get("model_name"), (".pt", ".onnx"))
            if _ultralytics_model_task(model_name) != ("detect" if task == "track" else task):
                raise ValueError("choose a local YOLO checkpoint compatible with the selected task")
            kwargs["model_name"] = model_name
        elif name == "addRFDETR":
            if kwargs.get("tracker") and tracker_names:
                raise ValueError("RF-DETR built-in ByteTrack cannot also forward to a playground tracker")
            task, variant = kwargs.get("task"), kwargs.get("model_variant")
            if task not in RFDETR_SUFFIXES or variant not in CHOICES["model_variant"]:
                raise ValueError("choose a supported RF-DETR task and model variant")
            weights_path = kwargs.get("weights_path")
            if Path(str(weights_path)).name != _rfdetr_checkpoint_name(task, variant):
                raise ValueError("choose the local checkpoint matching the selected RF-DETR task and model variant")
            if kwargs.get("device", "cpu") not in {"cpu", "cuda:0"}:
                raise ValueError("RF-DETR device must be cpu or cuda:0")
            kwargs.setdefault("device", "cpu")
            kwargs["weights_path"] = self._local_model_path(weights_path, (".pth", ".pt"))
        if tracker_names:
            with self._lock:
                missing = [item for item in tracker_names if f"addTracker:{item}" not in self._features or not self._features[f"addTracker:{item}"].isThreadActive]
            if missing:
                raise ValueError(f"selected tracker is not active: {', '.join(missing)}")
        if callback != "none" or tracker_names:
            key = f"{name}:{identifier}"
            kwargs["postFunction"] = _playground_detector_callback
            kwargs["postFunctionArgs"] = {"_playground_session": self, "_playground_camera": self._camera, "_feature_key": key, "_store": store, "_id": identifier, "_source": name, "_report": callback == "report_results", "_trackers": tuple(tracker_names)}
            return kwargs, "# Playground callback: browser-visible detector result" if callback != "none" else "# Playground callback: forward detections to local tracker"
        return kwargs, None

    def _prepare_tracker_kwargs(self, kwargs: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        kwargs = dict(kwargs)
        callback = kwargs.pop("playgroundCallback", "none")
        if callback not in GUIDED_CALLBACKS:
            raise ValueError("choose a supported local tracker callback")
        identifier = kwargs.get("idName")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("tracker name is required")
        self._color(kwargs)
        if callback == "report_results":
            kwargs["postFunction"] = _playground_detector_callback
            kwargs["postFunctionArgs"] = {"_playground_session": self, "_playground_camera": self._camera, "_feature_key": f"addTracker:{identifier}", "_store": "trackers", "_id": identifier, "_source": "addTracker", "_report": True, "_trackers": ()}
            return kwargs, "# Playground callback: browser-visible tracker result"
        return kwargs, None

    def _stop_locked(self) -> None:
        camera = self._camera
        for feature in list(self._features.values()):
            try:
                getattr(feature, "stop")()
            except Exception:
                pass
        self._features.clear()
        self._feature_outputs.clear()
        self._decorations.clear()
        if camera is not None:
            try:
                if hasattr(camera, "stop"):
                    camera.stop()
                elif hasattr(camera, "shutdown"):
                    camera.shutdown()
            except Exception as exc:  # best-effort teardown must not strand state
                self._last_error = f"stop failed: {exc}"
        self._camera = None
        self._backend = None
        self._calls.clear()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _next_port_locked(self) -> int:
        for port in self._ports:
            if port in self._used_ports:
                continue
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.bind(("", port))
            except OSError:
                continue
            finally:
                probe.close()
            self._used_ports.add(port)
            return port
        raise RuntimeError("no unused stream ports remain in --stream-port-range; restart with another range")

    def find_port(self, preferred: int) -> int:
        """Return an available permitted port without reserving it."""
        with self._lock:
            candidates = [port for port in self._ports if port not in self._used_ports]
            if preferred not in candidates:
                preferred = candidates[0] if candidates else preferred
            port = olab_utils.findOpenPort(preferred, options=candidates)
            if port is None or port not in candidates:
                raise RuntimeError("no open port remains in --stream-port-range")
            return port

    def _reserve_port_locked(self, preferred: int | None, allow_fallback: bool) -> int:
        candidates = [port for port in self._ports if port not in self._used_ports]
        if not candidates:
            raise RuntimeError("no unused stream ports remain in --stream-port-range")
        preferred = preferred if preferred is not None else candidates[0]
        if not allow_fallback and preferred not in candidates:
            raise RuntimeError("preferred port is outside the configured stream-port range or was already used")
        selected = self.find_port(preferred) if allow_fallback else preferred
        if not allow_fallback:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.bind(("", selected))
            except OSError as exc:
                raise RuntimeError("preferred port is busy; enable automatic port selection or choose another port") from exc
            finally:
                probe.close()
        self._used_ports.add(selected)
        return selected

    def browse_certificates(self, path: str = "/") -> list[dict[str, Any]]:
        """List directories only for the loopback-only certificate picker."""
        candidate = Path(path).expanduser().resolve()
        if not candidate.is_dir():
            raise ValueError("certificate path is not a directory")
        rows = []
        for entry in sorted(candidate.iterdir()):
            if entry.is_dir():
                rows.append({"name": entry.name, "path": str(entry), "hasPair": (entry / "ca.crt").is_file() and (entry / "ca.key").is_file()})
        return rows

    @staticmethod
    def _certificate_path(stream: dict[str, Any], default: str) -> str:
        mode = stream.get("certificateMode", "current")
        if mode == "current":
            return default
        if mode == "choose":
            path = Path(stream.get("certificatePath", "")).expanduser().resolve()
            if not ((path / "ca.crt").is_file() and (path / "ca.key").is_file()):
                raise ValueError("selected certificate directory must contain ca.crt and ca.key")
            return str(path)
        if mode == "generate":
            advertised = str(stream.get("advertisedHost") or olab_utils.getIP())
            safe_name = "".join(character if character.isalnum() or character in ".-" else "_" for character in advertised)
            path = Path(default).parent / f"playground-stream-{safe_name}"
            try:
                ipaddress.ip_address(advertised)
                kwargs = {"ip_addresses": [advertised]}
            except ValueError:
                kwargs = {"dns_names": [advertised]}
            generate_self_signed_cert(path / "ca.crt", path / "ca.key", common_name=advertised, **kwargs)
            return str(path)
        raise ValueError("certificate mode must be current, generate, or choose")

    def start(self, backend: str, init: dict[str, Any], start: dict[str, Any], stream: dict[str, Any]) -> None:
        if backend not in BACKENDS:
            raise ValueError(f"unknown backend: {backend}")
        if backend in DISABLED_PLAYGROUND_BACKENDS:
            raise ValueError(f"backend is disabled in this playground: {backend}")
        with self._lock:
            self._stop_locked()
            self._last_error = None
            try:
                cls = BACKENDS[backend]
                init = dict(init)
                start = dict(start)
                if backend == "CameraRealSense":
                    init, start = self._prepare_realsense(init, start)
                elif backend == "CameraOpenMV":
                    init, start = self._prepare_openmv(init, start)
                if backend != "AVWebcam" and "sslPath" not in init:
                    init["sslPath"] = self.camera_ssl_path
                if issubclass(cls, CameraUSB) and stream.get("enabled"):
                    init["sslPath"] = self._certificate_path(stream, self.camera_ssl_path)
                camera = cls(**init)
                self._camera, self._backend = camera, backend
                self._calls = [f"from olab_camera import {backend}", _python_call(f"camera = {backend}", init)]
                if hasattr(camera, "start"):
                    camera.start(**start)
                    self._calls.append(_python_call("camera.start", start))
                if isinstance(camera, CameraUSB) and not camera.camOn:
                    raise RuntimeError("CameraUSB did not start; check the selected source and the technical camera log")
                if stream.get("enabled"):
                    port = self._reserve_port_locked(stream.get("port"), bool(stream.get("autoPort", True)))
                    protocol = stream.get("protocol", "mjpeg")
                    lan_visible = bool(stream.get("lanVisible", False))
                    bind_host = "0.0.0.0" if lan_visible else "127.0.0.1"
                    advertised_host = stream.get("advertisedHost") or (olab_utils.getIP() if lan_visible else "localhost")
                    stream_camera = camera.camera if backend == "AVWebcam" else camera
                    stream_camera.startStream(port=port, protocol=protocol, bindHost=bind_host, advertisedHost=advertised_host)
                    self._calls.append(_python_call("camera.startStream", {"port": port, "protocol": protocol, "bindHost": bind_host, "advertisedHost": advertised_host}))
            except Exception as exc:
                self._last_error = str(exc)
                self._stop_locked()
                raise

    def _prepare_aruco_kwargs(self, kwargs: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        kwargs = dict(kwargs)
        dictionary = kwargs.get("idName")
        if dictionary not in olab_utils.ARUCO_DICT:
            raise ValueError("choose a supported ArUco marker dictionary")
        ids = kwargs.get("ids_of_interest")
        if ids is not None:
            if not isinstance(ids, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in ids):
                raise ValueError("IDs of interest must be a list of integer marker IDs")
            kwargs["ids_of_interest"] = ids or None
        overrides = kwargs.get("configOverrides")
        if overrides is not None:
            if not isinstance(overrides, dict) or not set(overrides).issubset(olab_utils.ARUCO_DRAWING_DEFAULTS):
                raise ValueError("drawing overrides contain an unsupported setting")
            clean_overrides = {}
            for key, value in overrides.items():
                default = olab_utils.ARUCO_DRAWING_DEFAULTS[key]
                if key.endswith("Color"):
                    if not isinstance(value, list) or len(value) != 3 or any(isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255 for channel in value):
                        raise ValueError(f"{key} must be a BGR list of three values from 0 to 255")
                    clean_overrides[key] = tuple(value)
                elif isinstance(default, bool):
                    if not isinstance(value, bool):
                        raise ValueError(f"{key} must be true or false")
                    clean_overrides[key] = value
                elif isinstance(default, (int, float)) and not isinstance(value, bool):
                    clean_overrides[key] = value
                else:
                    raise ValueError(f"{key} has an invalid value")
            kwargs["configOverrides"] = clean_overrides
        callback_name = kwargs.pop("playgroundCallback", "none")
        callback_args = kwargs.pop("playgroundCallbackArgs", {})
        tracker_names = kwargs.pop("playgroundTrackers", [])
        if (callback_name not in ARUCO_CALLBACKS or not isinstance(callback_args, dict)
                or not isinstance(tracker_names, list) or any(not isinstance(item, str) for item in tracker_names)):
            raise ValueError("choose a supported post-detection callback")
        callback_note = None
        callback = None
        callback_args_server = {"_playground_camera": self._camera, "_playground_session": self, "_feature_key": f"addAruco:{dictionary}", "idName": dictionary}
        if callback_name == "log_detections":
            include_centers = callback_args.get("includeCenters", True)
            if not isinstance(include_centers, bool):
                raise ValueError("include centers must be true or false")
            callback = _log_aruco_detections
            callback_args_server["includeCenters"] = include_centers
            callback_note = f"# Playground callback: log detected marker IDs (includeCenters={include_centers!r})"
        elif callback_name == "camera_pose":
            tag_size, unit, output_unit = callback_args.get("tagSize", 1.0), callback_args.get("unit", "inches"), callback_args.get("outputUnit", "meters")
            if isinstance(tag_size, bool) or not isinstance(tag_size, (int, float)) or not math.isfinite(tag_size) or tag_size <= 0 or unit not in {"inches", "meters"} or output_unit not in {"meters", "centimeters", "inches"}:
                raise ValueError("pose callback needs a positive tag size and inches or meters")
            rows = kwargs.get("res_rows", self._camera.res_rows)
            cols = kwargs.get("res_cols", self._camera.res_cols)
            callback = _aruco_camera_pose
            callback_args_server.update({"_resolution": f"{cols}x{rows}", "tagSize": float(tag_size), "unit": unit, "outputUnit": output_unit})
            callback_note = f"# Playground callback: camera-frame tag pose (tagSize={tag_size!r}, unit={unit!r})"
        if tracker_names:
            with self._lock:
                missing = [item for item in tracker_names if f"addTracker:{item}" not in self._features or not self._features[f"addTracker:{item}"].isThreadActive]
            if missing:
                raise ValueError(f"selected tracker is not active: {', '.join(missing)}")
        if callback is not None or tracker_names:
            callback_args_server.update({"_callback": callback, "_trackers": tuple(tracker_names), "_store": "aruco", "_id": dictionary, "_source": "addAruco"})
            kwargs["postFunction"] = _playground_feature_callback
            kwargs["postFunctionArgs"] = callback_args_server
            if callback_note is None:
                callback_note = "# Playground callback: forward ArUco detections to local tracker"
        return kwargs, callback_note

    def _prepare_detection_kwargs(self, name: str, kwargs: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        kwargs = dict(kwargs)
        callback = kwargs.pop("playgroundCallback", "none")
        tracker_names = kwargs.pop("playgroundTrackers", [])
        if (callback not in DETECTION_CALLBACKS or not isinstance(tracker_names, list)
                or any(not isinstance(item, str) for item in tracker_names)):
            raise ValueError("choose a supported detection callback")
        if "color" in kwargs:
            color = kwargs["color"]
            if not isinstance(color, list) or len(color) != 3 or any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255 for value in color):
                raise ValueError("drawing color must be three BGR values from 0 to 255")
            kwargs["color"] = tuple(color)
        identifier = kwargs.get("idName", "default")
        if name == "addQR":
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("QR detector name is required")
            ids = kwargs.get("ids_of_interest")
            if ids is not None and (not isinstance(ids, list) or any(not isinstance(value, str) for value in ids)):
                raise ValueError("QR payloads of interest must be strings")
            kwargs["ids_of_interest"] = ids or None
        if tracker_names:
            with self._lock:
                missing = [item for item in tracker_names if f"addTracker:{item}" not in self._features or not self._features[f"addTracker:{item}"].isThreadActive]
            if missing:
                raise ValueError(f"selected tracker is not active: {', '.join(missing)}")
        if callback == "report_detections" or tracker_names:
            store = FEATURE_STORES[name]
            key = f"{name}:{identifier}"
            kwargs["postFunction"] = _playground_feature_callback
            kwargs["postFunctionArgs"] = {"_playground_camera": self._camera, "_playground_session": self, "_feature_key": key, "_store": store, "_id": identifier, "_source": name, "_callback": _report_detections if callback == "report_detections" else None, "_trackers": tuple(tracker_names)}
            return kwargs, "# Playground callback: browser-visible detection report" if callback == "report_detections" else "# Playground callback: forward detections to local tracker"
        return kwargs, None

    def _publish_feature_output(self, key: str, output: dict[str, Any]) -> None:
        with self._lock:
            if key in self._features:
                self._feature_outputs[key] = _jsonable(output)

    def intrinsics_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.calibration_root.exists():
                return []
            rows = []
            for path in sorted(self.calibration_root.glob("*.json")):
                try:
                    payload = json.loads(path.read_text())
                    self._validate_intrinsics(payload)
                    rows.append({"name": path.stem, "intrinsics": payload})
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            return rows

    @staticmethod
    def _validate_intrinsics(payload: Any) -> None:
        if not isinstance(payload, dict) or not payload:
            raise ValueError("intrinsics profile must contain a resolution")
        for resolution, values in payload.items():
            if not re.fullmatch(r"\d+x\d+", resolution) or not isinstance(values, dict) or set(values) != {"fx", "fy", "cx", "cy", "dist"}:
                raise ValueError("invalid intrinsics profile")
            numbers = [values[key] for key in ("fx", "fy", "cx", "cy")] + list(values["dist"] if isinstance(values["dist"], list) else [])
            if not isinstance(values["dist"], list) or not numbers or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in numbers):
                raise ValueError("intrinsics values must be finite numbers")

    def save_calibration(self, name: str) -> dict[str, Any]:
        with self._lock:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                raise ValueError("calibration name may use letters, digits, underscores, and hyphens")
            feature = self._features.get("addCalibrate:default")
            latest = feature.deque[-1] if feature is not None and feature.deque else None
            if not isinstance(latest, dict) or latest.get("state") != "success":
                raise ValueError("a successful calibration is required before saving intrinsics")
            matrix, dist = latest["matrix"], latest["dist"]
            profile = {latest["resolution"]: {"fx": float(matrix[0][0]), "fy": float(matrix[1][1]), "cx": float(matrix[0][2]), "cy": float(matrix[1][2]), "dist": np.asarray(dist, dtype=float).reshape(-1).tolist()}}
            self._validate_intrinsics(profile)
            self.calibration_root.mkdir(parents=True, exist_ok=True)
            target = self.calibration_root / f"{name}.json"
            if target.exists():
                raise ValueError("a calibration profile with that name already exists")
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(profile, indent=2) + "\n")
            os.replace(temporary, target)
            return {"name": name, "intrinsics": profile}

    def start_feature(self, name: str, kwargs: dict[str, Any]) -> None:
        with self._lock:
            if self._camera is None or self._backend == "AVWebcam":
                raise RuntimeError("start a Camera backend before adding camera features")
            if name not in FEATURES:
                raise ValueError(f"unknown feature: {name}")
            display_kwargs = dict(kwargs)
            callback_note = None
            if name == "addAruco":
                kwargs, callback_note = self._prepare_aruco_kwargs(kwargs)
                display_kwargs.pop("playgroundCallback", None)
                display_kwargs.pop("playgroundCallbackArgs", None)
                display_kwargs.pop("playgroundTrackers", None)
            elif name in {"addQR", "addBarcode"}:
                kwargs, callback_note = self._prepare_detection_kwargs(name, kwargs)
                display_kwargs.pop("playgroundCallback", None)
                display_kwargs.pop("playgroundTrackers", None)
            elif name in {"addFaceDetect", "addUltralytics", "addRFDETR"}:
                kwargs, callback_note = self._prepare_guided_detector(name, kwargs)
                display_kwargs.pop("playgroundCallback", None)
                display_kwargs.pop("playgroundTrackers", None)
            elif name == "addTracker":
                kwargs, callback_note = self._prepare_tracker_kwargs(kwargs)
                display_kwargs.pop("playgroundCallback", None)
            elif name in {"addCircle", "addText"}:
                pair_name = "center" if name == "addCircle" else "position"
                pair = kwargs.get(pair_name)
                if (not isinstance(pair, list) or len(pair) != 2 or
                        any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in pair)):
                    raise ValueError(f"{pair_name} must be two finite pixel coordinates")
                kwargs[pair_name] = tuple(int(value) for value in pair)
                if "color" in kwargs:
                    self._color(kwargs)
            method = getattr(self._camera, name)
            result = method(**kwargs)
            if name in {"addCircle", "addText"}:
                decoration_id = result[0]
                key = f"{name}:{decoration_id}"
                self._decorations[key] = decoration_id
                self._calls.append(_python_call(f"camera.{name}", display_kwargs))
                return
            store = getattr(self._camera, FEATURE_STORES[name])
            key = kwargs.get("idName", "default")
            if name == "addTracker":
                key = kwargs.get("idName")
            feature = store.get(key)
            if feature is None:
                raise RuntimeError(f"{name} did not start; inspect the camera log and required local dependencies")
            self._features[f"{name}:{key}"] = feature
            if callback_note:
                self._calls.append(callback_note)
            self._calls.append(_python_call(f"camera.{name}", display_kwargs))

    def stop_feature(self, key: str) -> None:
        with self._lock:
            if key in self._decorations:
                self._camera.removeDecoration(self._decorations.pop(key))
                return
            feature = self._features.pop(key, None)
            self._feature_outputs.pop(key, None)
            if feature is None:
                raise KeyError(key)
            feature.stop()

    def _feature_status(self, feature: Any) -> dict[str, Any]:
        result: dict[str, Any] = {"active": bool(getattr(feature, "isThreadActive", True))}
        if hasattr(feature, "deque") and feature.deque:
            result["latest"] = _browser_feature_output(feature.deque[-1])
        return result

    def status(self) -> dict[str, Any]:
        with self._lock:
            camera = self._camera
            result = {
                "backend": self._backend,
                "active": camera is not None,
                "error": self._last_error,
                "features": {key: {**self._feature_status(value), **({"callback": self._feature_outputs[key]} if key in self._feature_outputs else {})} for key, value in self._features.items()},
                "decorations": dict(self._decorations),
                "python": "\n".join(self._calls + (["camera.stop()"] if camera is not None else [])),
            }
            if camera is not None:
                stream_camera = camera.camera if self._backend == "AVWebcam" else camera
                result["camera"] = _jsonable({
                    "camOn": getattr(stream_camera, "camOn", None),
                    "protocol": getattr(stream_camera, "activeProtocol", None),
                    "streamURL": getattr(stream_camera, "streamURL", None),
                    "streamPort": getattr(stream_camera, "streamPort", None),
                    "numStreams": getattr(stream_camera, "numStreams", None),
                    "fps": getattr(stream_camera, "fps", None),
                    "res_rows": getattr(stream_camera, "res_rows", None),
                    "res_cols": getattr(stream_camera, "res_cols", None),
                    "framerate": getattr(stream_camera, "framerate", None),
                })
            return result

    def browse(self, kind: str, relative: str = ".") -> list[dict[str, Any]]:
        root = self.model_root if kind == "models" else self.output_root if kind == "outputs" else None
        if root is None:
            raise ValueError("kind must be models or outputs")
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("path is outside the approved root")
        if not candidate.is_dir():
            raise ValueError("path is not a directory")
        return [{"name": entry.name, "path": str(entry), "directory": entry.is_dir()} for entry in sorted(candidate.iterdir())]

    def discover(self) -> dict[str, Any]:
        with self._lock:
            active = self._active_identifiers_locked()
        v4l2, other_v4l2 = self._discover_v4l2(active)
        serial = []
        try:
            from serial.tools import list_ports
            serial = [{"path": port.device, "label": port.description} for port in list_ports.comports() if port.device not in active]
        except ImportError:
            pass
        realsense = []
        try:
            import pyrealsense2 as rs
            for device in rs.context().query_devices():
                serial_number = device.get_info(rs.camera_info.serial_number)
                if serial_number not in active:
                    realsense.append({"serial": serial_number, "name": device.get_info(rs.camera_info.name)})
        except (ImportError, RuntimeError):
            pass
        return {"v4l2": v4l2, "otherV4L2": other_v4l2, "openmv": serial, "realsense": realsense, "avwebcam": {"cameras": v4l2, "microphones": []}}

    def discover_boson_dual(self) -> dict[str, Any]:
        """Separate device scan just for CameraBosonDual's guided form.

        The RHP-BOS-DS-IF board's HDMI signal reaches the host through an
        HDMI-to-USB capture dongle that (confirmed against real hardware)
        **never** produces a frame via a bare, unconfigured
        cv2.VideoCapture(path) the way discover()'s scan opens every device
        -- it only responds once opened the same way CameraBosonDual itself
        opens it (CAP_V4L2 backend, MJPG FOURCC, an explicit resolution),
        and even then the first ~3 reads throw a real cv2.error (the same
        transient decode error self-healed during hardware validation, see
        .pairwork/camera-boson-dual.md) before one succeeds, typically well
        under a second. Bare `discover()` would silently drop this device
        forever, no matter how long it retried. Kept as a separate endpoint
        (bespoke open/retry config) so the default scan -- used by every
        other backend's guided form, and known to work with a bare open --
        stays untouched.
        """
        with self._lock:
            active = self._active_identifiers_locked()
        v4l2, other_v4l2 = self._discover_v4l2(
            active, retry_seconds=4.0, api_pref=cv2.CAP_V4L2,
            fourcc=_BOSONDUAL_PROBE_FOURCC, probe_res=_BOSONDUAL_PROBE_RESOLUTION,
        )
        return {"v4l2": v4l2, "otherV4L2": other_v4l2}

    @staticmethod
    def _v4l2_is_monochrome(path: Path) -> bool:
        """Return whether V4L2 reports a single-channel capture format."""
        try:
            result = subprocess.run(["v4l2-ctl", "-d", str(path), "--all"], capture_output=True, text=True, timeout=3)
            match = re.search(r"Pixel Format\s*:\s*'(\w+)'", result.stdout)
            return bool(match and match.group(1) in {"GREY", "Y800", "Y8", "Y10", "Y12", "Y16"})
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _v4l2_group(path: Path) -> str:
        sysfs_device = Path("/sys/class/video4linux") / path.name / "device"
        try:
            return str(sysfs_device.resolve())
        except OSError:
            return str(path)

    @staticmethod
    def _read_frame_with_retry(capture: Any, retry_seconds: float = 0.0, poll_interval: float = 0.5) -> tuple[bool, Any]:
        """Read one frame from an already-opened cv2.VideoCapture, retrying
        for up to `retry_seconds` if the first read doesn't produce one.

        Tolerates `capture.read()` raising (confirmed against real hardware:
        a capture dongle mid-lock-on throws a real cv2.error on its first
        few reads, not just returns ok=False) -- an exception counts as a
        failed attempt, same as ok=False, and is retried the same way. This
        matches the tolerance the real camera capture loop (CameraUSB) has
        always had (see camera_usb.py's _captureLoop, which logs and
        continues on exactly this kind of per-frame decode error).

        Default `retry_seconds=0` preserves the original single-shot
        behavior exactly (one attempt, whether it raises or returns
        ok=False, and no `time.sleep` call).
        """
        deadline = time.monotonic() + retry_seconds
        while True:
            try:
                ok, frame = capture.read()
            except Exception:
                ok, frame = False, None
            if ok and frame is not None:
                return ok, frame
            if time.monotonic() >= deadline:
                return ok, frame
            time.sleep(poll_interval)

    def _discover_v4l2(self, active: set[str], retry_seconds: float = 0.0, api_pref: int | None = None,
                        fourcc: tuple[str, str, str, str] | None = None,
                        probe_res: tuple[int, int, int] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Probe usable color capture nodes and group secondary interfaces.

        This is intentionally user-triggered (or performed once during page
        bootstrap), never a status-refresh operation. It follows the proven
        OFM approach: a V4L2 node must open and return a frame, and reported
        monochrome/IR nodes are not suggested as ordinary color cameras.

        `retry_seconds` (default 0 -- today's original single-shot behavior):
        some sources don't deliver a frame on the very first read right after
        open (e.g. an HDMI-to-USB capture dongle still syncing to its
        incoming signal). When > 0, retry the read for up to this many
        seconds before giving up on a device, instead of failing on the
        first attempt.

        `api_pref`/`fourcc`/`probe_res` (all default `None` -- today's
        original bare-open behavior, `cv2.VideoCapture(path)` with no
        further configuration): some sources (confirmed against real
        hardware: this HDMI-to-USB capture dongle) never produce a frame via
        a bare, unconfigured open, no matter how long you retry -- they only
        respond once opened/configured the same way the real camera class
        itself would (backend, FOURCC, resolution). Pass these to match
        that configuration for scans where it's known to matter.

        All four are left at their defaults for the default scan, so
        ordinary USB webcams keep discovering exactly as before -- see
        discover_boson_dual() for the one caller that opts into a
        configured, more patient scan.
        """
        recommended, secondary, seen_groups = [], [], set()
        for device in sorted(Path("/dev").glob("video*")):
            if str(device) in active:
                continue
            capture = None
            try:
                capture = cv2.VideoCapture(str(device), api_pref) if api_pref is not None else cv2.VideoCapture(str(device))
                if not capture.isOpened():
                    continue
                if probe_res is not None:
                    res_rows, res_cols, fps = probe_res
                    capture.set(cv2.CAP_PROP_FRAME_WIDTH, res_cols)
                    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, res_rows)
                    capture.set(cv2.CAP_PROP_FPS, fps)
                if fourcc is not None:
                    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*fourcc))
                ok, frame = self._read_frame_with_retry(capture, retry_seconds)
                if not ok or frame is None or self._v4l2_is_monochrome(device):
                    continue
                name_file = Path("/sys/class/video4linux") / device.name / "name"
                label = name_file.read_text().strip() if name_file.exists() else str(device)
                aliases = [str(path) for path in Path("/dev/v4l/by-id").glob("*") if path.resolve() == device.resolve()] if Path("/dev/v4l/by-id").exists() else []
                entry = {"path": str(device), "label": label or str(device), "aliases": aliases}
                group = self._v4l2_group(device)
                (secondary if group in seen_groups else recommended).append(entry)
                seen_groups.add(group)
            except Exception:
                continue
            finally:
                if capture is not None:
                    capture.release()
        return recommended, secondary

    def _active_identifiers_locked(self) -> set[str]:
        camera = self._camera
        if camera is None:
            return set()
        values = {getattr(camera, name, None) for name in ("device", "devicePort", "serial_number")}
        if self._backend == "AVWebcam":
            values.add(getattr(getattr(camera, "camera", None), "device", None))
            values.add(getattr(getattr(camera, "mic", None), "deviceID", None))
        return {str(value) for value in values if value not in (None, "")}


def _assets_path(name: str) -> Path:
    return Path(__file__).with_name("static") / name


def make_handler(session: PlaygroundSession, csrf_token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "olab-camera-playground/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            route = urlparse(self.path).path
            if route == "/":
                return self._asset("index.html", "text/html; charset=utf-8", replace_token=True)
            if route in {"/app.js", "/styles.css"}:
                return self._asset(route[1:], "application/javascript" if route.endswith("js") else "text/css")
            if route == "/api/schema":
                return self._json(session.schema())
            if route == "/api/status":
                return self._json(session.status())
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            try:
                self._require_csrf()
                body = self._body()
                route = urlparse(self.path).path
                if route == "/api/camera/start":
                    session.start(body["backend"], body.get("init", {}), body.get("start", {}), body.get("stream", {}))
                    return self._json(session.status())
                if route == "/api/camera/stop":
                    session.stop()
                    return self._json(session.status())
                if route == "/api/feature/start":
                    session.start_feature(body["name"], body.get("kwargs", {}))
                    return self._json(session.status())
                if route == "/api/feature/stop":
                    session.stop_feature(body["key"])
                    return self._json(session.status())
                if route == "/api/intrinsics/list":
                    return self._json(session.intrinsics_profiles())
                if route == "/api/intrinsics/save":
                    return self._json(session.save_calibration(body["name"]))
                if route == "/api/discover":
                    return self._json(session.discover())
                if route == "/api/discover-boson-dual":
                    return self._json(session.discover_boson_dual())
                if route == "/api/browse":
                    return self._json(session.browse(body["kind"], body.get("path", ".")))
                if route == "/api/certificates/browse":
                    return self._json(session.browse_certificates(body.get("path", "/")))
                self.send_error(HTTPStatus.NOT_FOUND)
            except Exception as exc:  # Hardware backends may raise bare Exception.
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def _require_csrf(self) -> None:
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError("state-changing requests must use application/json")
            origin = self.headers.get("Origin")
            expected = f"https://{self.headers.get('Host')}"
            if not origin or origin != expected or self.headers.get("X-Olab-Playground-CSRF") != csrf_token:
                raise ValueError("same-origin CSRF validation failed")

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _asset(self, name: str, content_type: str, replace_token: bool = False) -> None:
            data = _assets_path(name).read_bytes()
            if replace_token:
                data = data.replace(b"__CSRF_TOKEN__", csrf_token.encode("ascii"))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(_jsonable(payload), sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
    return Handler


def _port_range(raw: str) -> range:
    try:
        start, stop = (int(part) for part in raw.split(":", 1))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected START:STOP, e.g. 8000:8099") from exc
    if not (0 < start <= stop < 65536):
        raise argparse.ArgumentTypeError("stream ports must be in 1..65535")
    return range(start, stop + 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local HTTPS olab_camera playground.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--stream-port-range", type=_port_range, default=range(8000, 8100))
    parser.add_argument("--ssl-path", type=Path)
    parser.add_argument("--model-root", type=Path, default=Path.home() / "Projects" / "olab_models")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "Videos")
    args = parser.parse_args(argv)
    ssl_path = args.ssl_path or ensure_local_cert(Path.home() / ".olab_camera" / "ssl")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(Path(ssl_path) / "ca.crt", Path(ssl_path) / "ca.key")
    session = PlaygroundSession(args.stream_port_range, args.port, args.model_root, args.output_root, Path(ssl_path))
    host = "127.0.0.1"
    server = ThreadingHTTPServer((host, args.port), make_handler(session, secrets.token_urlsafe(32)))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"olab-camera playground listening on https://{host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
        server.server_close()
    return 0
