"""Tests for CameraRealSense (Intel RealSense backend). pyrealsense2 needs
real hardware to run for real, so the SDK is entirely mocked here (injected
via the rs_module= seam) -- these tests cover our own logic (param
validation, depthDeque/imuData population, streamSource switching,
intrinsics ownership, and the failure-mode contract), not the SDK itself.
Kept minimal/high-signal per the project's stated test-thoroughness
preference -- see .pairwork/camera-realsense.md's Plan for the full,
review-hardened design this implements.

Real end-to-end validation against the D435i is manual, via
examples/realsense_hardware_test.ipynb (gitignored), not part of this suite.
"""

import subprocess
import sys
import textwrap
from unittest.mock import MagicMock

import numpy as np
import pytest

import olab_utils
from olab_camera.camera_realsense import CameraRealSense


PARAM_DICT = {'res_rows': 480, 'res_cols': 640, 'fps_target': 30, 'outputPort': 8000}


class _RecordingLogger:
    """Stub logger recording every call, so tests can assert on exactly
    what was logged without depending on the real olab_utils.Logger."""

    def __init__(self):
        self.calls = []

    def log(self, msgtext, severity=None, **kwargs):
        self.calls.append((msgtext, severity))


class _FakeIntrinsics:
    def __init__(self, width, height, fx, fy, ppx, ppy, coeffs=(0.0, 0.0, 0.0, 0.0, 0.0)):
        self.width, self.height = width, height
        self.fx, self.fy = fx, fy
        self.ppx, self.ppy = ppx, ppy
        self.coeffs = list(coeffs)


def _make_video_profile(stream_type, width, height, fps, intrinsics=None):
    profile = MagicMock()
    profile.stream_type.return_value = stream_type
    profile.fps.return_value = fps
    vprofile = MagicMock()
    vprofile.width.return_value = width
    vprofile.height.return_value = height
    vprofile.as_video_stream_profile.return_value = vprofile
    vprofile.get_intrinsics.return_value = intrinsics or _FakeIntrinsics(width, height, 600.0, 600.0, width / 2, height / 2)
    profile.as_video_stream_profile.return_value = vprofile
    return profile


def _make_motion_profile(stream_type, fps):
    profile = MagicMock()
    profile.stream_type.return_value = stream_type
    profile.fps.return_value = fps
    return profile


def _make_rs_module():
    rs = MagicMock()
    rs.stream.color = 'color'
    rs.stream.depth = 'depth'
    rs.stream.accel = 'accel'
    rs.stream.gyro  = 'gyro'
    rs.format.bgr8 = 'bgr8'
    rs.format.z16  = 'z16'
    rs.format.motion_xyz32f = 'motion_xyz32f'
    rs.camera_info.serial_number = 'serial_number'
    rs.option.color_scheme = 'color_scheme'
    return rs


def _make_device(rs, serial, profiles, depth_scale=0.001):
    device = MagicMock()
    device.get_info.side_effect = lambda info: serial if info == rs.camera_info.serial_number else None
    sensor = MagicMock()
    sensor.get_stream_profiles.return_value = profiles
    device.query_sensors.return_value = [sensor]
    depth_sensor = MagicMock()
    depth_sensor.get_depth_scale.return_value = depth_scale
    device.first_depth_sensor.return_value = depth_sensor
    return device


def _wire_pipeline(rs, device, color_stream_profile, depth_stream_profile=None):
    """Wires rs.context()/rs.pipeline() so start() succeeds against `device`,
    and returns the fake pipeline object (its wait_for_frames()/stop() are
    configured per-test)."""
    fake_context = MagicMock()
    fake_context.query_devices.return_value = [device]
    rs.context.return_value = fake_context

    fake_pipeline_profile = MagicMock()
    fake_pipeline_profile.get_device.return_value = device
    streams = {rs.stream.color: color_stream_profile}
    if depth_stream_profile is not None:
        streams[rs.stream.depth] = depth_stream_profile
    fake_pipeline_profile.get_stream.side_effect = lambda st: streams[st]

    fake_pipeline = MagicMock()
    fake_pipeline.start.return_value = fake_pipeline_profile
    rs.pipeline.return_value = fake_pipeline
    return fake_pipeline


def _make_color_frame(bgr_array):
    frame = MagicMock()
    frame.get_data.return_value = bgr_array
    return frame


def _make_depth_frame(depth_array):
    frame = MagicMock()
    frame.get_data.return_value = depth_array
    return frame


