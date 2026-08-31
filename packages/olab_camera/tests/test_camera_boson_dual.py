"""Tests for CameraBosonDual. No hardware (RHP-BOS-DS-IF + HDMI capture
dongle) is available yet -- see issue #58 / .pairwork/camera-boson-dual.md.
CameraBosonDual.__init__() never touches cv2.VideoCapture (that only
happens in CameraUSB.start()), so these tests just construct instances and
check the resolved attributes. Kept minimal per the project's stated
test-thoroughness preference: the only real logic here is the
resolution-preset lookup and its interaction with paramDict, so that's the
only thing tested.
"""

import pytest

from olab_camera import CameraBosonDual


def test_resolution_presets_resolve_and_paramdict_overrides():
    cam_720 = CameraBosonDual(resolution='720p60')
    assert (cam_720.res_rows, cam_720.res_cols, cam_720.fps_target) == (720, 1280, 60)

    cam_1080 = CameraBosonDual(resolution='1080p60')
    assert (cam_1080.res_rows, cam_1080.res_cols, cam_1080.fps_target) == (1080, 1920, 60)

    # An explicit paramDict key overrides the resolution-derived default.
    cam_override = CameraBosonDual(resolution='720p60', paramDict={'res_rows': 999})
    assert cam_override.res_rows == 999
    assert (cam_override.res_cols, cam_override.fps_target) == (1280, 60)


def test_invalid_resolution_raises_value_error():
    with pytest.raises(ValueError):
        CameraBosonDual(resolution='not-a-real-mode')
