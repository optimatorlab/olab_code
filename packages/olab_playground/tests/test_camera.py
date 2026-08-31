from pathlib import Path

import pytest

import olab_playground.camera as camera_module
from olab_playground.camera import PlaygroundSession, _browser_feature_output, _jsonable, _port_range, _python_call


def _session(tmp_path: Path) -> PlaygroundSession:
    return PlaygroundSession(range(9100, 9103), 8765, tmp_path / "models", tmp_path / "outputs", tmp_path)


def test_port_range_requires_valid_order():
    assert list(_port_range("8100:8101")) == [8100, 8101]
    with pytest.raises(Exception):
        _port_range("8101:8100")


def test_browse_rejects_escape_and_serializes_idle_status(tmp_path: Path):
    session = _session(tmp_path)
    assert session.status()["active"] is False
    with pytest.raises(ValueError, match="outside"):
        session.browse("models", "../outside")


def test_avwebcam_composed_device_identifiers_are_excluded(tmp_path: Path):
    class FakeCamera:
        device = "/dev/video7"

    class FakeMic:
        deviceID = 12

    class FakeAVWebcam:
        camera = FakeCamera()
        mic = FakeMic()

    session = _session(tmp_path)
    session._backend = "AVWebcam"
    session._camera = FakeAVWebcam()
    assert session._active_identifiers_locked() == {"/dev/video7", "12"}


def test_schema_exposes_known_feature_choices(tmp_path: Path):
    choices = {row["name"]: row["choices"] for row in _session(tmp_path).schema()["features"]["addTracker"]}
    assert choices["algorithm"] == ("sort", "bytetrack", "ocsort", "botsort")


def test_camera_backend_device_param_is_not_a_cpu_gpu_dropdown(tmp_path: Path):
    # CHOICES["device"] means CPU/GPU for addUltralytics/addRFDETR, but a camera
    # backend's own "device" parameter is a video source path -- regression
    # test for the collision this surfaced when CameraBosonDual (the first
    # generic-form backend with a "device" parameter) hit it in practice.
    backends = _session(tmp_path).schema()["backends"]
    for name in ("CameraUSB", "CameraBosonDual"):
        constructor_device = next(p for p in backends[name]["constructor"] if p["name"] == "device")
        assert constructor_device["choices"] is None
        start_device = next(p for p in backends[name]["start"] if p["name"] == "device")
        assert start_device["choices"] is None
    ultralytics_device = next(p for p in _session(tmp_path).schema()["features"]["addUltralytics"] if p["name"] == "device")
    assert ultralytics_device["choices"] == ("cpu", "gpu")


def test_schema_exposes_guided_aruco_choices(tmp_path: Path):
    aruco = _session(tmp_path).schema()["aruco"]
    assert "DICT_APRILTAG_36h11" in aruco["dictionaries"]
    assert aruco["callbacks"]["log_detections"]["args"]["includeCenters"]["default"] is True


def test_aruco_request_normalization_uses_safe_structured_values(tmp_path: Path):
    class FakeCamera:
        aruco = {}

    session = _session(tmp_path)
    session._camera = FakeCamera()
    kwargs, note = session._prepare_aruco_kwargs({
        "idName": "DICT_4X4_50",
        "ids_of_interest": [],
        "configOverrides": {"borderColor": [1, 2, 3], "borderDraw": False},
        "playgroundCallback": "log_detections",
        "playgroundCallbackArgs": {"includeCenters": False},
    })
    assert kwargs["ids_of_interest"] is None
    assert kwargs["configOverrides"] == {"borderColor": (1, 2, 3), "borderDraw": False}
    assert kwargs["postFunctionArgs"]["includeCenters"] is False
    assert kwargs["postFunctionArgs"]["_playground_camera"] is session._camera
    assert "includeCenters=False" in note