def _make_motion_frame(x, y, z, timestamp_ms):
    frame = MagicMock()
    frame.get_timestamp.return_value = timestamp_ms
    motion_frame = MagicMock()
    data = MagicMock()
    data.x, data.y, data.z = x, y, z
    motion_frame.get_motion_data.return_value = data
    frame.as_motion_frame.return_value = motion_frame
    return frame


def _run_capture_loop_once(cam, frameset):
    """Runs _captureLoop() for exactly one full iteration, then stops it on
    the *second* wait_for_frames() call -- not the first -- so a real bug in
    the loop body during the iteration under test still surfaces as a logged
    error (the loop's shutdown-noise suppression only applies once
    _capture_running has already gone False, which must not happen until
    after the iteration being tested has run)."""
    calls = {'n': 0}

    def _side_effect():
        calls['n'] += 1
        if calls['n'] == 1:
            return frameset
        cam._capture_running = False
        raise RuntimeError('stop capture loop (test helper)')

    cam.pipeline.wait_for_frames.side_effect = _side_effect
    cam._capture_running = True
    cam._captureLoop()


# ─── __init__ validation (pure input-shape errors, no hardware touched) ───

def test_init_raises_import_error_when_pyrealsense2_missing(monkeypatch):
    # Force the module-level `rs` to None regardless of whether pyrealsense2
    # actually happens to be installed in the venv running this test (e.g.
    # during real-hardware validation, where it deliberately is) -- this
    # test is about the rs_module=None-and-unresolvable code path, not about
    # this environment's actual dependency state.
    import olab_camera.camera_realsense as camera_realsense_module
    monkeypatch.setattr(camera_realsense_module, 'rs', None)
    with pytest.raises(ImportError):
        CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=None)


def test_init_accepts_injected_rs_module_with_valid_params():
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module())
    assert cam.streamSource == 'color'
    assert cam.enableDepth is False
    assert cam.enableIMU is False


@pytest.mark.parametrize('bad_source', ['bogus', 'COLOR', None, 123])
def test_init_rejects_invalid_streamSource(bad_source):
    with pytest.raises(ValueError):
        CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(), streamSource=bad_source)


def test_init_rejects_depth_streamSource_without_enableDepth():
    with pytest.raises(ValueError):
        CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(),
                         streamSource='depth', enableDepth=False)


def test_init_accepts_depth_streamSource_with_enableDepth():
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(),
                           streamSource='depth', enableDepth=True)
    assert cam.streamSource == 'depth'


@pytest.mark.parametrize('field', ['depth_res_rows', 'depth_res_cols', 'depth_framerate'])
@pytest.mark.parametrize('bad_value', [0, -1, 1.5, 'x'])
def test_init_rejects_bad_depth_dimensions(field, bad_value):
    kwargs = {field: bad_value}
    with pytest.raises(ValueError):
        CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(), **kwargs)


@pytest.mark.parametrize('bad_serial', ['', 123, 4.5])
def test_init_rejects_bad_serial_number(bad_serial):
    with pytest.raises(ValueError):
        CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(), serial_number=bad_serial)


def test_init_accepts_none_or_string_serial_number():
    CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(), serial_number=None)
    CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(), serial_number='ABC123')


@pytest.mark.parametrize('field', ['imu_accel_rate', 'imu_gyro_rate'])
@pytest.mark.parametrize('bad_value', [0, -1, 1.5, 'x'])
def test_init_rejects_bad_imu_rates(field, bad_value):
    kwargs = {field: bad_value}
    with pytest.raises(ValueError):
        CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(), **kwargs)


@pytest.mark.parametrize('bad_value', [-1, 10, 1.5, 'x'])
def test_init_rejects_out_of_range_depth_color_scheme(bad_value):
    # 0 is deliberately excluded from bad_value -- unlike the positive-int-only
    # params above, 0 (Jet) is a valid, in-range depth_color_scheme.
    with pytest.raises(ValueError):
        CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(), depth_color_scheme=bad_value)


@pytest.mark.parametrize('good_value', [0, 9, None])
def test_init_accepts_in_range_depth_color_scheme(good_value):
    kwargs = {} if good_value is None else {'streamSource': 'depth', 'enableDepth': True}
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(),
                           depth_color_scheme=good_value, **kwargs)
    assert cam.depth_color_scheme == good_value


def test_init_rejects_depth_color_scheme_without_depth_streamSource():
    with pytest.raises(ValueError):
        CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=_make_rs_module(), depth_color_scheme=0)


