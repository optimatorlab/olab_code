# olab_camera Usage Guide

# Introduction to the `olab_camera.py` module

This document describes some basic functionality of the `olab_camera` module.

See [example Jupyter notebook](https://github.com/optimatorlab/ub_code/issues/5).

---

### 1.  Import the `olab_camera` and other useful packages:
```python
import olab_camera, olab_utils
import cv2
import numpy as np
```

### 2. Initialize your camera
There are several types of camera classes:
1. `CameraUSB` - This is for any camera that has a device path (like `/dev/video0`).  Examples include webcams, internal laptop cams, and even Raspberry Pi cameras.
2. `CameraROS` - This is for cameras that subscribe to compressedImage topic, including Gazebo simulations and the Clover drone (real hardware).
3. `CameraPi` - This is exclusive to Raspberry Pi cameras that use the `picamera` package.  This option is deprecated.
4. `CameraRealSense` - For Intel RealSense devices (developed/tested against a D435i), via the `pyrealsense2` SDK. See [Section 5](#5-realsense-cameras-color--depth--imu) below for depth/IMU-specific usage.

If you're unsure, chances are `CameraUSB` is the appropriate class for you.

```python
# Specify port for streaming:
port = olab_utils.findOpenPort(8000, options=range(8000,8040))

# Define input device, image size, frames-per-second, etc:
device    = 0      # or 'https://192.0.2.1:8002/stream.mjpg' or '/dev/video0'
paramDict = {'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort': port}
apiPref   = cv2.CAP_ANY   # on linux try cv2.CAP_V4L2

# Initialize `CameraUSB` class, using default SSL certs
camera = olab_camera.CameraUSB(paramDict = paramDict,
                             device = device,
                             apiPref = apiPref,
                             showFPS=True)    # False --> Hide frames-per-second in video feed

# Start camera and stream (MJPEG default):
camera.start(startStream=True, port=paramDict['outputPort'])

print(f'Visit https://localhost:{paramDict["outputPort"]}/stream.mjpg')

# Or start with a different protocol after camera.start():
# camera.startStream(port=paramDict['outputPort'], protocol='websocket')
# camera.startStream(port=paramDict['outputPort'], protocol='webrtc')
# print(f'Visit https://localhost:{paramDict["outputPort"]}/webrtc')

print("When you're done, be sure to stop the camera: camera.stop()")
```
- **Before you exit, make sure you stop your camera.**  See code below.


### 3.  When you're done with the camera, stop it:
```python
camera.stopStream()
camera.stop()
```

---

### 4.  Per-frame processing with `frameProcessor` (optional)

`CameraUSB` and `CameraPi2` expose a `frameProcessor` hook — an optional callable that runs on every captured frame before it is streamed. Assign it before or after `start()`.

**Process and stream the edited frame:**
```python
def my_pipeline(frame):
    frame = apply_color_filter(frame)
    frame = cv2.GaussianBlur(frame, (5, 5), 0)
    return frame   # edited frame is streamed

camera.frameProcessor = my_pipeline
```

**Process a copy, stream the original unchanged:**
```python
def my_pipeline(frame):
    processed = frame.copy()
    do_something_with(processed)   # analyze, log, publish, etc.
    return frame                   # original streams unchanged
```

**Drop a frame entirely** (not streamed, not published to ROS) by returning `None`:
```python
def my_pipeline(frame):
    if should_drop(frame):
        return None   # frame is discarded
    return frame
```

**Non-blocking processing** — use a worker thread with a size-1 queue so slow inference never blocks the capture loop:
```python
import queue, threading

q = queue.Queue(maxsize=1)

def worker():
    while True:
        frame = q.get()
        if frame is None:
            break
        do_something_with(run_inference(frame))

threading.Thread(target=worker, daemon=True).start()

def my_pipeline(frame):
    try:
        q.put_nowait(frame.copy())  # drop if worker is still busy
    except queue.Full:
        pass
    return frame   # original always streams without blocking

camera.frameProcessor = my_pipeline
```

> **Note:** For `CameraUSB`, `frameProcessor` receives the frame *after* digital zoom is applied. Set `camera.frameProcessor = None` to restore pass-through behavior.

---

### 5.  RealSense cameras (color + depth + IMU)

`CameraRealSense` wraps Intel's `pyrealsense2` SDK. Install the optional
dependency first:
```bash
pip install olab-camera[realsense]
```

**On x86_64 Linux (verified: Ubuntu 24.04, Python 3.12)** this installs a
prebuilt `pyrealsense2` wheel with the RealSense SDK bundled in --
no system-level `librealsense`/apt setup, no build step. Confirm it worked:
```bash
python -c "import pyrealsense2 as rs; print(rs.context().query_devices())"
```
This should print an (possibly empty, if no device is plugged in yet)
device list with no errors -- if it raises `ModuleNotFoundError`, the
`pip install` above didn't complete; if it raises something else, that's
a real `pyrealsense2`/librealsense problem, not an `olab_camera` one.

**On ARM (e.g. Raspberry Pi, including the CM5)**: `pyrealsense2` does
publish prebuilt `manylinux2014_aarch64` wheels on PyPI, but only for
specific CPython versions -- confirmed (via PyPI's own file index) that as
of `pyrealsense2==2.58.3.10794` these exist for **Python 3.9, 3.10, and
3.12** on aarch64, and *not* for 3.11 or 3.13. Check which case you're in
before assuming `pip install` will "just work":
```bash
python3 --version
```
- **3.9 / 3.10 / 3.12 on aarch64**: `pip install olab-camera[realsense]`
  should install a prebuilt wheel exactly like the x86_64 case above.
- **3.11 (e.g. Raspberry Pi OS Bookworm's default) / 3.13 (e.g. Debian
  Trixie's default) on aarch64, or any other platform without a matching
  wheel**: `pip install pyrealsense2` will fail with "no matching
  distribution." Two options: install one of the supported Python versions
  alongside the system default just for this venv (e.g. via your distro's
  packages, `pyenv`, or `deadsnakes` -- **not verified in this repo**), or
  build `librealsense` from source with Python bindings enabled --
  **verified end-to-end** (2026-07-30, Raspberry Pi CM5, Debian Trixie
  (13), Python 3.13.5, aarch64, kernel `6.12.47+rpt-rpi-2712`; librealsense
  `2.58.3` from
  [github.com/realsenseai/librealsense](https://github.com/realsenseai/librealsense)
  -- the project moved from `IntelRealSense/librealsense`, use the new URL):

  ```bash
  sudo apt-get update
  sudo apt-get install -y libusb-1.0-0-dev libudev-dev libssl-dev pkg-config libgtk-3-dev
  sudo apt-get install -y git wget cmake build-essential
  sudo apt-get install -y libglfw3-dev libgl1-mesa-dev libglu1-mesa-dev
  sudo apt-get install -y python3-dev python3.13-dev   # match your actual Python version
  ```
  `python3-dev`/`python3.NN-dev` (the actual headers + `libpython3.NN.so`)
  are **required**, separately from the base `python3` package -- without
  them, `cmake` fails with `Could NOT find Python (missing:
  Python_INCLUDE_DIRS Python_LIBRARIES ...)` even though the legacy
  `PythonInterp`/`PythonLibs` detection reports the interpreter found fine.

  ```bash
  cd ~/Projects   # or wherever you keep repos
  git clone https://github.com/realsenseai/librealsense.git
  cd librealsense
  ./scripts/setup_udev_rules.sh
  mkdir build && cd build
  cmake .. -DCMAKE_BUILD_TYPE=Release -DFORCE_RSUSB_BACKEND=ON -DBUILD_PYTHON_BINDINGS=true -DPYTHON_EXECUTABLE=$(which python3) -DBUILD_EXAMPLES=false -DBUILD_GRAPHICAL_EXAMPLES=false -DBUILD_TOOLS=false -DCHECK_FOR_UPDATES=false
  make -j$(nproc)   # ~20 minutes on a CM5's 4 cores
  sudo make install
  sudo ldconfig
  ```
  - **`./scripts/setup_udev_rules.sh` prompts** with `read -p "Remove all
    RealSense cameras attached. Hit any key when ready"` whenever *any*
    `/dev/video*` node exists on the machine -- true on essentially every
    CM5 here, since Pi cameras are typically already attached. This is
    fine interactively (just hit Enter) -- it only becomes a problem when
    running this non-interactively (e.g. over `ssh host cmd` with no pty),
    where nothing can ever answer it and it hangs forever with zero
    output. If automating this, pipe an answer in explicitly, e.g. `echo |
    sudo ./scripts/setup_udev_rules.sh`.
  - **`-DBUILD_EXAMPLES=false -DBUILD_GRAPHICAL_EXAMPLES=false
    -DBUILD_TOOLS=false -DCHECK_FOR_UPDATES=false`**: we only need the
    core library + Python bindings, not the GUI viewer/depth-quality
    tools or firmware-updater. Confirmed via a real build failure on a
    second CM5 (olab-131, 2026-07-31): with the defaults (all four of
    these are ON by default on Linux), `realsense-viewer` and
    `rs-depth-quality` failed to link with `undefined reference to
    idn2_*` -- `CHECK_FOR_UPDATES` (ON by default on Linux) pulls in a
    bundled libcurl built with IDN support, but the final link of those
    two tool binaries never actually links `libidn2.so`, so the build
    fails. Disabling examples/tools/update-checking sidesteps the broken
    link entirely (we don't need any of it) rather than chasing the idn2
    linkage itself, and also cuts build time.
  - **`FORCE_RSUSB_BACKEND=ON`**: Intel's kernel-patch scripts
    (`patch-realsense-*.sh`) only support Ubuntu 20/22/24 LTS -- Debian
    isn't covered. `FORCE_RSUSB_BACKEND` uses a userspace USB backend
    instead, needing no kernel patches at all; it's explicitly documented
    as "optional for Linux" (vs. mandatory on Windows/macOS/Android),
    exactly for this situation.
  - **`-DPYTHON_EXECUTABLE=$(which python3)`**: run this with your target
    venv activated. `sudo make install` installs the compiled
    `pyrealsense2` package **directly into that venv's own
    site-packages** (with proper `__init__.py` + versioned `.so` symlinks)
    -- no manual copying, no `PYTHONPATH` fiddling needed. (An earlier
    version of this guide assumed a manual copy step was required; it
    wasn't -- verified by watching `make install`'s actual output
    location.)
  - Confirm: `python3 -c "import pyrealsense2 as rs;
    print(rs.context().query_devices())"` should print a real device list
    (or an empty one if nothing's plugged in), no `ModuleNotFoundError`.
  - **Do not run `pip install olab-camera[realsense]` after a source
    build.** That extra tries to pull `pyrealsense2` from PyPI, which has
    no wheel for this platform/Python combination and will just fail --
    `pyrealsense2` is already provided by the build above, and
    `olab-camera`'s other dependencies install normally on their own.

  **Same-hardware transfer**: on an *identical* CM5 (same Debian point
  release, same Python 3.13.x), you don't need to rebuild --
  `rsync`/copy the whole `librealsense/build/` directory over and run
  `sudo make install` there too (fast, since everything's already
  compiled). Still need `./scripts/setup_udev_rules.sh` and your own
  `99-realsense-iio.rules`/group setup (below) on the second machine --
  those aren't part of the build tree. If the two machines ever drift
  onto different OS/kernel/Python versions, don't assume binary
  compatibility -- rebuild and re-verify.

**Device permissions (Linux)**: if `query_devices()` finds nothing despite
a RealSense camera being plugged in, it's usually a udev-rules issue (the
device node exists but your user can't access it), not a Python/SDK
problem -- Intel's installation guide above covers installing
`librealsense`'s udev rules. Unplugging/replugging the device after
installing the rules is often required.

**IMU permissions (Linux, `enableIMU=True`)**: color and depth streaming
use standard USB/UVC access, which `systemd-logind` grants your login
session automatically (no extra udev rule needed on most desktop distros --
confirmed on this repo's Ubuntu 24.04 dev machine). The IMU is different:
it's exposed through the Linux **IIO** (Industrial I/O) subsystem via a HID
sensor hub, and `logind`'s automatic per-session ACLs do *not* cover IIO
devices. Without an explicit udev rule, `enableIMU=True` fails at
`pipeline.start()` with a `Permission denied` error even when
`query_devices()` and color/depth both work fine. Confirmed (by actually
plugging in a D435i and working through this) that a working fix needs a
udev rule covering **three separate things**, not just one:
1. The `/sys/bus/iio/devices/iio:deviceN/...` sysfs attribute tree
   (`scan_elements/*_en`, `in_accel_sampling_frequency`,
   `in_anglvel_sampling_frequency`, `buffer/*`, `trigger/*`) -- a plain
   `MODE=` udev directive doesn't reach these (they're created after the
   device node itself), so this needs a `RUN+=` shell `chmod`.
2. `/dev/hidrawN` for the RealSense's HID interface -- the actual HID
   reports flow through here.
3. `/dev/iio:deviceN` itself (major 236) -- the char device
   `pyrealsense2` reads buffered IMU samples from; distinct from both of
   the above, and needs its own `MODE=` rule.

**Scope every rule to the specific RealSense device** (`idVendor`+
`idProduct`), not just `idVendor=="8086"` (Intel's vendor ID, which also
matches unrelated Intel HID/sensor hardware on many machines, e.g. laptop
touchpads or built-in sensor hubs) -- an earlier draft of this rule matched
on vendor ID alone and granted world read/write to *every* Intel hidraw
device and *every* IIO device on the machine, which a companion-computer/
shared-machine reviewer correctly flagged as a real security and device-
integrity regression, not just a RealSense-specific permission fix.
`idProduct` for the D435i is `0b3a`; confirmed via
`udevadm info -a -n /dev/iio:device1 | grep -A1 ATTRS{idProduct}` that
`udev`'s `ATTRS{}` matching walks up the sysfs parent chain from the IIO
device to the originating USB device, so both `idVendor` and `idProduct`
can be matched directly on the `iio`/`hidraw` rules below without any
extra plumbing. **If you're on a different RealSense model** (D415, D455,
L515, etc.), look up its `idProduct` the same way (or via `lsusb -d 8086:`)
and use that value instead -- don't widen the match back to vendor-only.

**Use a dedicated group, not world-writable permissions.** Device scoping
(above) stops the rule from touching *unrelated* devices, but on its own
still leaves the RealSense's own IMU nodes world read/write (`MODE="0666"`/
`a+rwX`), so any unprivileged local process -- not just the intended
`olab_camera` caller -- can alter that camera's IMU buffer/trigger/
sampling-rate/enable controls or read/inject its device traffic. On a
companion computer running an obstacle-avoidance process, that's a real
integrity risk, not just a permissions inconvenience. Create a dedicated
group once, and grant access to it instead of everyone:
```bash
sudo groupadd -f realsense
sudo usermod -aG realsense $USER
```
**`usermod` alone does not update your already-running login session's
group list** -- and a plain **new terminal window is not a fresh login
session** and will not pick up the change either. In most desktop
environments (GNOME Terminal, etc.), a new window/tab is just a new shell
spawned from a terminal-server process that has been running (with its
original group list fixed) since you logged into your desktop session --
opening another window doesn't make it re-read `/etc/group`. Confirmed
this the hard way: opening a new terminal window still failed with the
same IMU permission error, while `newgrp realsense` in the *existing*
shell worked immediately. Two ways to actually pick up the new group:
- **Immediately, in your current shell only**: run `newgrp realsense`
  (or launch whatever needs it via `sg realsense -c '...'`). Only applies
  to that shell and its children (e.g. a Jupyter server launched from it).
- **Durably, for every future shell/terminal/app with no extra steps**:
  fully log out of your desktop session and back in (or reboot). This
  forces a fresh PAM-driven session that re-reads `/etc/group`, after
  which new group membership is picked up automatically everywhere --
  no `newgrp`/`sg` needed again.

Create `/etc/udev/rules.d/99-realsense-iio.rules`:
```
SUBSYSTEM=="iio", ATTRS{idVendor}=="8086", ATTRS{idProduct}=="0b3a", ACTION=="add", RUN+="/bin/sh -c 'chgrp -R realsense /sys%p 2>/dev/null; chmod -R g+rwX,o-rwx /sys%p 2>/dev/null || true'"
KERNEL=="hidraw*", ATTRS{idVendor}=="8086", ATTRS{idProduct}=="0b3a", GROUP:="realsense", MODE:="0660"
SUBSYSTEM=="iio", ATTRS{idVendor}=="8086", ATTRS{idProduct}=="0b3a", KERNEL=="iio:device*", GROUP:="realsense", MODE:="0660"
```
**Use `GROUP:=`/`MODE:=` (the "final assignment" operator), not plain
`GROUP=`/`MODE=`.** If you built `librealsense` from source, you already
ran `./scripts/setup_udev_rules.sh`, which installs Intel's own
`99-realsense-libusb.rules` -- and confirmed on a real CM5 that this file
sorts *after* `99-realsense-iio.rules` alphabetically and re-assigns the
same `hidraw` device to `GROUP=plugdev, MODE=0666` (world-permissive),
silently overriding a plain `GROUP=`/`MODE=` assignment in ours. `:=`
makes an assignment immune to being overridden by any later-processed
rule, regardless of filename ordering (verified: `udevadm test
/dev/hidrawN` showed `99-realsense-libusb.rules` applying its own
`GROUP`/`MODE` right after ours, before the fix). Diagnose this yourself
via `sudo udevadm test /dev/hidrawN 2>&1 | grep -E "GROUP|MODE"` -- it
prints every rule file that touches those properties, in the order
applied; the last one listed is what wins under plain `=`.

**Type or paste this file carefully -- a corrupted rule here can silently
break unrelated devices system-wide, not just the RealSense.** Confirmed
the hard way: pasting the long `RUN+=` line into a terminal without
bracketed-paste support let the terminal's own line-wrap become a literal
newline mid-word, splitting one rule into two broken fragments. The
tail fragment of a different line ended up as `MODE="0660"` on its own,
with **no match conditions at all** -- an unconditional rule that
applied `0660` permissions to *every* device udev processed afterward,
including `/dev/null`, silently breaking it (and likely others) until a
reboot reasserted correct defaults. After creating/editing this file,
always run `cat -n /etc/udev/rules.d/99-realsense-iio.rules` and confirm
it's **exactly 3 lines** before reloading -- if `ACTION=="add"` or
`MODE=` land on a line by themselves, edit it directly (e.g. `sudo nano`)
rather than re-pasting.

Then apply it (no reboot/replug needed -- `--action=add` re-triggers udev's
add event for the already-present device):
```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add
```
**Use `--action=add`, not a plain `udevadm trigger`.** Confirmed (the hard
way) that a bare `udevadm trigger` fires a `change` event by default, not
`add` -- the `MODE=`/`GROUP=` device-node rules above have no `ACTION=`
filter so they still apply either way, but the sysfs `RUN+=` rule below is
guarded by `ACTION=="add"` and silently never re-fires under a plain
`trigger`, leaving the sysfs attribute permissions stale even though the
device nodes look correctly updated. `--action=add` re-triggers a real
add event for the already-present device (no unplug/replug needed) and
fires both.

Note the `chmod -R g+rwX,o-rwx` (not just `g+rwX`) on the sysfs `RUN+=`
line -- `chmod`'s `+` is additive only, so if you're replacing an earlier,
broader version of this rule (or otherwise already have stray
world-writable permissions on these sysfs attributes from prior
troubleshooting), `g+rwX` alone would add group access without ever
removing the pre-existing world-writable bits. `o-rwx` explicitly strips
them, so the end state is correct regardless of what was there before.

**If it still fails after the rule is applied**, two things caused real
false starts while validating this on the dev machine, both worth checking
before assuming the udev rule itself is wrong:
- **A conflicting process already has the IMU open.** The desktop
  `iio-sensor-proxy` system service (ships with most desktop Ubuntu/GNOME
  installs; used for screen auto-rotation) grabs any accel/gyro IIO device
  it finds, including the RealSense's, and holds it exclusively --
  `pipeline.start()` then fails with `"iio hid device is busy or not
  found!"`. Stop it for the session: `sudo systemctl stop
  iio-sensor-proxy.service` (likely not present/relevant on a headless
  companion computer). A leftover Jupyter kernel from a previous test run
  that never called `camera.stop()` (or was never restarted) can cause the
  identical "busy" symptom -- restart the kernel if so.
- **A previous failed/partial `pipeline.start()` can leave
  `buffer/enable` stuck at `1`** under
  `/sys/bus/iio/devices/iio:deviceN/buffer/enable`, which can block
  reconfiguring the device on the next attempt. Reset it manually if
  needed: `echo 0 | sudo tee /sys/bus/iio/devices/iio:device1/buffer/enable
  /sys/bus/iio/devices/iio:device2/buffer/enable` (device numbers vary --
  confirm via `udevadm info` which `iio:deviceN` maps to the RealSense's
  `accel_3d`/`gyro_3d` triggers first).

**Companion computer (e.g. Raspberry Pi CM5) note**: this whole IMU-udev
permissions gap is general to Linux/IIO, not specific to any one machine
-- expect to need the same `99-realsense-iio.rules` file (with the `:=`
fix above) on a CM5 too. This is separate from, and unaffected by,
whichever `pyrealsense2` install path you used (see above) -- the udev
rule matters once `pyrealsense2` is installed and importable, regardless
of how it got there.

**Confirmed IMU limitation on the Raspberry Pi Foundation's CM5 kernel**
(2026-07-30, `6.12.47+rpt-rpi-2712`, Debian Trixie): `enableIMU=True`
cannot work at all on this kernel, and it's not a permissions issue --
`/lib/modules/$(uname -r)/kernel/drivers/iio/` ships only the base
`industrialio` framework; `hid_sensor_hub`, `hid_sensor_accel_3d`, and
`hid_sensor_gyro_3d` (the glue drivers that expose a HID sensor
collection like the D435i's IMU as `/dev/iio:deviceN`) are entirely
absent -- confirmed via `find /lib/modules/$(uname -r) -iname
"*hid_sensor*"` (no results) and `dmesg`, which shows the kernel binding
the IMU's HID interface with the generic `hid-generic` driver instead of
`hid-sensor-hub`. No `/dev/iio:device*` node is ever created as a result.
Getting IMU working on this specific kernel would need out-of-tree
kernel module compilation (a distinct, larger task from anything in this
guide) -- not attempted yet, tracked in
[olab_code#41](https://github.com/optimatorlab/olab_code/issues/41).
**Color and depth are unaffected** (confirmed working on this same
machine/kernel) -- they only need standard USB/UVC access, not the IIO
subsystem.

**Color-only** (the default -- behaves like any other camera class):
```python
camera = olab_camera.CameraRealSense(
    paramDict={'res_rows': 480, 'res_cols': 640, 'fps_target': 30, 'outputPort': port})
camera.start(startStream=True, port=port)
# camera.frameDeque / getFrame() / addAruco() / startStream() all work exactly
# like every other camera class.
```

**Depth + IMU, for obstacle avoidance:**
```python
camera = olab_camera.CameraRealSense(
    paramDict={'res_rows': 480, 'res_cols': 640, 'fps_target': 30, 'outputPort': port},
    enableDepth=True, enableIMU=True)
camera.start()

depth_m = camera.getDepthFrameCopy()   # float32 meters, aligned to color by default
imu = camera.getIMUData()
# {'accel': (x,y,z)|None, 'accel_timestamp_ms': float|None,
#  'gyro': (x,y,z)|None, 'gyro_timestamp_ms': float|None}
```

**Viewing depth as a live colorized stream** (instead of color): pass
`streamSource='depth'` (requires `enableDepth=True`) -- the colorized depth
image is what lands in `frameDeque`/gets streamed, using the exact same
`startStream()` call as color:
```python
camera = olab_camera.CameraRealSense(
    paramDict={'res_rows': 480, 'res_cols': 640, 'fps_target': 30, 'outputPort': port},
    enableDepth=True, streamSource='depth')
camera.start(startStream=True, port=port)
```

Notes:
- `serial_number=None` (default) auto-selects the first connected RealSense
  device; pass a specific serial to target one device when multiple are attached.
- Depth resolution/framerate default to the color stream's values, but can be
  set independently via `depth_res_rows`/`depth_res_cols`/`depth_framerate`.
- `alignDepthToColor=True` (default) keeps depth and color pixels spatially
  corresponding -- turn off only if you specifically want native depth-sensor
  resolution/framing instead.
- `enableDepthFilters=True` (default) applies pyrealsense2's spatial/
  temporal/hole-filling filters to depth. **Confirmed via real D435i
  hardware testing to make a dramatic difference** -- much less jitter/
  noise (especially at longer range) and visibly softer occlusion "shadow"
  artifacts at object edges when aligned to color (see below). Set
  `enableDepthFilters=False` only if you specifically need the extra CPU
  headroom and can tolerate noisier raw depth.
- `depth_color_scheme` (int 0-9, default `None` = SDK default) selects
  which of pyrealsense2's colorizer color schemes is used for
  `streamSource='depth'` (0=Jet, 1=Classic, 2=WhiteToBlack, 3=BlackToWhite,
  4=Bio, 5=Cold, 6=Warm, 7=Quantized, 8=Pattern, 9=Hue). Confirmed via real
  hardware that the SDK default (Jet) colors **near=blue, far=red** -- the
  reverse of the "near=red/hot=danger" convention some obstacle-avoidance
  UIs expect. Pass e.g. `depth_color_scheme=6` (Warm) for a different
  mapping if that matters for your use; look at the actual stream to judge
  which scheme reads best for your use case, since the SDK's own scheme
  descriptions don't fully capture how each one renders in practice.
- Color's own factory intrinsics are auto-populated into `camera.intrinsics`
  (so `addAruco()`/pose functions work with no manual calibration step);
  depth's native intrinsics are kept separately in `camera.depthIntrinsics`.
- **Depth-to-color alignment occlusion "shadow" artifact**: with
  `alignDepthToColor=True` (default), you may see a black offset "shadow"
  trailing real objects in the colorized depth view. This is expected --
  the depth (stereo IR) module and the RGB module sit at different
  physical positions on the device, so at object edges, some pixels
  visible to one aren't visible to the other and can't get valid aligned
  depth. `enableDepthFilters=True` (default, see above) softens this via
  hole-filling, but doesn't eliminate it -- it's inherent to the sensors'
  physical baseline, not a bug.
- Point-cloud generation is not yet supported (tracked in a separate GitHub issue).

### 6.  OpenMV cameras (GENX320 histogram preview)

`CameraOpenMV` wraps the official `openmv` host protocol client. Install the
optional dependency first:
```bash
pip install olab-camera[openmv]
```
`openmv`'s own published wheel metadata only requires Python >=3.8 -- it is
`olab_camera`'s own base `requires-python = ">=3.10"` that applies here, not
a separate or stricter floor imposed by the `openmv` extra.

This first release integrates one profile, `genx_histogram_preview`: a
fixed 320x320 grayscale event-activity preview from a single GENX320 module,
streamed through the same MJPEG/WebSocket/WebRTC paths every other backend
uses. Raw GENX320 event streaming/recording, the `mt9v034_frame_passthrough`
profile, and multi-camera sync are later work -- see
`docs/plans/olab_camera_openmv_support_plan.md`.

**What's hardware-confirmed vs. pending in this profile:** every GENX320
sensor-control call this profile issues (`csi.CSI(cid=csi.GENX320)`,
`pixformat`/`framesize`/`framerate`/`brightness`/`contrast`/`snapshot`, and
the `ioctl()` calls + constants for bias presets, anti-flicker, hot-pixel
calibration, and spatio-temporal filtering) is confirmed against
[OpenMV's own published GENX320 documentation](https://docs.openmv.io/dev/openmvcam/sensors/genx320.html)
-- not guessed. The exact numeric defaults (anti-flicker Hz windows,
hot-pixel calibration event-count/sigma) are reasonable choices, not
hardware-tuned ones, and the profile as a whole has not yet been run
against a physical board (hardware bring-up is step 5 of the plan doc, out
of scope this round) -- so "confirmed API" here means *the calls are real
and documented*, not *this exact configuration has been validated on a
GENX320*. The supplementary config/health telemetry channel this profile
also publishes is an independent, best-effort mechanism: it prints a
"not yet implemented" notice and does not affect the frame stream, pending
that same bring-up confirming the on-device channel-write primitive (which
is a separate, new protocol feature unrelated to the sensor APIs above).

```python
import olab_camera

camera = olab_camera.CameraOpenMV(devicePort='/dev/ttyACM0')
camera.start(startStream=True, port=8003)
# Visit https://localhost:8003/stream.mjpg

# Host receipt time + sequence, paired atomically with the current frame --
# use this (not a separate getFrame() call) whenever you need metadata:
frame, meta = camera.getFrameAndMeta()

camera.shutdown()
```

Notes:
- **The laptop/host connects to the OpenMV board over USB**, addressed by
  its serial device path (e.g. `/dev/ttyACM0` on Linux) -- that's what
  `devicePort` is. The `csi` module referenced above is entirely on-device
  MicroPython code running on the OpenMV board itself, talking to its
  attached GENX320 module over the board's internal sensor interface --
  it has nothing to do with any port/interface on the host machine, and
  nothing in this integration expects the host to have a CSI port of its
  own.
- **`devicePort` is the OpenMV serial path** (e.g. `/dev/ttyACM0`), not the
  same thing as `start()`'s `port` argument (the streaming port, exactly
  like every other backend). There's no discovery/default-device policy
  yet -- name the port explicitly.
- The profile's resolution (320x320) and histogram rate are fixed once
  configured; passing a different `res_rows`/`res_cols`/`framerate` to
  `start()` raises `ValueError` before any device interaction, rather than
  silently ignoring the request. `changeResolutionFramerate()` can change
  the histogram rate (via a stop/restart cycle, same as `CameraRealSense`),
  not the resolution.
- `getFrameAndMeta()` is the only way to get frame + metadata as a matched
  pair -- `getFrame()` and inspecting metadata separately are *not*
  guaranteed to correspond to the same frame, since the capture thread can
  replace the single-slot frame buffer between the two calls. The metadata
  is **host receipt time**, never a sensor-exposure timestamp.

#### Custom scripts and the helper/channel contract

Maintained profiles are recommended so you don't have to write OpenMV
MicroPython yourself. If you do write a custom script, the helper contract
it should use is a single reserved top-level name: **`_OmvHelper`** -- a
class providing versioned envelope encode/decode and channel-publish
primitives (see `olab_camera.openmv_profiles.contract` for the host-side
envelope format: `schema_version`, `profile_id`, `device_seq`,
`device_time_ms`, `kind` (`config`/`health`/`result`/`error`), `payload`).
Every maintained profile's rendered script is `_OmvHelper`'s source text,
followed by two blank lines, then the profile body calling
`_OmvHelper.publish(...)` -- a hand-assembled custom script that reuses this
convention must not itself define a top-level `_OmvHelper` name, since
that's the entire reserved collision surface.

`OpenMVDevice.runScriptFile(path)` is the low-level primitive for uploading
an arbitrary local `.py` file -- it only guarantees script upload/execution
and stdout, not profile-level channels or frame/event semantics. This is
explicitly an experimental, low-level path; the helper contract above is
what interoperates with `olab_camera`'s own tooling.

---

# Additional Tools

### Calibration

See [`calibration_example.ipynb`](https://github.com/optimatorlab/ub_code/issues/5) notebook for details.

```python
# This is copied from `calibration_example.ipynb`:
camera.intrinsics = { "640x480": {"fx": 613.9267755271052, "fy": 617.2876757419133, "cx": 326.06379688638367, "cy": 226.4726965669937, "dist": [-0.040671732389409375, 0.2205460570452358, -0.008313365917653356, 0.0025141234454979433, -0.32871689004906784]  } }
camera.intrinsics = camera._getIntrinsics()
camera.intrinsics
```
- **NOTE**: You might want to calibrate the camera for other resolutions, like `320x240`, too.

---

### Aruco Tags
- **NOTE**: You will need to calibrate the camera if you want to be able to determine the distance from a tag.

```python
# Specify the size of the ArUco tag in inches (or enter `None` if unknown)
TAG_SIZE_INCHES = 4.25   #  or None, or 4 + 3/16, etc

# Specify what type of ArUco tag you have:
ARUCO_DICTIONARY = 'DICT_APRILTAG_36h11'   # or 'DICT_4X4_250', or 'DICT_APRILTAG_16h5', etc
```

```python
# Define the "callback" function to be called on each ArUco detection:
def aruco_post_poses(argsDict):
    # This function gets called each time an aruco detection is run
    idName  = argsDict['idName']

    ids     = camera.aruco[idName].deque[0]['ids']
    corners = camera.aruco[idName].deque[0]['corners']
    centers = camera.aruco[idName].deque[0]['centers']
    for i in range(len(corners)):
        # centers give the center point, in pixels, of the tag.
        print(f"id: {ids[i]}")
        print(f"\tcenter: {centers[i]}")

    if (TAG_SIZE_INCHES is not None):
        '''
        NOTE:
        If you get an error like `Error in Aruco DICT_APRILTAG_36h11 thread: '640x480'`,
        that likely means you have not the camera calibration
        (or that you have calibrated your camera at a resolution other than '640x480'.
        '''
        # Adjust based on resolution
        res = f'{camera.res_cols}x{camera.res_rows}'
        cameraMatrix = camera.intrinsics[res]['matrix']
        dist = camera.intrinsics[res]['dist']

        # ********************************************
        # Specify the size of the marker, in [meters]
        # ********************************************
        ml = olab_utils.inches2meters(TAG_SIZE_INCHES)

        # olab_utils.findTagPoses() consolidates the loop/objPoints/solvePnP
        # boilerplate a hand-rolled version of this used to need -- see
        # https://github.com/optimatorlab/olab_code/issues/20.
        for pose in olab_utils.findTagPoses(corners, ids, ml, cameraMatrix, dist):
            # pose['tvec'] is the x/y/z translation of the marker from the origin (camera).
            # It's in [meters], since we passed `ml` in [meters].
            # tvec[0] (x) is the distance left (-) or right (+) from the camera.
            # tvec[1] (y) is the distance above (-) or below (+) the camera.
            # tvec[2] (z) is the distance away from the camera.
            tvec = pose['tvec']
            print(f"id: {pose['id']}")
            print(f"\tdistance [inches]: x: {olab_utils.meters2inches(tvec[0])}, y: {olab_utils.meters2inches(tvec[1])}, z: {olab_utils.meters2inches(tvec[2])}")
```

```python
# Start AruCo detection:
camera.addAruco(idName=ARUCO_DICTIONARY,
                fps_target=5,
                postFunction=aruco_post_poses,
                postFunctionArgs={'idName': ARUCO_DICTIONARY},
                configOverrides={},
                ids_of_interest=None,  # default is None, or provide a list of IDs to track
                decorate=True)  # default is True; set False to skip drawing detections on the stream
```

**Run the next cell when you're ready to stop the ArUco detection:**
```python
camera.aruco[ARUCO_DICTIONARY].stop()
```

---

### Detect Barcodes and QR Codes

```python
# Create a function that will be called each time a barcode or QR code is detected:
def postBarcode(argsDict):
    # print(camera.barcode['default'].deque[0])
    for i in range(len(camera.barcode['default'].deque[0]['data'])):
        print(f"""data: {camera.barcode['default'].deque[0]['data'][i]},
                codeType: {camera.barcode['default'].deque[0]['codeTypes'][i]},
                quality: {camera.barcode['default'].deque[0]['qualities'][i]},
                corners: {camera.barcode['default'].deque[0]['corners'][i]}""")
```

```python
# Start the barcode reader, pointing to the `postBarcode()` function:
camera.addBarcode(fps_target=5,
                  postFunction=postBarcode,
                  decorate=True)  # default is True; set False to skip drawing detections on the stream
```

**Run the next cell when you're ready to stop the barcode reader:**

```python
camera.barcode['default'].stop()
```

---

### Generate a printable QR tag

`olab_utils.generateQR()` generates a printable QR-code tag image for use as
a physical fiducial with the detection/pose pipeline below.

```python
# Generate a 4.25-inch QR tag at 300 DPI, saved as a lossless PNG (the only
# formats accepted are .png/.tif/.tiff/.bmp -- a lossy format like JPEG can
# corrupt the sharp module edges QR decoding depends on):
img = olab_utils.generateQR(
    payload='PAD_A',
    tag_size_inches=4.25,
    dpi=300,
    outputFile='pad_a_tag.png')
```

- `tag_size_inches` sizes **the QR symbol itself**. With the default
  `border=0`, the saved file *is* the QR symbol -- no extra white margin
  baked in -- so the file's own size always equals `tag_size_inches`
  exactly, matching what a print dialog's "actual size"/100% setting, or
  an image editor's "set image size", controls, and exactly what a ruler
  measures on the printed page. No separate "which edge do I measure" step
  is needed.
- **Print `outputFile` at actual size / 100% scale**, with any "fit to
  page"/"scale to fit" print option turned **off**. The file has `dpi`
  embedded as real metadata, but a print dialog's "fit to page" ignores
  that and resamples the image to the page size regardless -- so DPI
  metadata alone does not guarantee the printed tag is the size you asked
  for.
- After printing, **physically measure the printed tag with a ruler**
  before trusting it for pose -- it should match `tag_size_inches` (within
  +/-1 pixel, at the `dpi` you generated it with); confirming it with a
  ruler catches any print-pipeline scaling that DPI metadata alone
  couldn't prevent. This same `tag_size_inches` value is what to pass (in
  meters, via `inches2meters()`) into `findTagPose()`/`findTagPoses()`
  below (see `TAG_SIZE_INCHES`).
- The default `border=0` means the *file* has no quiet zone baked in --
  a QR code's quiet-zone requirement is about the physical scanning
  environment having clean white space around the tag, not about the file
  containing that space itself. An ordinary printed page's own margins
  already provide this for a single tag printed on its own sheet; just
  don't crop tight to the black pixels or place the tag immediately
  against other dark content. Pass a nonzero `border` only if you need
  that margin baked into the file itself (e.g. compositing the tag into a
  design that doesn't otherwise leave it any white space) -- doing so
  makes the file **larger** than `tag_size_inches` (the QR symbol still
  prints at exactly `tag_size_inches`; the border is added on top, not
  carved out of it), so the physical size you measure/use for pose is still
  `tag_size_inches` unchanged -- a nonzero border only affects the saved
  file's total size, never what `tag_size_inches` means. `findTagPoses()`
  requires this value converted to **meters** (`olab_utils.inches2meters(tag_size_inches)`
  -- see `TAG_SIZE_INCHES`/`ml` in the examples above); `findTagPose()`
  itself is unit-agnostic and accepts whatever consistent unit system its
  caller's `objPoints` use.
- Optional `logo=<path or numpy image>` embeds a logo centered on the tag
  (composited inside an opaque backing square, capped at `logo_scale`,
  verified to still decode after compositing); optional `label=<str>`
  prints a human-readable caption below the tag (e.g. the payload itself).
  See the function's docstring for the full parameter set (error
  correction level, colors, quiet-zone width, etc).

---

### QR Codes

`addQR()` is QR-only (unlike `addBarcode()`, which scans any symbology pyzbar
supports) and lets you pick the decoder. It reports the same shape of thing
ArUco/barcode do -- payload data + corners each cycle -- and, like ArUco,
does not compute distance/pose itself.

- **NOTE**: `decoder='cv2'` (the default) may print a native OpenCV warning
  like `QR: ECI is not supported properly` for QR codes that embed an ECI
  segment (common -- many phone/web QR generators add one to declare
  UTF-8). It's benign: decoding still succeeds despite the warning. If it's
  too noisy (it repeats every detection cycle a tag with ECI is in view),
  suppress OpenCV's own logging once, near the top of your notebook/script.
  The API moved between OpenCV versions -- newer builds (roughly 4.11+)
  only expose `cv2.utils.logging.setLogLevel()`; older builds only expose
  the now-removed top-level `cv2.setLogLevel()` -- so try both and fall
  back gracefully:
  ```python
  def suppress_cv2_warnings():
      try:
          import cv2.utils.logging as cvlog
          cvlog.setLogLevel(cvlog.LOG_LEVEL_ERROR)
          return True
      except (ImportError, AttributeError):
          pass
      try:
          cv2.setLogLevel(2)   # 2 == LOG_LEVEL_ERROR numerically, stable across versions
          return True
      except AttributeError:
          return False

  suppress_cv2_warnings()
  ```
  This is a process-wide OpenCV setting (not specific to QR detection), so
  it also quiets any other OpenCV WARNING-level messages elsewhere in your
  session -- pass `LOG_LEVEL_WARNING`/`3` instead of `LOG_LEVEL_ERROR`/`2`
  if you need those back.

```python
# Create a function that will be called each time a QR code is detected:
def postQR(argsDict):
    idName = argsDict['idName']
    for i in range(len(camera.qr[idName].deque[0]['data'])):
        print(f"""data: {camera.qr[idName].deque[0]['data'][i]},
                corners: {camera.qr[idName].deque[0]['corners'][i]}""")
```

```python
# Start QR detection. decoder='cv2' (default) uses cv2.QRCodeDetector, which
# is more robust to skewed/oblique viewing angles than pyzbar and is the
# right choice if you plan to compute pose (see below). decoder='pyzbar' is
# also available for generic use.
camera.addQR(idName='default',
             decoder='cv2',
             postFunction=postQR,
             postFunctionArgs={'idName': 'default'},
             ids_of_interest=None,  # default is None, or provide a list of payloads to track
             decorate=True)  # default is True; set False to skip drawing detections on the stream
```

**Run the next cell when you're ready to stop QR detection:**
```python
camera.qr['default'].stop()
```

- **NOTE**: You will need to calibrate the camera if you want to be able to determine the distance from a tag.

```python
# Specify the size of the QR tag in inches (or enter `None` if unknown)
TAG_SIZE_INCHES = 4.25   #  or None, or 4 + 3/16, etc
```

```python
# Create the "callback" function to be called on each QR detection --
# this is exactly the same pattern as `aruco_post_poses()` above:
def qr_post_poses(argsDict):
    idName = argsDict['idName']

    corners = camera.qr[idName].deque[0]['corners']
    data    = camera.qr[idName].deque[0]['data']
    for i in range(len(corners)):
        print(f"data: {data[i]}")

    if (TAG_SIZE_INCHES is not None):
        res = f'{camera.res_cols}x{camera.res_rows}'
        cameraMatrix = camera.intrinsics[res]['matrix']
        dist = camera.intrinsics[res]['dist']

        ml = olab_utils.inches2meters(TAG_SIZE_INCHES)

        # For a world-frame position (e.g. precision landing), first tell the
        # camera where it is (once, or whenever it changes):
        #     camera.setPose(x=..., y=..., z=..., roll=..., pitch=..., yaw=...)
        #     camera.setExtrinsics(x=..., y=..., z=..., roll=..., pitch=..., yaw=...)  # optional, defaults to identity mount
        # olab_utils.findTagPoses() composes to world coordinates automatically
        # once camera.pose is set (worldPosition/worldOrientation are None
        # otherwise) -- see https://github.com/optimatorlab/olab_code/issues/20.
        poses = olab_utils.findTagPoses(corners, data, ml, cameraMatrix, dist,
                                         cameraPose=camera.pose, cameraExtrinsics=camera.extrinsics)
        for pose in poses:
            tvec = pose['tvec']
            print(f"data: {pose['id']}")
            print(f"\tdistance [inches]: x: {olab_utils.meters2inches(tvec[0])}, y: {olab_utils.meters2inches(tvec[1])}, z: {olab_utils.meters2inches(tvec[2])}")

            if (pose['worldPosition'] is not None):
                print(f"\tworld position: {pose['worldPosition']}")

                # And the inverse -- if you instead know the tag's own world
                # position (e.g. a landing pad at a known GPS/local position),
                # you can solve for the vehicle's own pose:
                tagPose = {'position': pose['worldPosition'], 'orientation': pose['worldOrientation']}
                (vehiclePos, vehicleOrientation) = olab_utils.findCameraPoseGlobal(tagPose, pose['rvec'], pose['tvec'], camera.extrinsics)
```

```python
# Start QR detection, pointing to the `qr_post_poses()` function:
camera.addQR(idName='default',
             decoder='cv2',
             postFunction=qr_post_poses,
             postFunctionArgs={'idName': 'default'},
             decorate=True)  # default is True; set False to skip drawing detections on the stream
```

- **NOTE**: `findTagPose()`/`findTagPoseGlobal()`/`findCameraPoseGlobal()`/`findTagPoses()`
  work for ArUco markers too -- they operate on any single planar tag's 4
  corners / solvePnP rvec+tvec, not just QR. (These were formerly named
  `arucoFindPose()`/`arucoFindPoseGlobal()`/`arucoFindCameraPoseGlobal()`;
  the old names still work but are deprecated -- see
  https://github.com/optimatorlab/olab_code/issues/21.)


---

### Face Detection

`addFaceDetect()` uses OpenCV's built-in YuNet DNN model (`cv2.FaceDetectorYN`)
-- besides `confidence`/`corners` (a bounding box per face), it also reports
`landmarks`: 5 `(x, y)` points per face (right eye, left eye, nose tip,
right mouth corner, left mouth corner).

```python
# Create a function that will be called each time a face is detected:
def postFaceDetect(argsDict):
    # print(camera.facedetect['default'].deque[0])
    for i in range(len(camera.facedetect['default'].deque[0]['confidence'])):
        print(f"{i} - confidence: {camera.facedetect['default'].deque[0]['confidence'][i]}, "
              f"corners: {camera.facedetect['default'].deque[0]['corners'][i]}, "
              f"landmarks: {camera.facedetect['default'].deque[0]['landmarks'][i]}")
```

```python
# Start the face detection
#
# Optional:  Specify where the OpenCV face detection models are saved.
# None --> Use default `cv2_dnn_models` included with olab_camera package.
modelPath = None
camera.addFaceDetect(fps_target=5,
                     postFunction=postFaceDetect,
                     conf_threshold=0.7,
                     model_name='face_detection_yunet_2023mar.onnx',  # or '..._int8.onnx' for lower resource usage
                     device='cpu',
                     modelPath=modelPath,
                     decorate=True,  # default is True; set False to skip drawing detections on the stream
                     drawLandmarks=True)  # default is True; set False to skip drawing the 5 facial landmark points
```

**Run the next cell when you're ready to stop the face detection:**
```python
camera.facedetect['default'].stop()
```

---

### Generic local multi-object tracking

Install the local-only tracking extra in this checkout's virtual environment:

```bash
./venv/bin/pip install -e "packages/olab_camera[tracking]"
```

`addTracker()` owns tracker state, callbacks, the latest-result deque, and an
optional stream overlay; it does not own a detector or a worker thread. Submit
one normalized detector result to explicitly named trackers to compare the
same sequence under SORT, ByteTrack, OC-SORT, or BoT-SORT. `xyxy` is the only
required field; `class_id`, `class`, `class_conf`, and `masks` may be omitted.

```python
from olab_camera import Camera

camera = Camera({'res_rows': 480, 'res_cols': 640, 'fps_target': 30})
for name, algorithm in [('sort', 'sort'), ('byte', 'bytetrack'),
                        ('oc', 'ocsort'), ('bot', 'botsort')]:
    camera.addTracker(name, algorithm, decorate=False)

results = camera.updateTrackers(
    {'xyxy': [[20, 30, 120, 190]], 'class_id': [0],
     'class': ['person'], 'class_conf': [0.95]},
    ('sort', 'byte', 'oc', 'bot'), timestamp=12.5,
)
```

Each successful mapping entry is that tracker's normalized latest result;
`None` means that named backend/callback failed. A malformed payload, or an
invalid selection containing one or more string names, returns each supplied
string name as `None` without advancing any backend.
Track IDs are local to one tracker instance and reset when it is stopped or
re-added. The same stream must have only one tracking owner: prefer
Ultralytics' native tracking when it meets the need, and do not feed already
native-tracked results into this API. RF-DETR's existing
`tracker='bytetrack'` remains independent.

All four initial algorithms run locally and this API downloads neither models
nor tracker assets. McByte is intentionally deferred; its mask mode will need
explicit pre-provisioned local assets. The `trackers` dependency may install
plain OpenCV; if that displaces this package's required contrib runtime, use
the README's existing OpenCV recovery command.

### RF-DETR (local detection and segmentation)

RF-DETR is optional: install `olab-camera[rfdetr]` and provision model
checkpoints in `~/Projects/olab_models/` before starting the program. Relative
`weights_path` names resolve in that shared user directory; absolute paths are
also accepted. Keep the project's
`opencv-contrib-python` runtime active. `addRFDETR()` never downloads weights
and never uses a hosted Roboflow service.

#### One-time model provisioning

The published starter checkpoints are hosted by Roboflow.  If that external
download is permitted, make it an explicit setup action (not a runtime side
effect) and save the model in the shared local root:

```bash
mkdir -p "$HOME/Projects/olab_models"
python -c "from pathlib import Path; from rfdetr.assets.model_weights import download_pretrain_weights; download_pretrain_weights(str(Path.home() / 'Projects' / 'olab_models' / 'rf-detr-small.pth'))"
```

For the segmentation-plus-ByteTrack example, provision its separate checkpoint:

```bash
python -c "from pathlib import Path; from rfdetr.assets.model_weights import download_pretrain_weights; download_pretrain_weights(str(Path.home() / 'Projects' / 'olab_models' / 'rf-detr-seg-small.pt'))"
```

For a strict air-gapped workflow, obtain and approve the same checkpoint by
your normal artifact-transfer process, then place it at
`~/Projects/olab_models/rf-detr-small.pth`.  The camera API and the runnable
examples make no network request in either case.

RF-DETR runs on `device='cpu'` by default, avoiding an accidental CUDA setup
dependency. Pass an explicit device such as `device='cuda'` only after your
PyTorch/CUDA/cuDNN installation is known to work. The runnable USB examples
also start an MJPEG stream on port 8000 by default and print its URL; pass
`--stream-port 0` to disable it.

```python
# Detection: local checkpoint only.  The callback gets both a small normalized
# snapshot and the native supervision.Detections object for advanced use.
def rfdetr_detected(args):
    for label, box in zip(args['result']['class'], args['result']['xyxy']):
        print(label, box)

camera.addRFDETR(
    idName='warehouse-detect', task='detect', model_variant='small',
    weights_path='rf-detr-small.pth', fps_target=5, device='cpu',
    postFunction=rfdetr_detected,
)
```

```python
# Segmentation + local ByteTrack.  Each retained mask corresponds to the same
# row in class/xyxy/track_id; -1 means the tracker has not confirmed a track.
camera.addRFDETR(
    idName='warehouse-segment', task='segment', model_variant='small',
    weights_path='rf-detr-seg-small.pt', tracker='bytetrack',
    fps_target=5, maskOutline=True,
)

latest = camera.rfdetr['warehouse-segment'].deque[0]
for track_id, mask in zip(latest['track_id'], latest['masks']):
    if track_id >= 0:
        print(track_id, mask.sum())
```

### Ultralytics
The following options are documented:
- Detect
- Pose
- Oriented Bounding Box (obb)
- Segment (mask)
- Track (can be applied to `Detect`, `Pose`, and `Segment`)

All `addUltralytics()` calls below also accept `decorate=True` (default; set
False to skip drawing detections on the stream) -- shown explicitly on the
Detect example below, and applies the same way to Pose/OBB/Segment/Track.

The examples below use the YOLO 11 pre-trained models.  See https://docs.ultralytics.com/models/ for other options.

NOTE:  We should also explore the following:
- https://docs.ultralytics.com/models/rtdetr/#pretrained-models
- https://docs.ultralytics.com/models/sam-3/#training-data-scaling
- https://docs.ultralytics.com/models/mobile-sam/


#### Detect
```python
# Create a function that will be called each time an object is detected:
def postUltralyticsDetect(argsDict):
    idName = argsDict['idName']
    results = argsDict['results']

    for result in results:
        '''
        xywh = result.boxes.xywh  # center-x, center-y, width, height
        xywhn = result.boxes.xywhn  # normalized
        xyxy = result.boxes.xyxy  # top-left-x, top-left-y, bottom-right-x, bottom-right-y
        xyxyn = result.boxes.xyxyn  # normalized
        names = [result.names[cls.item()] for cls in result.boxes.cls.int()]  # class name of each box
        confs = result.boxes.conf  # confidence score of each box
        '''

        for i in range(0, len(result.boxes.cls)):
            # print(int(result.boxes.cls[i].item())
            # print(camera.ultralytics[idName].model.names[int(result.boxes.cls[i].item())])
            # print(result.boxes.conf[i].item(), result.boxes.xyxy[i].tolist())
            print(f'{result.names[int(result.boxes.cls[i].item())]} ({result.boxes.conf[i].item()}), {result.boxes.xyxy[i].tolist()}')
```

```python
# Start the object detection:
camera.addUltralytics(idName="detect",
                      model_name="yolo11n.pt",
                      conf_threshold=0.75,
                      postFunction=postUltralyticsDetect,
                      decorate=True)  # default is True; set False to skip drawing detections on the stream
```

```python
# Get list of objects that can be detected:
camera.ultralytics['detect'].model.names
```

```python
# Customize the annotation drawn on the video stream:
camera.ultralytics['detect'].drawBox   = True
camera.ultralytics['detect'].drawLabel = True
```

**Run the next cell when you're ready to stop the detection:**
```python
camera.ultralytics['detect'].stop()
```

#### Pose
```python
# Create a function that will be called each time a pose is detected:
def postUltralyticsPose(argsDict):
    idName = argsDict['idName']
    results = argsDict['results']

    '''
    `keypoints` should have 17 elements:
    0: Nose, 1: Left Eye, 2: Right Eye, 3: Left Ear, 4: Right Ear,
    5: Left Shoulder, 6: Right Shoulder, 7: Left Elbow, 8: Right Elbow, 9: Left Wrist, 10: Right Wrist,
    11: Left Hip, 12: Right Hip, 13: Left Knee, 14: Right Knee, 15: Left Ankle, 16: Right Ankle
    '''

    for result in results:
        if (result.keypoints.has_visible):
            print(f'conf: {result.keypoints.conf.tolist()}, keypoints: {result.keypoints.xy.tolist()} \n')
```

```python
# Start the pose detection:
camera.addUltralytics(idName="pose",
                      model_name="yolo11n-pose.pt",
                      conf_threshold=0.75,
                      postFunction=postUltralyticsPose,
                      drawBox = False, drawLabel=True)
```

```python
# Customize the annotation drawn on the video stream:
camera.ultralytics['pose'].drawBox   = False
camera.ultralytics['pose'].drawLabel = False
```

**Run the next cell when you're ready to stop the detection:**
```python
camera.ultralytics['pose'].stop()
```

#### Oriented Bounding Boxes (OBB)
```python
# Create a function that will be called each time an oriented object is detected:
def postUltralyticsObb(argsDict):
    idName = argsDict['idName']
    results = argsDict['results']

    for result in results:
        if (result.obb):
            for i in range(0, len(result.obb.cls)):
                    print(f'{result.names[int(result.obb.cls[i].item())]} ({result.obb.conf[i].item()}), Center: {result.obb.xywhr[i][0:2].tolist()}')
```

```python
# Start the obb detection:
camera.addUltralytics(idName="obb",
                      model_name="yolo11n-obb.pt",
                      conf_threshold=0.65,
                      postFunction=postUltralyticsObb,
                      drawBox = True, drawLabel=True)
```

```python
# Get list of objects that can be detected:
camera.ultralytics['obb'].model.names
```

**Run the next cell when you're ready to stop the obb detection:**
```python
camera.ultralytics['obb'].stop()
```

#### Segmentation
```python
# Create a function that will be called each time an object is detected:
def postUltralyticsSegment(argsDict):
    idName = argsDict['idName']
    results = argsDict['results']

    for result in results:
        for i in range(0, len(result.boxes.cls)):
            try:
                print(f'{result.names[int(result.boxes.cls[i].item())]} ({result.boxes.conf[i].item()}), {result.boxes.xyxy[i].tolist()}')
            except Exception as e:
                print(f'Error: {e}')
```

```python
# Start the segmentation:
camera.addUltralytics(idName="segment",
                      model_name="yolo11n-seg.pt",
                      conf_threshold=0.65,
                      postFunction=postUltralyticsSegment,
                      drawBox = False, drawLabel=True,
                      maskOutline = False)
```

```python
# Customize the annotation drawn on the video stream:
camera.ultralytics['segment'].maskOutline = True
```


```python
# Get list of objects that can be detected:
camera.ultralytics['segment'].model.names
```

**Run the next cell when you're ready to stop the segmentation:**
```python
camera.ultralytics['segment'].stop()
```

#### Tracking
```python
# Create a function that will be called each time an object is detected:
def postUltralyticsTrack(argsDict):
    idName = argsDict['idName']
    results = argsDict['results']

    # print(idName)   # "track"
    for result in results:
        '''
        xywh = result.boxes.xywh  # center-x, center-y, width, height
        xywhn = result.boxes.xywhn  # normalized
        xyxy = result.boxes.xyxy  # top-left-x, top-left-y, bottom-right-x, bottom-right-y
        xyxyn = result.boxes.xyxyn  # normalized
        names = [result.names[cls.item()] for cls in result.boxes.cls.int()]  # class name of each box
        confs = result.boxes.conf  # confidence score of each box
        '''
        for i in range(0, len(result.boxes.cls)):
            try:
                print(f'ID: {result.boxes.id[i].item()} - {result.names[int(result.boxes.cls[i].item())]} ({result.boxes.conf[i].item()}), {result.boxes.xyxy[i].tolist()}')
            except Exception as e:
                print(f'Error: {e}')
```

```python
# Tracking can be done with detect, pose, or segment models.
# Choose one of the following
model_name = "yolo11n.pt"          # detect
# model_name = "yolo11n-pose.pt"   # pose
# model_name = "yolo11n-seg.pt"    # segment
```

```python
# Start tracking:
camera.addUltralytics(idName="track",
                      model_name=model_name,
                      conf_threshold=0.65,
                      postFunction=postUltralyticsTrack,
                      drawBox = False, drawLabel=True)
```

```python
# Customize the annotation drawn on the video stream:
camera.ultralytics['track'].drawBox = False
camera.ultralytics['track'].drawLabel = True
```

**Run the next cell when you're ready to stop the tracking:**
```python
camera.ultralytics['track'].stop()
```

---

### Timelapse
Take photos at regular intervals, saving them to a directory on your computer.

```python
'''
outputDir: Folder where the photos will be saved.  Use relative directory or absolute path.
secBetwPhotos: How many seconds between photo captures.
timeLimitSec: Keep capturing photos for this many seconds.  `None` --> No limit.
delayStartSec: How many seconds to wait before taking the first picture.
postPostFunction: Function to call when the timelapse is finished.
'''

camera.addTimelapse(outputDir        = 'timelapse_photos',
                    secBetwPhotos    = 3,
                    timeLimitSec     = None,
                    delayStartSec    = 0,
                    postPostFunction = None)
```

**Run the next cell when you're ready to stop the timelapse:**
```python
camera.timelapse['default'].stop()
```

---

### Circle and Text Overlays

You can add circle and text overlays to the video stream. Both return a `(decorationID, params)` tuple. The `params` dict is mutable — update its values to change the overlay dynamically each frame.

#### Circle
```python
# Add a circle at (center_x, center_y) with radius 50
cid, circle_params = camera.addCircle(center=(320, 240), radius=50, thickness=3, color=(150, 25, 25))
```

```python
# Move the circle dynamically:
circle_params['center'] = (400, 300)
circle_params['radius'] = 75
circle_params['color'] = (0, 255, 0)
```

```python
# Remove the circle:
camera.removeDecoration(cid)
```

#### Text
```python
# Add text at position (x, y)
tid, text_params = camera.addText(text="Hello", position=(100, 100), fontScale=0.7, thickness=2, color=(255, 255, 255))
```

```python
# Update the text dynamically:
text_params['text'] = "World"
text_params['position'] = (200, 200)
text_params['color'] = (0, 0, 255)
```

```python
# Remove the text:
camera.removeDecoration(tid)
```

---

### Video from Pics
- TBD.  First, run timelapse to save photos to a directory, then process the photos in that directory into an `.mpeg` video.

### Region of Interest (ROI)
- Deprecated.  This functionality would (poorly) track a selected object.  The Ultralytics tracking is better (although it's limited to trained objects).
- `addROI()` also accepts `decorate=True` (default; set `False` to track without drawing the tracking box on the stream), same as the other detection methods above.
