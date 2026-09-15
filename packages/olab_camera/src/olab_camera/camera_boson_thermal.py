"""RHP-BOS-USBC-IF (FLIR Boson+ thermal core, USB-C VPC Kit profile) camera
backend."""

import time
from pathlib import Path

import cv2

from .camera_usb import CameraUSB


# Sysfs root walked by discover_boson_thermal() -- a module-level constant
# (rather than a literal inline) so tests can monkeypatch it against a fake
# sysfs tree without touching the real filesystem.
_SYSFS_V4L2_ROOT = Path('/sys/class/video4linux')

# Confirmed against real hardware (see .pairwork/camera-boson-thermal.md):
# the RHP-BOS-USBC-IF's FLIR Boson+ core enumerates under this exact sysfs
# card name on both of its V4L2 nodes (the real capture node and the
# metadata-only node) -- discover_boson_thermal() tells them apart by
# actually opening and reading a frame, not by name alone.
_BOSON_THERMAL_CARD_NAME = 'Boson: FLIR Video'


def discover_boson_thermal(retry_seconds=2.0, poll_interval=0.5, exclude=()):
    """Find the single confirmed FLIR Boson thermal (RHP-BOS-USBC-IF) V4L2
    capture node.

    Technique: walk `/sys/class/video4linux/*/name` for the board's known,
    stable card name (`'Boson: FLIR Video'`), then -- for each match not
    already in `exclude` -- actually open the corresponding `/dev/videoN`
    node with `cv2.VideoCapture(path, cv2.CAP_V4L2)` and attempt to read a
    real frame. Matching by sysfs name alone is not enough: this board
    exposes *two* V4L2 nodes under the identical card name for one physical
    device (e.g. `/dev/video4`/`/dev/video5`) -- one is the real capture
    node, the other metadata-only and will open but never produce a frame.
    Only nodes that actually deliver a frame are "confirmed".

    Args:
        retry_seconds (float, optional): How long to keep retrying a read
            after the node opens, before giving up on it, in case the
            driver needs a moment after open() before the first frame is
            ready. Attempt-then-check-deadline: a `.read()` is always tried
            at least once, so `retry_seconds=0` still performs exactly one
            attempt (never zero) -- just with no `time.sleep`/retry after
            it. Defaults to 2.0.
        poll_interval (float, optional): Seconds to sleep between retry
            attempts. Defaults to 0.5.
        exclude (iterable of str, optional): `/dev/videoN` paths to skip
            outright -- never opened at all. Intended for a caller (e.g.
            `olab_playground`) that already has one of these nodes open in
            an active session and does not want a rescan to open/read it
            concurrently. Defaults to `()` (nothing excluded).

    Returns:
        str: The `/dev/videoN` path of the single confirmed capture node.

    Raises:
        RuntimeError: If zero nodes confirm (no matching sysfs card name, or
            none of the matches produced a frame), or if more than one node
            confirms (more than one board plugged in, or -- hypothetically,
            not seen on real hardware -- a future firmware exposing more
            than one capture node per board). Either way, the caller must
            pass `device=` explicitly; this function does not attempt
            serial-number-based disambiguation (see the class docstring).

    Notes:
        - A per-node sysfs `name` read that raises `OSError` (the node
          disappeared mid-walk -- unplugged, a permissions race) is treated
          as "not a match" and skipped, not propagated.
        - Any exception raised while opening/reading a given candidate
          (including `isOpened()` or `.read()` raising, which real V4L2
          backends are known to do transiently) is treated as "not
          confirmed" for that candidate, not propagated -- mirroring
          `olab_playground.camera.PlaygroundSession._read_frame_with_retry`'s
          own tolerance for a raising `.read()`. This is a second,
          deliberate copy of that tolerance/retry shape, not an oversight:
          `olab_camera` cannot import from `olab_playground` (the
          dependency only runs the other direction), so the same semantics
          are re-implemented here rather than shared.
        - Every `cv2.VideoCapture` this function opens is released in a
          `finally`, on every path (confirmed, not confirmed, or raised) --
          so a failing/raising probe never leaks a capture handle, no
          matter how many times discovery is re-triggered.
        - Confirmed nodes are not grouped by physical device (the way
          `olab_playground.camera.PlaygroundSession._v4l2_group` groups
          sibling `/dev/videoN` nodes under one sysfs device) and there is
          no serial-number disambiguation -- both are deliberately deferred
          to a future pass; v1 just raises on ambiguity.
    """
    excluded = {str(path) for path in exclude}

    candidates = []
    for name_path in sorted(_SYSFS_V4L2_ROOT.glob('*/name')):
        try:
            label = name_path.read_text().strip()
        except OSError:
            continue
        if label != _BOSON_THERMAL_CARD_NAME:
            continue
        node = str(Path('/dev') / name_path.parent.name)
        if node not in excluded:
            candidates.append(node)

    confirmed = []
    for node in candidates:
        cap = None
        try:
            cap = cv2.VideoCapture(node, cv2.CAP_V4L2)
            if not cap.isOpened():
                continue

            deadline = time.monotonic() + retry_seconds
            ok, frame = False, None
            while True:
                try:
                    ok, frame = cap.read()
                except Exception:
                    ok, frame = False, None
                if ok and frame is not None:
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(poll_interval)

            if ok and frame is not None:
                confirmed.append(node)
        except Exception:
            continue
        finally:
            if cap is not None:
                cap.release()

    if not confirmed:
        raise RuntimeError(
            f"No Boson thermal device found: no /sys/class/video4linux node "
            f"named {_BOSON_THERMAL_CARD_NAME!r} produced a frame. Pass "
            f"device= explicitly.")
    if len(confirmed) > 1:
        raise RuntimeError(
            f"Multiple confirmed Boson thermal capture nodes found ({confirmed}); "
            f"pass device= explicitly to disambiguate (no serial-number "
            f"disambiguation in this pass).")
    return confirmed[0]