def test_start_rejects_zero_depth_override_loudly_not_swallowed():
    """Regression test for review finding #2: depth_res_rows=0 used to be
    treated as falsy (same as None) by the old `val or self.x` defaulting
    logic and silently fall back to color's resolution instead of raising.
    A ValueError must escape start() directly here -- not be caught by
    start()'s own hardware-failure try/except and merely logged."""
    rs = _make_rs_module()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs)
    with pytest.raises(ValueError):
        cam.start(depth_res_rows=0)


# ─── start(): hardware/runtime failure contract (logged, camOn stays False) ───

def test_start_no_device_found_logs_and_leaves_camOn_false():
    rs = _make_rs_module()
    fake_context = MagicMock()
    fake_context.query_devices.return_value = []
    rs.context.return_value = fake_context

    logger = _RecordingLogger()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, logger=logger)
    cam.start()

    assert cam.camOn is False
    assert cam.pipeline is None
    assert any(sev == olab_utils.SEVERITY_ERROR for _, sev in logger.calls)


def test_start_no_matching_serial_number_logs_and_leaves_camOn_false():
    rs = _make_rs_module()
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30)
    device = _make_device(rs, 'OTHER_SERIAL', [color_profile])
    _wire_pipeline(rs, device, color_profile.as_video_stream_profile())

    logger = _RecordingLogger()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, logger=logger, serial_number='REQUESTED_SERIAL')
    cam.start()

    assert cam.camOn is False
    assert any(sev == olab_utils.SEVERITY_ERROR for _, sev in logger.calls)


def test_start_unsupported_depth_profile_logs_and_leaves_camOn_false():
    rs = _make_rs_module()
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30)
    # No depth profile at all offered by the device -- requested depth mode can't match.
    device = _make_device(rs, 'SN1', [color_profile])
    _wire_pipeline(rs, device, color_profile.as_video_stream_profile())

    logger = _RecordingLogger()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, logger=logger, enableDepth=True)
    cam.start()

    assert cam.camOn is False
    assert cam.pipeline is None
    assert any(sev == olab_utils.SEVERITY_ERROR for _, sev in logger.calls)


def test_start_missing_motion_profile_logs_and_leaves_camOn_false():
    rs = _make_rs_module()
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30)
    # No accel/gyro profiles offered -- enableIMU=True can't be satisfied.
    device = _make_device(rs, 'SN1', [color_profile])
    _wire_pipeline(rs, device, color_profile.as_video_stream_profile())

    logger = _RecordingLogger()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, logger=logger, enableIMU=True)
    cam.start()

    assert cam.camOn is False
    assert any(sev == olab_utils.SEVERITY_ERROR for _, sev in logger.calls)


def test_start_requests_explicit_resolved_fps_for_imu_streams_when_rate_is_none():
    """Regression test: _selectMotionProfile() used to return None on
    success, and start() passed imu_accel_rate/imu_gyro_rate straight
    through to config.enable_stream() -- which meant the fps argument was
    omitted entirely whenever the (documented-default) None was used.
    Confirmed against real D435i hardware that librealsense has no such
    wildcard for motion streams: the fps-omitted form fails to resolve at
    pipeline.start() ("Couldn't resolve requests"), even when the stream is
    requested alone. start() must always pass an explicit, resolved fps --
    here, the highest fps the device offers for that stream."""
    rs = _make_rs_module()
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30)
    accel_profiles = [_make_motion_profile(rs.stream.accel, fps) for fps in (63, 250)]
    gyro_profiles  = [_make_motion_profile(rs.stream.gyro, fps) for fps in (200, 400)]
    device = _make_device(rs, 'SN1', [color_profile] + accel_profiles + gyro_profiles)
    _wire_pipeline(rs, device, color_profile.as_video_stream_profile())

    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, enableIMU=True)
    cam.start()

    assert cam.camOn is True
    enable_stream_calls = rs.config.return_value.enable_stream.call_args_list
    accel_calls = [c for c in enable_stream_calls if c.args[0] == rs.stream.accel]
    gyro_calls  = [c for c in enable_stream_calls if c.args[0] == rs.stream.gyro]
    assert len(accel_calls) == 1 and len(gyro_calls) == 1
    # 3 positional args (stream_type, format, fps) -- fps must never be omitted.
    assert accel_calls[0].args == (rs.stream.accel, rs.format.motion_xyz32f, 250)
    assert gyro_calls[0].args == (rs.stream.gyro, rs.format.motion_xyz32f, 400)


