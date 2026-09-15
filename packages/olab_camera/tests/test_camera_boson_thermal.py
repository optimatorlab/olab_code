"""Tests for CameraBosonThermal and discover_boson_thermal(). No hardware
(RHP-BOS-USBC-IF FLIR Boson+ board) is available yet -- see issue #59 /
.pairwork/camera-boson-thermal.md. `cv2.VideoCapture` and the sysfs root are
mocked throughout; `discover_boson_thermal()`'s bounded retry is exercised
with `retry_seconds=0` so tests stay fast (this still performs exactly one
real read attempt per candidate -- see its docstring's attempt-then-check-
deadline contract, never zero attempts).

Kept minimal per the project's stated test-thoroughness preference: the only
real logic here is (a) CameraBosonThermal.__init__'s fixed-defaults/
paramDict-merge/discovery-on-None behavior and (b) discover_boson_thermal()'s
sysfs-walk + open-and-read-confirm + 0-or->1-raises logic. Everything else
(docstrings, pass-through kwargs, playground/app.js wiring) is scaffolding
with no independent logic to regress and gets no dedicated unit test here.
"""

import cv2
import pytest

from olab_camera import camera_boson_thermal
from olab_camera.camera_boson_thermal import CameraBosonThermal, discover_boson_thermal


# ---------------------------------------------------------------------------
# CameraBosonThermal construction
# ---------------------------------------------------------------------------

def test_fixed_defaults_resolve():
    cam = CameraBosonThermal(device='/dev/video4')
    assert (cam.res_rows, cam.res_cols, cam.fps_target) == (512, 640, 30)
    assert cam.fourcc is None
    assert cam.apiPref == cv2.CAP_V4L2
    assert cam.device == '/dev/video4'


def test_paramdict_overrides_a_fixed_default():
    cam = CameraBosonThermal(device='/dev/video4', paramDict={'res_rows': 999})
    assert cam.res_rows == 999
    assert (cam.res_cols, cam.fps_target) == (640, 30)


def test_device_none_triggers_discovery(monkeypatch):
    monkeypatch.setattr(camera_boson_thermal, 'discover_boson_thermal', lambda: '/dev/video7')
    cam = CameraBosonThermal()
    assert cam.device == '/dev/video7'


