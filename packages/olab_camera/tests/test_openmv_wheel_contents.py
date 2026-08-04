"""Wheel-level verification for the OpenMV extra (review round 2, point 2):
a package/import test proving the `openmv_profiles/assets/helper.py` text
asset is actually included in the *built* wheel, not just importable from
the source tree -- plus a slow, opt-in clean-install matrix test.

Builds via `python -m build`, matching this repo's own convention (see
CONTRIBUTING.md's pre-commit checklist and .github/workflows/ci.yml's
"Build wheel and sdist" step).
"""

import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]  # packages/olab_camera


def _build_wheel(outdir):
    subprocess.run(
        [sys.executable, '-m', 'build', '--wheel', '--outdir', str(outdir), str(_PACKAGE_ROOT)],
        check=True, capture_output=True, text=True, timeout=180,
    )
    wheels = list(Path(outdir).glob('*.whl'))
    assert len(wheels) == 1, f'expected exactly one wheel, got {wheels}'
    return wheels[0]


def test_built_wheel_includes_openmv_helper_asset(tmp_path):
    wheel_path = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel_path) as z:
        names = z.namelist()

    assert 'olab_camera/openmv_profiles/assets/helper.py' in names
    assert 'olab_camera/openmv_device.py' in names
    assert 'olab_camera/camera_openmv.py' in names


@pytest.mark.slow
def test_clean_install_matrix_base_and_openmv_extra(tmp_path):
    """The actual clean-install matrix (review round 2, point 2): builds the
    wheel once, then installs it into two throwaway venvs -- one base-only,
    one with the [openmv] extra -- and smoke-tests each. Excluded from the
    default run (see pyproject.toml's addopts) since it does real network
    installs; run explicitly with `pytest -m slow`.
    """
    wheel_path = _build_wheel(tmp_path / 'dist')

    # olab_utils is a workspace-internal dependency not published anywhere
    # pip can resolve it from by name -- install it from its local path
    # first, same as CI does (see .github/workflows/ci.yml).
    olab_utils_path = _PACKAGE_ROOT.parent / 'olab_utils'

    def _fresh_venv(name):
        venv_dir = tmp_path / name
        venv.create(venv_dir, with_pip=True)
        return venv_dir / 'bin' / 'python'

    # --- base install, no extras ---
    base_python = _fresh_venv('venv_base')
    subprocess.run([str(base_python), '-m', 'pip', 'install', '-q', str(olab_utils_path)],
                    check=True, capture_output=True, text=True, timeout=300)
    subprocess.run([str(base_python), '-m', 'pip', 'install', '-q', str(wheel_path)],
                    check=True, capture_output=True, text=True, timeout=300)

    result = subprocess.run(
        [str(base_python), '-c',
         "import olab_camera; olab_camera.CameraUSB(paramDict={'res_rows':480,'res_cols':640,'fps_target':30}); "
         "import importlib.util; assert importlib.util.find_spec('openmv') is None; print('BASE_OK')"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert 'BASE_OK' in result.stdout

    # --- [openmv] extra ---
    openmv_python = _fresh_venv('venv_openmv')
    subprocess.run([str(openmv_python), '-m', 'pip', 'install', '-q', str(olab_utils_path)],
                    check=True, capture_output=True, text=True, timeout=300)
    subprocess.run([str(openmv_python), '-m', 'pip', 'install', '-q', f'{wheel_path}[openmv]'],
                    check=True, capture_output=True, text=True, timeout=300)

    result = subprocess.run(
        [str(openmv_python), '-c',
         "import openmv; import olab_camera; "
         "olab_camera.OpenMVDevice('/dev/ttyFAKE', client_class=object); print('OPENMV_OK')"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert 'OPENMV_OK' in result.stdout
