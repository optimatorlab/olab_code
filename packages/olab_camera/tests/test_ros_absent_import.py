"""Guards the no-ROS compatibility contract after `camera.py` took over the
single guarded rospy/cv_bridge/sensor_msgs import (see issue #12's pairwork
plan). Runs in a subprocess that deterministically blocks rospy/cv_bridge/
sensor_msgs via a sys.meta_path finder -- so this exercises the no-ROS path
on every runner, including ones that happen to have ROS installed, rather
than relying on this dev environment's own lack of ROS."""

import subprocess
import sys
import textwrap

_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys
    import importlib.abc

    _BLOCKED = {"rospy", "cv_bridge", "sensor_msgs"}

    class _BlockROS(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name.split(".")[0] in _BLOCKED:
                raise ImportError(f"{name} blocked for test")
            return None

    sys.meta_path.insert(0, _BlockROS())

    import olab_camera
    from olab_camera.camera import Camera, STREAM_MAX_WAIT_TIME_SEC, ROSPUB_MAX_WAIT_TIME_SEC

    assert STREAM_MAX_WAIT_TIME_SEC == 2
    assert ROSPUB_MAX_WAIT_TIME_SEC == 2

    cam = Camera(paramDict={"res_rows": 480, "res_cols": 640, "fps_target": 30})
    assert cam.hasROSnode is False
    cam._init_ros_node()
    assert cam.hasROSnode is False

    print("SMOKE_TEST_OK")
    """
)


def test_import_and_ros_use_without_ros_installed():
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("rospy is not installed") == 1, result.stdout
    assert "SMOKE_TEST_OK" in result.stdout