# ─── start(): success path + intrinsics ownership ───

def test_start_populates_color_intrinsics_only_when_depth_shares_resolution():
    rs = _make_rs_module()
    # Color and depth share the SAME resolution but different calibration --
    # this is the collision case reviewer finding #3 was about.
    color_intr = _FakeIntrinsics(640, 480, fx=600.0, fy=600.0, ppx=320.0, ppy=240.0)
    depth_intr = _FakeIntrinsics(640, 480, fx=300.0, fy=300.0, ppx=320.0, ppy=240.0)
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30, intrinsics=color_intr)
    depth_profile = _make_video_profile(rs.stream.depth, 640, 480, 30, intrinsics=depth_intr)
    device = _make_device(rs, 'SN1', [color_profile, depth_profile])
    pipeline = _wire_pipeline(rs, device, color_profile.as_video_stream_profile(), depth_profile.as_video_stream_profile())

    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, enableDepth=True)
    cam._startCaptureThread = lambda: None  # only the synchronous setup in start() is under test here
    cam.start()

    assert cam.camOn is True
    assert '640x480' in cam.intrinsics
    assert cam.intrinsics['640x480']['matrix'][0][0] == 600.0
    assert '640x480' in cam.depthIntrinsics
    assert cam.depthIntrinsics['640x480']['matrix'][0][0] == 300.0
    # Depth's calibration must never have overwritten color's in the shared dict.
    assert cam.intrinsics['640x480']['matrix'][0][0] != cam.depthIntrinsics['640x480']['matrix'][0][0]


def test_start_does_not_overwrite_user_supplied_intrinsics():
    rs = _make_rs_module()
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30)
    device = _make_device(rs, 'SN1', [color_profile])
    pipeline = _wire_pipeline(rs, device, color_profile.as_video_stream_profile())

    paramDict = dict(PARAM_DICT)
    paramDict['intrinsics'] = {'640x480': {'fx': 111.0, 'fy': 111.0, 'cx': 320.0, 'cy': 240.0, 'dist': [0, 0, 0, 0, 0]}}
    cam = CameraRealSense(paramDict=paramDict, rs_module=rs)
    cam._startCaptureThread = lambda: None  # only the synchronous setup in start() is under test here
    cam.start()

    assert cam.intrinsics['640x480']['matrix'][0][0] == 111.0  # user value preserved, not the SDK's 600.0


def test_start_applies_depth_color_scheme_when_set():
    rs = _make_rs_module()
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30)
    depth_profile = _make_video_profile(rs.stream.depth, 640, 480, 30)
    device = _make_device(rs, 'SN1', [color_profile, depth_profile])
    _wire_pipeline(rs, device, color_profile.as_video_stream_profile(), depth_profile.as_video_stream_profile())

    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, enableDepth=True,
                           streamSource='depth', depth_color_scheme=6)
    cam._startCaptureThread = lambda: None  # only the synchronous setup in start() is under test here
    cam.start()

    assert cam.camOn is True
    cam.colorizer.set_option.assert_called_once_with(rs.option.color_scheme, 6)


def test_start_leaves_default_color_scheme_untouched_when_none():
    rs = _make_rs_module()
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30)
    depth_profile = _make_video_profile(rs.stream.depth, 640, 480, 30)
    device = _make_device(rs, 'SN1', [color_profile, depth_profile])
    _wire_pipeline(rs, device, color_profile.as_video_stream_profile(), depth_profile.as_video_stream_profile())

    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, enableDepth=True, streamSource='depth')
    cam._startCaptureThread = lambda: None  # only the synchronous setup in start() is under test here
    cam.start()

    assert cam.camOn is True
    cam.colorizer.set_option.assert_not_called()


# ─── _captureLoop(): channel order, depth meters, IMU sourcing ───

def _bgr_test_frame():
    """3x3 image with distinct, known B/G/R channel values so a swapped
    channel order is detectable."""
    img = np.zeros((3, 3, 3), dtype=np.uint8)
    img[..., 0] = 10   # B
    img[..., 1] = 20   # G
    img[..., 2] = 30   # R
    return img


def test_capture_loop_color_path_preserves_bgr_order():
    rs = _make_rs_module()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs)
    cam.pipeline = MagicMock()

    color_bgr = _bgr_test_frame()
    frameset = MagicMock()
    frameset.get_depth_frame.return_value = None
    frameset.get_color_frame.return_value = _make_color_frame(color_bgr)
    frameset.first_or_default.return_value = None

    _run_capture_loop_once(cam, frameset)

    result = cam.frameDeque[0]
    assert result[0, 0, 0] == 10 and result[0, 0, 1] == 20 and result[0, 0, 2] == 30


