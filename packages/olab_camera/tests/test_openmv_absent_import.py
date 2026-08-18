"""Guards the no-openmv compatibility contract (mirrors
test_ros_absent_import.py): runs in a subprocess that deterministically
blocks `openmv` via a sys.meta_path finder, so this exercises the
absent-dependency path on every runner regardless of whether `openmv`
happens to be installed in the dev environment."""

import subprocess
import sys
import textwrap

_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys
    import importlib.abc

    class _BlockOpenMV(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name.split(".")[0] == "openmv":
                raise ImportError("openmv blocked for test")
            return None

    sys.meta_path.insert(0, _BlockOpenMV())

    import olab_camera

    # Every other camera class still constructs fine with openmv absent.
    cam = olab_camera.CameraUSB(paramDict={"res_rows": 480, "res_cols": 640, "fps_target": 30})
    assert cam.camOn is False

    # OpenMVDevice/CameraOpenMV raise a clear ImportError naming the extra,
    # rather than failing on unrelated AttributeErrors deeper in the client.
    try:
        olab_camera.OpenMVDevice("/dev/ttyACM0")
        raise AssertionError("OpenMVDevice() should have raised ImportError")
    except ImportError as e:
        assert "olab-camera[openmv]" in str(e)

    try:
        olab_camera.CameraOpenMV(devicePort="/dev/ttyACM0").start()
        raise AssertionError("CameraOpenMV(...).start() should have raised ImportError")
    except ImportError as e:
        assert "olab-camera[openmv]" in str(e)

    print("SMOKE_TEST_OK")
    """
)


def test_import_and_openmv_use_without_openmv_installed():
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "SMOKE_TEST_OK" in result.stdout