def test_schema_reports_missing_ros_extra(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(camera_module.importlib.util, "find_spec", lambda _name: None)
    spec = _session(tmp_path).schema()["backends"]["CameraROS"]
    assert spec["available"] is False
    assert spec["hint"] == 'pip install "olab-camera[ros]"'


def test_status_serializes_numpy_and_generated_cleanup(tmp_path: Path):
    session = _session(tmp_path)
    session._camera = object()
    session._calls = ["camera = Fake()"]
    assert _jsonable({"value": camera_module.np.int64(4)}) == {"value": 4}
    assert session.status()["python"].endswith("camera.stop()")


def test_starting_replacement_stops_previous_session(monkeypatch, tmp_path: Path):
    created = []

    class FakeBackend:
        def __init__(self, **_kwargs):
            self.stopped = False
            created.append(self)

        def start(self, **_kwargs):
            pass

        def stop(self):
            self.stopped = True

    monkeypatch.setitem(camera_module.BACKENDS, "FakeBackend", FakeBackend)
    session = _session(tmp_path)
    session.start("FakeBackend", {}, {}, {"enabled": False})
    session.start("FakeBackend", {}, {}, {"enabled": False})
    assert created[0].stopped is True
    assert session._camera is created[1]


def test_stream_ports_are_reserved_without_reuse(tmp_path: Path):
    session = _session(tmp_path)
    first = session._next_port_locked()
    second = session._next_port_locked()
    assert (first, second) == (9100, 9101)


def test_stream_port_finder_uses_allowed_range(monkeypatch, tmp_path: Path):
    session = _session(tmp_path)
    calls = []
    monkeypatch.setattr(camera_module.olab_utils, "findOpenPort", lambda preferred, options: calls.append((preferred, list(options))) or 9101)
    assert session.find_port(9100) == 9101
    assert calls == [(9100, [9100, 9101, 9102])]


def test_certificate_picker_only_returns_directories(tmp_path: Path):
    folder = tmp_path / "certs"
    folder.mkdir()
    (folder / "ca.crt").write_text("cert")
    (folder / "ca.key").write_text("key")
    (tmp_path / "not-a-directory").write_text("nope")
    rows = _session(tmp_path).browse_certificates(str(tmp_path))
    assert rows == [{"name": "certs", "path": str(folder), "hasPair": True}]


def test_successful_calibration_saves_constructor_compatible_profile(tmp_path: Path):
    session = _session(tmp_path)
    feature = type("Feature", (), {"deque": [{"state": "success", "resolution": "640x480", "matrix": [[1.0, 0.0, 2.0], [0.0, 3.0, 4.0], [0.0, 0.0, 1.0]], "dist": [[0.1, 0.2, 0.3, 0.4, 0.5]]}]})()
    session._features["addCalibrate:default"] = feature
    saved = session.save_calibration("usb-calibration")
    assert saved["intrinsics"]["640x480"]["fx"] == 1.0
    assert saved["intrinsics"]["640x480"]["dist"] == [0.1, 0.2, 0.3, 0.4, 0.5]
    assert session.intrinsics_profiles() == [{"name": "usb-calibration", "intrinsics": saved["intrinsics"]}]


def test_python_equivalent_uses_named_arguments_not_dict_expansion():
    assert _python_call("camera.start", {}) == "camera.start()"
    assert _python_call("camera = CameraUSB", {"device": "/dev/video0"}) == "camera = CameraUSB(\n    device='/dev/video0'\n)"


def test_port_fallback_only_uses_finder_when_enabled(monkeypatch, tmp_path: Path):
    session = _session(tmp_path)
    monkeypatch.setattr(session, "find_port", lambda _preferred: 9101)
    assert session._reserve_port_locked(9100, allow_fallback=True) == 9101
    assert 9101 in session._used_ports


def test_used_preferred_port_falls_back_when_enabled(monkeypatch, tmp_path: Path):
    session = _session(tmp_path)
    session._used_ports.add(9100)
    calls = []
    monkeypatch.setattr(camera_module.olab_utils, "findOpenPort", lambda preferred, options: calls.append((preferred, list(options))) or 9101)
    assert session._reserve_port_locked(9100, allow_fallback=True) == 9101
    assert calls == [(9101, [9101, 9102])]


def test_discover_returns_recommended_and_secondary_v4l2(monkeypatch, tmp_path: Path):
    session = _session(tmp_path)
    monkeypatch.setattr(session, "_discover_v4l2", lambda _active: (
        [{"path": "/dev/video0", "label": "Webcam", "aliases": []}],
        [{"path": "/dev/video1", "label": "Webcam metadata", "aliases": []}],
    ))
    discovered = session.discover()
    assert discovered["v4l2"][0]["path"] == "/dev/video0"
    assert discovered["otherV4L2"][0]["path"] == "/dev/video1"


def test_discover_boson_dual_configures_the_scan_default_does_not(monkeypatch, tmp_path: Path):
    # discover_boson_dual() exists because the default scan's bare,
    # unconfigured open+read never produces a frame from this capture
    # dongle at all (confirmed against real hardware) -- it must open the
    # same way CameraBosonDual itself does (backend/FOURCC/resolution) and
    # retry, while the default scan (used by every other backend) must open
    # exactly as it always has.
    session = _session(tmp_path)
    calls = []

    def fake_discover_v4l2(active, retry_seconds=0.0, api_pref=None, fourcc=None, probe_res=None):
        calls.append({"retry_seconds": retry_seconds, "api_pref": api_pref, "fourcc": fourcc, "probe_res": probe_res})
        return ([], [])

    monkeypatch.setattr(session, "_discover_v4l2", fake_discover_v4l2)
    session.discover_boson_dual()
    session.discover()
    assert calls[0] == {
        "retry_seconds": 4.0,
        "api_pref": camera_module.cv2.CAP_V4L2,
        "fourcc": camera_module._BOSONDUAL_PROBE_FOURCC,
        "probe_res": camera_module._BOSONDUAL_PROBE_RESOLUTION,
    }
    assert calls[1] == {"retry_seconds": 0.0, "api_pref": None, "fourcc": None, "probe_res": None}


def test_read_frame_with_retry_gives_a_slow_device_time_to_lock_on(tmp_path: Path):
    session = _session(tmp_path)

    class SlowCapture:
        def __init__(self, ready_after: int):
            self.reads = 0
            self.ready_after = ready_after

        def read(self):
            self.reads += 1
            return (True, "frame") if self.reads >= self.ready_after else (False, None)

    # Default (retry_seconds=0) preserves the original single-shot behavior.
    assert session._read_frame_with_retry(SlowCapture(ready_after=2), retry_seconds=0.0) == (False, None)
    # A real retry window gives a slow-to-lock-on device a chance to succeed.
    assert session._read_frame_with_retry(SlowCapture(ready_after=3), retry_seconds=1.0, poll_interval=0.01) == (True, "frame")


def test_read_frame_with_retry_tolerates_a_read_exception(tmp_path: Path):
    # Confirmed against real hardware: this capture dongle's first ~3 reads
    # after open throw a real cv2.error (not just ok=False) while its MJPEG
    # decoder locks onto a clean keyframe. An exception must count as a
    # failed attempt and be retried, not abort the whole scan.
    session = _session(tmp_path)

    class FlakyCapture:
        def __init__(self, raises_first: int):
            self.reads = 0
            self.raises_first = raises_first

        def read(self):
            self.reads += 1
            if self.reads <= self.raises_first:
                raise RuntimeError("simulated transient decode error")
            return True, "frame"

    assert session._read_frame_with_retry(FlakyCapture(raises_first=2), retry_seconds=1.0, poll_interval=0.01) == (True, "frame")
    # Default (retry_seconds=0) still gives up after the first exception, same as ok=False.
    assert session._read_frame_with_retry(FlakyCapture(raises_first=2), retry_seconds=0.0) == (False, None)


def test_guided_detector_rejects_model_outside_local_root(tmp_path: Path):
    session = _session(tmp_path)
    session._camera = object()
    with pytest.raises(ValueError, match="existing file"):
        session._prepare_guided_detector("addUltralytics", {
            "idName": "detect", "model_name": "../outside.pt", "color": [0, 255, 255],
        })


def test_tracker_payload_uses_exact_public_field_names(tmp_path: Path):
    session = _session(tmp_path)
    payload = session._tracker_payload("addRFDETR", {
        "xyxy": [[1, 2, 3, 4]], "class": ["person"], "class_id": [0],
        "class_conf": [0.9], "masks": ["mask"],
    })
    assert payload == {"xyxy": [[1, 2, 3, 4]], "class": ["person"], "class_id": [0], "class_conf": [0.9], "masks": ["mask"]}


def test_schema_filters_local_models_by_task_and_rfdetr_variant(monkeypatch, tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    for name in ("yolo11n.pt", "yolo26n.pt", "yolo26n.onnx", "yolo11n-seg.pt", "yolo11n-cls.pt", "rf-detr-small.pth", "rf-detr-seg-small.pt", "unrelated.pt"):
        (models / name).touch()
    monkeypatch.setattr(camera_module.importlib.util, "find_spec", lambda _name: object())
    spec = _session(tmp_path).schema()["models"]
    assert spec["ultralytics"]["tasks"]["detect"] == ["yolo11n.pt", "yolo26n.onnx", "yolo26n.pt"]
    assert spec["ultralytics"]["tasks"]["track"] == ["yolo11n.pt", "yolo26n.onnx", "yolo26n.pt"]
    assert spec["ultralytics"]["tasks"]["segment"] == ["yolo11n-seg.pt"]
    assert spec["ultralytics"]["tasks"]["classify"] == ["yolo11n-cls.pt"]
    assert spec["rfdetr"]["detect"]["small"] == ["rf-detr-small.pth"]
    assert spec["rfdetr"]["segment"]["small"] == ["rf-detr-seg-small.pt"]


def test_ultralytics_preflight_rejects_missing_dependency_and_mismatched_task(monkeypatch, tmp_path: Path):
    session = _session(tmp_path)
    session._camera = object()
    monkeypatch.setattr(camera_module.importlib.util, "find_spec", lambda _name: None)
    with pytest.raises(RuntimeError, match="Ultralytics is not installed"):
        session._prepare_guided_detector("addUltralytics", {"idName": "detect", "model_name": "yolo11n.pt", "color": [0, 255, 255]})
    models = tmp_path / "models"
    models.mkdir()
    (models / "yolo11n-seg.pt").touch()
    monkeypatch.setattr(camera_module.importlib.util, "find_spec", lambda _name: object())
    with pytest.raises(ValueError, match="compatible"):
        session._prepare_guided_detector("addUltralytics", {"idName": "detect", "model_name": "yolo11n-seg.pt", "color": [0, 255, 255]})


def test_guided_detector_devices_are_explicit_local_pytorch_values(monkeypatch, tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "yolo11n.pt").touch()
    (models / "rf-detr-small.pth").touch()
    session = _session(tmp_path)
    session._camera = object()
    monkeypatch.setattr(camera_module.importlib.util, "find_spec", lambda _name: object())
    ultra, _note = session._prepare_guided_detector("addUltralytics", {
        "idName": "detect", "model_name": "yolo11n.pt", "color": [0, 255, 255],
    })
    rfdetr, _note = session._prepare_guided_detector("addRFDETR", {
        "idName": "local", "task": "detect", "model_variant": "small",
        "weights_path": "rf-detr-small.pth", "color": [0, 255, 255],
    })
    assert ultra["device"] == rfdetr["device"] == "cpu"
    rfdetr, _note = session._prepare_guided_detector("addRFDETR", {
        "idName": "local", "task": "detect", "model_variant": "small",
        "weights_path": "rf-detr-small.pth", "color": [0, 255, 255], "device": "cuda:0",
    })
    assert rfdetr["device"] == "cuda:0"
    with pytest.raises(ValueError, match="cpu or cuda:0"):
        session._prepare_guided_detector("addUltralytics", {
            "idName": "detect", "model_name": "yolo11n.pt", "color": [0, 255, 255], "device": "gpu",
        })


def test_tag_tracker_payloads_normalize_boxes_and_preserve_callback(tmp_path: Path):
    session = _session(tmp_path)
    session._camera = type("Camera", (), {})()
    feature = type("Feature", (), {"deque": [{"ids": [7], "centers": [], "corners": [[[8, 4], [2, 9], [1, 3], [7, 1]]]}]})()
    session._camera.aruco = {"DICT_4X4_50": feature}
    session._features["addAruco:DICT_4X4_50"] = feature
    forwarded = []
    session._camera.updateTrackers = lambda payload, names: forwarded.append((payload, names))
    session._features["addTracker:shared"] = type("Tracker", (), {"isThreadActive": True})()
    kwargs, _note = session._prepare_aruco_kwargs({"idName": "DICT_4X4_50", "playgroundCallback": "log_detections", "playgroundCallbackArgs": {"includeCenters": True}, "playgroundTrackers": ["shared"]})
    kwargs["postFunction"](kwargs["postFunctionArgs"])
    assert forwarded == [({"xyxy": [[1.0, 1.0, 8.0, 9.0]], "class": ["7"], "class_conf": [1.0], "class_id": [7]}, ("shared",))]
    assert session._feature_outputs["addAruco:DICT_4X4_50"] == {"ids": [7], "centers": []}
    assert session._tracker_payload("addQR", {"data": ["payload"], "corners": [[[4, 8], [1, 2], [6, 3], [3, 9]] ]}) == {"xyxy": [[1.0, 2.0, 6.0, 9.0]], "class": ["payload"], "class_conf": [1.0]}
    assert session._tracker_payload("addBarcode", {"codeTypes": ["QRCODE"], "corners": [[(8, 4), (2, 9)]]}) == {"xyxy": [[2.0, 4.0, 8.0, 9.0]], "class": ["QRCODE"], "class_conf": [1.0]}


def test_tag_tracker_payload_omits_empty_or_malformed_entries(tmp_path: Path):
    session = _session(tmp_path)
    assert session._tracker_payload("addAruco", {"ids": None, "corners": []}) == {"xyxy": []}
    assert session._tracker_payload("addAruco", {
        "ids": [3, "bad"], "corners": [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
    }) == {"xyxy": [[1.0, 2.0, 3.0, 4.0]], "class": ["3"], "class_conf": [1.0], "class_id": [3]}
    assert session._tracker_payload("addQR", {
        "data": ["one", "two"], "corners": [[[1, 2], [3, 4]], "not corners"],
    }) == {"xyxy": [[1.0, 2.0, 3.0, 4.0]], "class": ["one"], "class_conf": [1.0]}


def test_browser_feature_output_compacts_segmentation_without_mutating_tracker_input(tmp_path: Path):
    ultra_masks = [camera_module.np.ones((2, 3), dtype=camera_module.np.float32)]
    ultra = {"class": ["person"], "xyxy": [[1, 2, 3, 4]], "masks_data": ultra_masks,
             "masks_xy": [camera_module.np.array([[1, 2], [3, 4]])]}
    rfdetr_masks = [camera_module.np.ones((2, 3), dtype=camera_module.np.float32)]
    rfdetr = {"class": ["person"], "xyxy": [[1, 2, 3, 4]], "masks": rfdetr_masks,
              "detections": object()}
    session = _session(tmp_path)
    ultra_feature = type("Feature", (), {"deque": [ultra], "isThreadActive": True})()
    rfdetr_feature = type("Feature", (), {"deque": [rfdetr], "isThreadActive": True})()
    session._features = {"addUltralytics:segment": ultra_feature, "addRFDETR:segment": rfdetr_feature}

    status = session.status()["features"]
    assert status["addUltralytics:segment"]["latest"] == {"class": ["person"], "xyxy": [[1, 2, 3, 4]], "maskCount": 1, "masksOmitted": True}
    assert status["addRFDETR:segment"]["latest"] == {"class": ["person"], "xyxy": [[1, 2, 3, 4]], "maskCount": 1, "masksOmitted": True, "detectionsOmitted": True}
    assert "masks_data" in ultra and "masks_xy" in ultra and "masks" in rfdetr and "detections" in rfdetr
    forwarded = []
    camera = type("Camera", (), {"ultralytics": {"segment": ultra_feature}, "updateTrackers": lambda _self, payload, names: forwarded.append((payload, names))})()
    camera_module._playground_detector_callback({"_playground_session": session, "_playground_camera": camera, "_feature_key": "addUltralytics:segment", "_store": "ultralytics", "_id": "segment", "_source": "addUltralytics", "_report": True, "_trackers": ("shared",)})
    assert session._feature_outputs["addUltralytics:segment"] == {"class": ["person"], "xyxy": [[1, 2, 3, 4]], "maskCount": 1, "masksOmitted": True}
    assert forwarded == [({"xyxy": [[1, 2, 3, 4]], "class": ["person"], "masks": ultra_masks}, ("shared",))]


def test_browser_feature_output_compacts_report_callback_and_preserves_no_mask_shape(tmp_path: Path):
    masks = [camera_module.np.ones((2, 3), dtype=camera_module.np.float32)]
    feature = type("Feature", (), {"deque": [{"class": ["person"], "masks": masks, "detections": object()}]})()
    camera = type("Camera", (), {"rfdetr": {"segment": feature}})()
    session = _session(tmp_path)
    session._features["addRFDETR:segment"] = feature
    camera_module._report_detections({"_playground_camera": camera, "_store": "rfdetr", "_id": "segment", "_playground_session": session, "_feature_key": "addRFDETR:segment"})
    assert session._feature_outputs["addRFDETR:segment"] == {"class": ["person"], "maskCount": 1, "masksOmitted": True, "detectionsOmitted": True}
    assert _browser_feature_output({"xyxy": [[1, 2, 3, 4]], "masks_data": [], "masks_xy": []}) == {"xyxy": [[1, 2, 3, 4]], "masks_data": [], "masks_xy": []}
    assert _browser_feature_output({"xyxy": [[1, 2, 3, 4]], "masks": []}) == {"xyxy": [[1, 2, 3, 4]], "masks": []}


def test_playground_catalog_disables_pi_and_gazebo_backends(tmp_path: Path):
    session = _session(tmp_path)
    backends = session.schema()["backends"]
    for backend in ("CameraGazebo", "CameraPi", "CameraPi2"):
        assert backends[backend]["available"] is False
        assert backends[backend]["hint"] == "disabled in this playground"
        with pytest.raises(ValueError, match="disabled in this playground"):
            session.start(backend, {}, {}, {"enabled": False})


def test_realsense_guide_defaults_and_cross_field_preflight(tmp_path: Path):
    session = _session(tmp_path)
    guide = session.schema()["guidedBackends"]["realsense"]
    assert guide["color"] == {"res_rows": 480, "res_cols": 640, "fps_target": 30}
    init, start = session._prepare_realsense({"enableDepth": True, "streamSource": "depth", "depth_color_scheme": 2}, {})
    assert init["paramDict"]["res_cols"] == 640
    assert start == {}
    with pytest.raises(ValueError, match="requires depth"):
        session._prepare_realsense({"streamSource": "depth"}, {})
    with pytest.raises(ValueError, match="color scheme requires depth preview"):
        session._prepare_realsense({"enableDepth": True, "streamSource": "color", "depth_color_scheme": 2}, {})


def test_openmv_guide_only_accepts_matching_profile_configuration(tmp_path: Path):
    session = _session(tmp_path)
    guide = session.schema()["guidedBackends"]["openmv"]
    assert set(guide) == {"genx_histogram_preview", "genx_histogram_regions", "genx_raw_events"}
    init, start = session._prepare_openmv({"devicePort": " /dev/ttyACM0 ", "profile": "genx_histogram_regions", "profile_kwargs": {"histogram_rate_hz": 100, "report_rate_hz": 25}}, {"res_rows": 320, "res_cols": 320})
    assert init["devicePort"] == "/dev/ttyACM0"
    assert init["profile_kwargs"] == {"histogram_rate_hz": 100, "report_rate_hz": 25}
    assert start == {"res_rows": 320, "res_cols": 320}
    with pytest.raises(ValueError, match="supported OpenMV profile"):
        session._prepare_openmv({"devicePort": "/dev/ttyACM0", "profile": "other", "profile_kwargs": {}}, {})
    with pytest.raises(ValueError, match="profile settings are invalid"):
        session._prepare_openmv({"devicePort": "/dev/ttyACM0", "profile": "genx_raw_events", "profile_kwargs": {"contrast": 1}}, {})
    with pytest.raises(ValueError, match="local /dev/ serial path"):
        session._prepare_openmv({"devicePort": "tcp://openmv.local", "profile": "genx_raw_events", "profile_kwargs": {}}, {})
    with pytest.raises(ValueError, match="owned by the selected profile"):
        session._prepare_openmv({"devicePort": "/dev/ttyACM0", "profile": "genx_histogram_preview", "profile_kwargs": {}}, {"res_rows": 640})


def test_camera_boson_dual_registered_with_resolution_help(tmp_path: Path):
    assert camera_module.BACKENDS["CameraBosonDual"] is camera_module.CameraBosonDual
    backend_schema = _session(tmp_path).schema()["backends"]["CameraBosonDual"]
    assert backend_schema["available"] is True
    assert backend_schema["hint"] is None
    resolution_param = next(p for p in backend_schema["constructor"] if p["name"] == "resolution")
    assert resolution_param["help"] == camera_module.CAMERA_USB_HELP["resolution"]


def test_camera_usb_subclass_gets_camon_guard(monkeypatch, tmp_path: Path):
    class FakeCameraUSB(camera_module.CameraUSB):
        def start(self, **_kwargs):
            pass  # leaves self.camOn False (set by Camera.__init__), simulating a failed open

    monkeypatch.setitem(camera_module.BACKENDS, "FakeCameraUSB", FakeCameraUSB)
    session = _session(tmp_path)
    with pytest.raises(RuntimeError, match="did not start"):
        session.start("FakeCameraUSB", {}, {}, {"enabled": False})


def test_camera_usb_subclass_gets_selected_certificate(monkeypatch, tmp_path: Path):
    received = []

    class FakeCameraUSB(camera_module.CameraUSB):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            received.append(self.sslPath)

        def start(self, **_kwargs):
            pass

    monkeypatch.setitem(camera_module.BACKENDS, "FakeCameraUSB", FakeCameraUSB)
    cert_dir = tmp_path / "chosen-cert"
    cert_dir.mkdir()
    (cert_dir / "ca.crt").write_text("cert")
    (cert_dir / "ca.key").write_text("key")

    session = _session(tmp_path)
    with pytest.raises(RuntimeError, match="did not start"):
        session.start("FakeCameraUSB", {}, {}, {"enabled": True, "certificateMode": "choose", "certificatePath": str(cert_dir)})

    assert len(received) == 1
    assert Path(received[0]).resolve() == cert_dir.resolve()
    assert received[0] != session.camera_ssl_path


def test_backend_renderer_dispatches_to_guided_realsense_and_openmv_cards():
    source = (Path(__file__).parents[1] / "src" / "olab_playground" / "static" / "app.js").read_text()
    styles = (Path(__file__).parents[1] / "src" / "olab_playground" / "static" / "styles.css").read_text()
    assert "function realSenseForm()" in source
    assert "function openMVForm()" in source
    assert "name === 'CameraRealSense'" in source
    assert "name === 'CameraOpenMV'" in source
    assert "<option ${spec.available ? '' : 'disabled'}>" in source
    assert 'id="rs-imu" type="checkbox" disabled' in source
    assert "$('rs-imu-options').hidden = !$('rs-imu').checked" in source
    assert "[hidden] { display: none !important; }" in styles


def test_backend_renderer_dispatches_to_guided_bosondual_card():
    source = (Path(__file__).parents[1] / "src" / "olab_playground" / "static" / "app.js").read_text()
    assert "function cameraBosonDualForm()" in source
    assert "name === 'CameraBosonDual'" in source
    # The render-dispatch string alone would not catch cameraInit()/cameraStart() silently
    # falling through to the generic path -- assert the actual serialization calls too.
    assert "if (name === 'CameraBosonDual') return bosonDualInit(requireSource);" in source
    assert "name === 'CameraBosonDual' ? bosonDualStart()" in source
    # Guided-form-specific UI reused from CameraUSB's established pattern.
    assert "listEditor('bd-allow'" in source
    assert "listEditor('bd-block'" in source
    assert 'id="bd-source"' in source


def test_bosondual_guide_exposes_resolution_presets(tmp_path: Path):
    guide = _session(tmp_path).schema()["guidedBackends"]["bosonDual"]
    assert guide["resolutions"] == ("720p60", "1080p60")