def test_capture_loop_depth_streamSource_converts_rgb_to_bgr():
    rs = _make_rs_module()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, enableDepth=True, streamSource='depth',
                           enableDepthFilters=False)
    cam.pipeline = MagicMock()
    cam.colorizer = MagicMock()
    cam.depthScale = 0.001

    # rs.colorizer() output is RGB8 -- distinct R/G/B so we can catch a swap.
    rgb = np.zeros((3, 3, 3), dtype=np.uint8)
    rgb[..., 0] = 30   # R (SDK-native RGB order)
    rgb[..., 1] = 20   # G
    rgb[..., 2] = 10   # B
    colorized_frame = MagicMock()
    colorized_frame.get_data.return_value = rgb
    cam.colorizer.colorize.return_value = colorized_frame

    depth_frame = _make_depth_frame(np.full((3, 3), 100, dtype=np.uint16))
    frameset = MagicMock()
    frameset.get_depth_frame.return_value = depth_frame
    frameset.first_or_default.return_value = None
    cam.align = None  # alignment tested separately; not needed for this assertion

    _run_capture_loop_once(cam, frameset)

    result = cam.frameDeque[0]
    # After RGB2BGR conversion, channel 0 (B) must be the source's B=10, channel 2 (R) must be 30.
    assert result[0, 0, 0] == 10 and result[0, 0, 2] == 30


def test_capture_loop_depth_converted_to_meters():
    rs = _make_rs_module()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, enableDepth=True, enableDepthFilters=False)
    cam.pipeline = MagicMock()
    cam.depthScale = 0.001  # typical RealSense scale: raw units -> meters
    cam.align = None

    raw_depth = np.full((2, 2), 2000, dtype=np.uint16)  # 2000 raw units
    depth_frame = _make_depth_frame(raw_depth)
    frameset = MagicMock()
    frameset.get_depth_frame.return_value = depth_frame
    frameset.get_color_frame.return_value = None
    frameset.first_or_default.return_value = None

    _run_capture_loop_once(cam, frameset)

    depth_m = cam.depthDeque[0]
    assert depth_m.dtype == np.float32
    assert np.allclose(depth_m, 2.0)  # 2000 * 0.001 = 2.0 meters


def test_enableDepthFilters_defaults_true_and_actually_applies_filters():
    """Regression test for reviewer finding #2 on the post-approval round:
    enableDepthFilters's public default changed to True (confirmed
    dramatically better on real hardware), but nothing in the mocked suite
    asserted the default actually initializes/applies the filters -- the
    other two capture-loop tests deliberately pass enableDepthFilters=False
    to avoid exercising them, so a regression here would slip past CI."""
    rs = _make_rs_module()
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30)
    depth_profile = _make_video_profile(rs.stream.depth, 640, 480, 30)
    device = _make_device(rs, 'SN1', [color_profile, depth_profile])
    _wire_pipeline(rs, device, color_profile.as_video_stream_profile(), depth_profile.as_video_stream_profile())

    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, enableDepth=True)  # enableDepthFilters left at its True default
    assert cam.enableDepthFilters is True
    cam._startCaptureThread = lambda: None  # only the synchronous setup in start() is under test here
    cam.start()

    assert cam.camOn is True
    assert cam._spatialFilter is not None
    assert cam._temporalFilter is not None
    assert cam._holeFillingFilter is not None

    # Now confirm the filters are actually applied to a captured frame, not just constructed.
    raw_depth = np.full((2, 2), 2000, dtype=np.uint16)
    depth_frame = _make_depth_frame(raw_depth)
    cam._spatialFilter.process.return_value = depth_frame
    cam._temporalFilter.process.return_value = depth_frame
    cam._holeFillingFilter.process.return_value = depth_frame

    frameset = MagicMock()
    frameset.get_depth_frame.return_value = depth_frame
    frameset.get_color_frame.return_value = None
    frameset.first_or_default.return_value = None
    cam.align = None  # isolate filter application from the (separately-tested) align step

    _run_capture_loop_once(cam, frameset)

    cam._spatialFilter.process.assert_called_once_with(depth_frame)
    cam._temporalFilter.process.assert_called_once_with(depth_frame)
    cam._holeFillingFilter.process.assert_called_once_with(depth_frame)