class CameraBosonThermal(CameraUSB):
    """RHP-BOS-USBC-IF FLIR Boson+ thermal camera board, captured directly
    over USB-C (a genuine FLIR VID:PID -- `09cb:4007` -- not a generic UVC
    bridge chip).

    A thin subclass of CameraUSB -- video-only in this release (see issue
    #59 / .pairwork/camera-boson-thermal.md for the full hardware
    investigation this class is based on). There is no independent capture
    behavior here: this class only configures CameraUSB's cv2.VideoCapture
    with this board's known-good defaults and, when no `device` is given,
    locates the board via `discover_boson_thermal()`.

    Physical wiring (none of this is visible from the code, so it's
    recorded here):
        - USB-C -> host. This is the only connector this class uses.
        - 2-pin JST sync connector (for syncing with other devices) is
          present on the board but not exposed/used by this class in this
          pass.
        - The board also exposes a CDC serial port (`/dev/ttyACM0` on
          Linux) implementing the FLIR Boson Serial Command protocol. This
          class never opens or references it at all -- no protocol code, no
          FFC/palette/gain-mode/telemetry-toggle methods, not even stubs.
          Live control-plane support is a deliberately deferred follow-up,
          to be scoped in its own future session once this video path is
          validated on hardware.

    Video-only, fixed-configuration (v1 scope):
        - There is no `resolution=` parameter (unlike `CameraBosonDual`'s
          `'720p60'`/`'1080p60'`, reflecting two real board-side HD modes)
          -- this board has exactly one supported/tested video target:
          **640x512 YU12 @ 30fps**, the board's own AGC-processed 8-bit
          display output. It is **not radiometric** -- no per-pixel
          temperature data is available over this interface.
        - The board also exposes a 320x256 Y16 native/raw mode (pre-AGC)
          and 640x514/320x258 "+2 row" telemetry variants of the above.
          Neither is implemented here: Y16 needs explicit 16-bit frame
          handling (arguably control-plane/radiometric-adjacent work), and
          the telemetry-row variants have no V4L2 control to select them
          (only the undocumented-here serial protocol could toggle
          telemetry mode, and that's out of scope). Both are
          known-but-unreachable through this class, not bugs.
        - `res_rows`/`res_cols`/`fps_target` default to 512/640/30 via the
          same unvalidated `paramDict`-merge-with-escape-hatch pattern as
          `CameraBosonDual`: a caller *can* pass a different value in
          `paramDict` (e.g. attempting the Y16 or 514-row variants above),
          but that is not a supported/tested configuration.
        - 30fps (not the board's own out-of-the-box negotiated default of
          20fps) is chosen as a conservative, real discrete interval
          advertised for 640x512 YU12, avoiding the untested USB bandwidth
          risk of the format-enum's optimistic 60fps for uncompressed YUV.

    Hardware-validated caveat -- requested fps_target is not honored:
        On real RHP-BOS-USBC-IF hardware, `cv2`'s `CAP_PROP_FPS` echoes back
        whatever value was requested (30, per this class's default) both
        before and after `cap.set()` -- but the board actually delivers
        frames at ~60fps regardless, confirmed by measuring the real capture
        rate over a sustained run. This looks like a driver/board quirk
        where the frame-interval control is accepted but not actually
        applied (the sensor free-runs at its native rate). `self.fps_target`
        (and the printed/asserted value in this class's own Usage Example
        below) therefore reflects what was *requested*, not the true
        streaming rate -- treat it as informational, not a throttle, until/
        unless this is revisited.

    `fourcc=None` by default -- a deliberate departure from
    `CameraBosonDual`'s non-None `('M','J','P','G')`: this board has **no
    MJPG capability at all**, only raw YU12/NV12/Y16. `CameraUSB.start()`
    only calls `cap.set(cv2.CAP_PROP_FOURCC, ...)` when `self.fourcc is not
    None`, so leaving it `None`:
        - Skips FOURCC negotiation entirely, relying on the device's own
          default, which is already `640x512 YU12` -- exactly this class's
          target.
        - Entirely sidesteps `CameraUSB`'s documented FOURCC-after-framesize
          V4L2 ordering hazard (see `CameraBosonDual`'s docstring) -- that
          hazard only fires when a non-None fourcc is set, and there is no
          reason to set one here.
    `apiPref` defaults to `cv2.CAP_V4L2` (Linux-only V4L2 device), same
    rationale as `CameraBosonDual`.

    Device auto-discovery (new relative to `CameraBosonDual`, which has no
    discoverable default): passing `device=None` (the default) calls
    `discover_boson_thermal()` to locate the board via its stable,
    distinctive sysfs card name and FLIR VID:PID -- unlike a generic UVC
    HDMI-capture dongle, this hardware is specific enough that "no device
    given" is a safe, real default rather than a guess. `discover_boson_
    thermal()` raises `RuntimeError` if it finds zero or more than one
    confirmed capture node; that exception propagates straight out of this
    constructor uncaught -- construction fails loudly rather than silently
    guessing. Pass `device=` explicitly to skip discovery altogether (e.g.
    when more than one board is attached).

    `paramDict={'device': ...}` also skips discovery -- not just overrides
    it after the fact. `CameraUSB.__init__` only applies its own `device=`
    kwarg when `not hasattr(self, 'device')`, and `Camera.__init__` already
    set `self.device` from `paramDict` before that check runs -- so a
    `device` key in `paramDict` always wins over whatever this constructor
    passes as `device=`. Calling `discover_boson_thermal()` in that case
    would therefore be pointless work at best (its result is discarded) and
    a spurious `RuntimeError` at worst (raising "no Boson thermal device
    found" on a host with no board attached, even though the caller *did*
    supply a device via `paramDict`) -- so discovery is only attempted when
    `device is None` *and* `paramDict` does not already supply `'device'`.

    Attributes:
        (No new attributes beyond CameraUSB's own -- this class adds no
        state of its own.)

    Usage Example:
        >>> # Exactly one Boson thermal board attached; let it discover.
        >>> cam = CameraBosonThermal()
        >>> cam.start(startStream=True, port=8000)
        >>> assert (cam.res_rows, cam.res_cols, cam.fps_target) == (512, 640, 30)
        >>> cam.shutdown()

        >>> # More than one board attached, or discovery otherwise
        >>> # ambiguous/unavailable -- bypass it explicitly.
        >>> cam = CameraBosonThermal(device='/dev/video4')
    """

    def __init__(self, paramDict=None, device=None, apiPref=cv2.CAP_V4L2, fourcc=None,
            logger=None, sslPath=None, pubCamStatusFunction=None, imgTopic=None,
            compImgTopic=None, initROSnode=False, showFPS=True, ipAllowlist=[], ipBlocklist=[]):
        """Initialize the CameraBosonThermal interface.

        Args:
            paramDict (dict, optional): Configuration dictionary, same keys
                as CameraUSB ('res_rows', 'res_cols', 'fps_target',
                'outputPort', 'device', 'fourcc'). Any key given here
                overrides the fixed 640x512@30fps default for that key --
                see the paramDict escape-hatch note in the class docstring.
                A 'device' key here also suppresses this constructor's
                device-discovery call entirely (see the class docstring).
                Defaults to None (nothing overridden).
            device (str, optional): Video source path for the board's USB-C
                capture node (e.g. '/dev/video4' on Linux). Defaults to
                None, which calls discover_boson_thermal() to locate the
                board automatically -- see the class docstring for the
                raise-on-ambiguous/raise-on-not-found behavior this
                triggers, and for the paramDict['device'] interaction.
            apiPref (int, optional): OpenCV VideoCapture API preference.
                Defaults to cv2.CAP_V4L2 (this class only makes sense on
                Linux V4L2); pass a different value for other platforms.
            fourcc (tuple or None, optional): FOURCC codec as a 4-character
                tuple. Defaults to None -- this board has no MJPG
                capability, so leaving it None uses the device's own
                already-correct YU12 default and avoids the
                FOURCC-after-framesize hazard. See the class docstring.
            logger, sslPath, pubCamStatusFunction, imgTopic, compImgTopic,
            initROSnode, showFPS, ipAllowlist, ipBlocklist: Passed straight
                through to CameraUSB.__init__() -- see its docstring.
        """
        if device is None and not (paramDict and 'device' in paramDict):
            device = discover_boson_thermal()

        merged = {'res_rows': 512, 'res_cols': 640, 'fps_target': 30, 'outputPort': 8000}
        if paramDict:
            merged.update(paramDict)

        super().__init__(merged, device=device, apiPref=apiPref, fourcc=fourcc, logger=logger,
            sslPath=sslPath, pubCamStatusFunction=pubCamStatusFunction, imgTopic=imgTopic,
            compImgTopic=compImgTopic, initROSnode=initROSnode, showFPS=showFPS,
            ipAllowlist=ipAllowlist, ipBlocklist=ipBlocklist)