def test_paramdict_device_suppresses_discovery(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError('discover_boson_thermal() should not be called '
                              "when paramDict already supplies 'device'")
    monkeypatch.setattr(camera_boson_thermal, 'discover_boson_thermal', _fail_if_called)
    cam = CameraBosonThermal(paramDict={'device': '/dev/video9'})
    assert cam.device == '/dev/video9'


# ---------------------------------------------------------------------------
# discover_boson_thermal()
# ---------------------------------------------------------------------------

class _FakeNamePath:
    """Stand-in for a Path('/sys/class/video4linux/<node>/name') entry."""

    def __init__(self, node, label, raises=False):
        self._node = node
        self._label = label
        self._raises = raises
        self.parent = self

    @property
    def name(self):
        return self._node

    def read_text(self):
        if self._raises:
            raise OSError('node disappeared')
        return self._label

    def __lt__(self, other):
        return self._node < other._node


class _FakeSysfsRoot:
    """Stand-in for _SYSFS_V4L2_ROOT, returning a fixed set of fake name paths."""

    def __init__(self, entries):
        self._entries = entries

    def glob(self, pattern):
        assert pattern == '*/name'
        return list(self._entries)


class _FakeCapture:
    """Stand-in for cv2.VideoCapture -- 'opens' if the node is in `openable`,
    and 'reads' successfully if the node is in `readable`.

    `read_raises=True` raises on every call (used to prove a raise is
    tolerated at all). `raise_then_succeed=N` raises on the first N calls
    and succeeds from call N+1 onward (used to prove a raise is *retried*,
    not just tolerated once -- see test_discover_retries_after_raising_
    read_then_succeeds)."""

    def __init__(self, node, opens=True, reads=True, read_raises=False, raise_then_succeed=0):
        self.node = node
        self._opens = opens
        self._reads = reads
        self._read_raises = read_raises
        self._raise_then_succeed = raise_then_succeed
        self._read_calls = 0
        self.released = False

    def isOpened(self):
        return self._opens

    def read(self):
        self._read_calls += 1
        if self._read_raises or self._read_calls <= self._raise_then_succeed:
            raise cv2.error('transient decode error')
        if self._reads:
            return True, object()
        return False, None

    def release(self):
        self.released = True


def _patch_sysfs(monkeypatch, card_entries):
    """card_entries: list of (node, label) or (node, label, raises_bool)."""
    entries = []
    for entry in card_entries:
        node, label = entry[0], entry[1]
        raises = entry[2] if len(entry) > 2 else False
        entries.append(_FakeNamePath(node, label, raises=raises))
    monkeypatch.setattr(camera_boson_thermal, '_SYSFS_V4L2_ROOT', _FakeSysfsRoot(entries))


def _patch_capture(monkeypatch, captures_by_node, fail_nodes=()):
    """captures_by_node: dict of node -> _FakeCapture. `fail_nodes` raise if
    cv2.VideoCapture() is ever called for them (used to prove exclusion)."""
    created = []

    def _fake_video_capture(node, api_pref):
        if node in fail_nodes:
            raise AssertionError(f'cv2.VideoCapture should not have been called for {node!r}')
        cap = captures_by_node[node]
        created.append(cap)
        return cap

    monkeypatch.setattr(camera_boson_thermal.cv2, 'VideoCapture', _fake_video_capture)
    return created


def test_discover_found_one(monkeypatch):
    _patch_sysfs(monkeypatch, [('video4', 'Boson: FLIR Video')])
    _patch_capture(monkeypatch, {'/dev/video4': _FakeCapture('/dev/video4')})

    assert discover_boson_thermal(retry_seconds=0) == '/dev/video4'


def test_discover_found_none_raises(monkeypatch):
    _patch_sysfs(monkeypatch, [('video0', 'Some Other Webcam')])
    _patch_capture(monkeypatch, {})

    with pytest.raises(RuntimeError, match='No Boson thermal device found'):
        discover_boson_thermal(retry_seconds=0)


def test_discover_found_multiple_raises(monkeypatch):
    _patch_sysfs(monkeypatch, [
        ('video4', 'Boson: FLIR Video'),
        ('video6', 'Boson: FLIR Video'),
    ])
    _patch_capture(monkeypatch, {
        '/dev/video4': _FakeCapture('/dev/video4'),
        '/dev/video6': _FakeCapture('/dev/video6'),
    })

    with pytest.raises(RuntimeError, match='Multiple confirmed Boson thermal capture nodes'):
        discover_boson_thermal(retry_seconds=0)


def test_discover_metadata_node_excluded(monkeypatch):
    # video4 is the real capture node (opens + reads); video5 shares the
    # card name but is metadata-only (opens, but read() never succeeds).
    _patch_sysfs(monkeypatch, [
        ('video4', 'Boson: FLIR Video'),
        ('video5', 'Boson: FLIR Video'),
    ])
    captures = {
        '/dev/video4': _FakeCapture('/dev/video4', reads=True),
        '/dev/video5': _FakeCapture('/dev/video5', reads=False),
    }
    _patch_capture(monkeypatch, captures)

    assert discover_boson_thermal(retry_seconds=0) == '/dev/video4'
    assert captures['/dev/video4'].released
    assert captures['/dev/video5'].released


def test_discover_exclude_skips_node_entirely(monkeypatch):
    _patch_sysfs(monkeypatch, [
        ('video4', 'Boson: FLIR Video'),
        ('video6', 'Boson: FLIR Video'),
    ])
    # video4 would raise if opened at all -- proves exclude= keeps it from
    # ever being probed, not just from being counted.
    _patch_capture(monkeypatch, {'/dev/video6': _FakeCapture('/dev/video6')},
                    fail_nodes={'/dev/video4'})

    assert discover_boson_thermal(retry_seconds=0, exclude=['/dev/video4']) == '/dev/video6'


def test_discover_tolerates_raising_read_and_still_releases(monkeypatch):
    # video4's .read() raises a real cv2.error (a transient decode error is
    # known to happen on real V4L2 backends -- see _read_frame_with_retry's
    # own docstring) -- this must be tolerated as "not confirmed", not
    # propagated, and the capture must still be released. video6 is a
    # normal confirming node, proving discovery keeps going afterward.
    _patch_sysfs(monkeypatch, [
        ('video4', 'Boson: FLIR Video'),
        ('video6', 'Boson: FLIR Video'),
    ])
    captures = {
        '/dev/video4': _FakeCapture('/dev/video4', read_raises=True),
        '/dev/video6': _FakeCapture('/dev/video6'),
    }
    _patch_capture(monkeypatch, captures)

    assert discover_boson_thermal(retry_seconds=0) == '/dev/video6'
    assert captures['/dev/video4'].released
    assert captures['/dev/video6'].released


def test_discover_retries_after_raising_read_then_succeeds(monkeypatch):
    # Distinguishes the inner per-read try/except (retries after a raise,
    # per the attempt-then-check-deadline contract) from the outer
    # per-candidate try/except (abandons the candidate on the *first*
    # raise, no retry) -- test_discover_tolerates_raising_read_and_still_
    # releases above is satisfied by either implementation, since its
    # capture always raises and there's nothing left to retry into. Only
    # a capture that raises first and then succeeds can tell them apart,
    # and this is the exact real-hardware behavior _read_frame_with_
    # retry's own docstring documents ("the first few reads throw a real
    # cv2.error" before one succeeds).
    _patch_sysfs(monkeypatch, [('video4', 'Boson: FLIR Video')])
    _patch_capture(monkeypatch, {'/dev/video4': _FakeCapture('/dev/video4', raise_then_succeed=2)})

    assert discover_boson_thermal(retry_seconds=1.0, poll_interval=0.001) == '/dev/video4'


def test_discover_tolerates_raising_sysfs_name_read(monkeypatch):
    # A node can disappear mid-walk (unplugged, permissions race) -- its
    # sysfs `name` file raising OSError must be skipped, not propagated,
    # and must not stop the walk from finding the real match afterward.
    _patch_sysfs(monkeypatch, [
        ('video3', 'irrelevant', True),
        ('video4', 'Boson: FLIR Video'),
    ])
    _patch_capture(monkeypatch, {'/dev/video4': _FakeCapture('/dev/video4')})

    assert discover_boson_thermal(retry_seconds=0) == '/dev/video4'