def test_capture_loop_imu_accel_and_gyro_not_swapped():
    rs = _make_rs_module()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, enableIMU=True)
    cam.pipeline = MagicMock()

    accel_frame = _make_motion_frame(1.0, 2.0, 3.0, timestamp_ms=1000.0)
    gyro_frame  = _make_motion_frame(4.0, 5.0, 6.0, timestamp_ms=2000.0)

    frameset = MagicMock()
    frameset.get_depth_frame.return_value = None
    frameset.get_color_frame.return_value = None

    def _first_or_default(stream_type):
        return {rs.stream.accel: accel_frame, rs.stream.gyro: gyro_frame}.get(stream_type)
    frameset.first_or_default.side_effect = _first_or_default

    _run_capture_loop_once(cam, frameset)

    imu = cam.getIMUData()
    assert imu['accel'] == (1.0, 2.0, 3.0)
    assert imu['accel_timestamp_ms'] == 1000.0
    assert imu['gyro'] == (4.0, 5.0, 6.0)
    assert imu['gyro_timestamp_ms'] == 2000.0


# ─── Thread-lifecycle robustness (review finding #1) ───

def test_capture_loop_stops_itself_on_real_error_not_shutdown():
    """A persistent real error (e.g. the device disconnecting mid-stream)
    must stop the loop deterministically after one logged error -- not spin
    calling wait_for_frames() (and re-logging) forever. Regression test for
    review finding #1's first half."""
    rs = _make_rs_module()
    logger = _RecordingLogger()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, logger=logger)
    cam.pipeline = MagicMock()
    cam.pipeline.wait_for_frames.side_effect = RuntimeError('device disconnected (test)')

    cam._capture_running = True
    cam._captureLoop()

    assert cam.camOn is False
    assert cam._capture_running is False
    assert cam.pipeline.wait_for_frames.call_count == 1  # not spinning/retrying
    assert any(sev == olab_utils.SEVERITY_ERROR for _, sev in logger.calls)


def test_start_failure_after_capture_thread_started_stops_the_thread():
    """A failure that happens *after* _startCaptureThread() has already run
    (e.g. startStream() raising) must still deterministically stop the real
    background thread via _stopCaptureThread() -- not just clear
    self.pipeline out from under a thread that's still running. Regression
    test for review finding #1's second half."""
    rs = _make_rs_module()
    color_profile = _make_video_profile(rs.stream.color, 640, 480, 30)
    device = _make_device(rs, 'SN1', [color_profile])
    pipeline = _wire_pipeline(rs, device, color_profile.as_video_stream_profile())

    frameset = MagicMock()
    frameset.get_depth_frame.return_value = None
    frameset.get_color_frame.return_value = _make_color_frame(np.zeros((480, 640, 3), dtype=np.uint8))
    frameset.first_or_default.return_value = None
    pipeline.wait_for_frames.return_value = frameset  # succeeds repeatedly -- only an external stop signal ends the thread

    logger = _RecordingLogger()
    cam = CameraRealSense(paramDict=dict(PARAM_DICT), rs_module=rs, logger=logger)
    cam.startStream = MagicMock(side_effect=RuntimeError('cannot stream (test)'))

    cam.start(startStream=True, port=9999)

    assert cam.camOn is False
    assert cam.pipeline is None
    assert cam._capture_thread is None  # _stopCaptureThread() joined and cleared it
    assert any(sev == olab_utils.SEVERITY_ERROR for _, sev in logger.calls)


# ─── Clean import without pyrealsense2 installed ───

def test_import_olab_camera_succeeds_without_pyrealsense2():
    script = textwrap.dedent(
        """
        import sys
        import importlib.abc

        class _BlockRealSense(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name.split(".")[0] == "pyrealsense2":
                    raise ImportError("pyrealsense2 blocked for test")
                return None

        sys.meta_path.insert(0, _BlockRealSense())

        import olab_camera
        from olab_camera.camera_realsense import CameraRealSense

        try:
            CameraRealSense(paramDict={'res_rows': 480, 'res_cols': 640, 'fps_target': 30})
        except ImportError:
            pass
        else:
            raise AssertionError('expected ImportError when pyrealsense2 is unavailable')

        print("SMOKE_TEST_OK")
        """
    )
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f'stdout={result.stdout!r} stderr={result.stderr!r}'
    assert 'SMOKE_TEST_OK' in result.stdout
