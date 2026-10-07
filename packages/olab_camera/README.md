# olab_camera

Camera capture, local recording, and network streaming (MJPEG/WebSocket/WebRTC)
for lab robotics projects, plus ArUco, barcode/QR, face-detection, and YOLO
computer-vision helpers. Requires [`olab_utils`](https://github.com/optimatorlab/olab_code/tree/main/packages/olab_utils),
resolved automatically from PyPI as a regular dependency.

Migrated from `~/Projects/ub_code/ub_camera` (a flat, non-`src/`-layout
single-file module — `ub_code` never had automated tests) per
[`docs/plans/olab_packages_reorg_plan.md`](https://github.com/optimatorlab/olab_code/blob/main/docs/plans/olab_packages_reorg_plan.md),
Migration sequence step 4.

## Installing

```bash
python3 -m venv venv
source venv/bin/activate
pip install olab-camera
```

Add extras as needed by appending `[...]` to the `olab-camera` line above,
one at a time:

| Extra | Adds | Notes |
|---|---|---|
| `yolo` | `ultralytics` (YOLO object detection) | Both this extra and the base package depend on plain `opencv-python` -- no conflicting install. |
| `tracking` | local SORT, ByteTrack, OC-SORT, and BoT-SORT | Detector-agnostic comparison API; no models, hosted inference, or automatic downloads. |
| `rfdetr` | local RF-DETR detection/segmentation plus Roboflow ByteTrack | Supply an existing local checkpoint. Relative names resolve in `~/Projects/olab_models/`; absolute paths also work. The usage guide documents an explicit, one-time optional provisioning download. This feature never downloads weights or uses hosted inference at runtime. |
| `websocket` | `websockets` | WebSocket + JPEG streaming. |
| `webrtc` | `aiortc`, `aiohttp` | WebRTC streaming. |
| `ros` | — | **Dropped as a pip extra** (`rospy`/`sensor-msgs` aren't on PyPI). To use `CameraROS`, install ROS via `apt`/`rosdep` and source your ROS environment — `olab-camera` then picks up `rospy` from that environment's Python path on its own. |

`all` bundles every extra above (`yolo`, `tracking`, `rfdetr`,
`websocket`, `webrtc`, `realsense`, `openmv`, `av`) together. Note that
`av`'s `olab-audio` dependency needs PortAudio's headers
(`portaudio19-dev` on Ubuntu/Debian) to build `pyaudio` from source.

**Local development**, against an `olab_code` checkout:

```bash
pip install -e "packages/olab_utils"
pip install -e "packages/olab_camera"                       # base
pip install -e "packages/olab_camera[yolo,websocket,webrtc]" # + extras
```

**`olab-camera` depends on plain `opencv-python`**, the same package
`ultralytics`/`trackers`/`rfdetr` already require -- installing `[yolo]`,
`[tracking]`, `[rfdetr]`, or `[all]` no longer installs a second, conflicting
`cv2` distribution (closes #70). ArUco, QR, and face-detection all work on
plain `opencv-python`; only `Camera.addROI()`'s classic object trackers other
than `'MIL'` (any case) still need `opencv-contrib-python`. If you want those:

```bash
pip uninstall -y opencv-python opencv-contrib-python
pip install "opencv-contrib-python>=4.10.0"
```

Installing `opencv-contrib-python` **on top of** an existing plain
`opencv-python` install (instead of uninstalling both first) does make the
extra trackers available, but leaves two distributions sharing the same
`cv2/` files -- a later `[yolo]`/`[tracking]`/`[rfdetr]` install, reinstall,
or uninstall can then clobber or delete `cv2` entirely. Uninstall both first,
as above, for a clean result. Note this re-creates the plain-vs-contrib
conflict for any of those three extras, since they require plain
`opencv-python` themselves.

**Upgrading from an install that predates this change** (when `olab-utils`
depended on `opencv-contrib-python`): the same clean-swap commands apply --
uninstall both `opencv-python` and `opencv-contrib-python`, then install
`opencv-python`:

```bash
pip uninstall -y opencv-python opencv-contrib-python
pip install "opencv-python>=4.10.0"
```

After installation:

```python
import olab_camera, olab_utils
```

## GENX320 modes

The OpenMV backend uses the same `CameraOpenMV` lifecycle and browser-stream
interfaces as the other cameras. Select a mode with `profile=`:

```python
from olab_camera import CameraOpenMV

# Provisional default: normal 320x320 histogram pixels.
cam = CameraOpenMV('/dev/ttyACM0', profile='genx_histogram_preview')

# Histogram pixels with optional on-board movement-region telemetry/overlay.
regions = CameraOpenMV('/dev/ttyACM0', profile='genx_histogram_regions')

# Raw ON/OFF events, rendered back into normal preview frames. Register these
# before start(); they execute away from USB acquisition and are bounded.
raw = CameraOpenMV('/dev/ttyACM0', profile='genx_raw_events')
raw.addEventCallback(lambda batch: print(batch.count))
raw.addEventRecorder(outputDir='genx-session')
```

Use `start()` / `startStream()` / `stop()` normally. A raw session is separate
from histogram acquisition; it supplies typed EVT2.0 batches to callbacks and
a derived browser-viewable preview, with explicit drop counters in `eventStats`.
Name the actual OpenMV CDC device explicitly because ACM numbering can change
after reconnects.

## TLS certificates

Every streaming protocol (including the MJPEG default) serves over
HTTPS/WSS. `olab_camera` auto-generates a fresh, machine-local self-signed
certificate the first time you actually start a stream (not when you
construct a `Camera`) at `~/.olab_camera/ssl/` (owner-only permissions),
via the `cryptography` library — no bundled/shared private key, no
platform-specific tooling. Capture-only use of a `Camera` never touches
the filesystem for TLS. See [`docs/deployment.md`](https://github.com/optimatorlab/olab_code/blob/main/packages/olab_camera/docs/deployment.md)
for custom certificates, fleet deployment via a lab-private CA and
per-device leaf certs (zero browser TLS warnings without a shared private
key), and reverse-proxy deployment.

## Streaming Protocols

| Protocol | Extra install | Typical latency | Browser endpoint | Multi-client |
|---|---|---|---|---|
| **MJPEG** (default) | None | 200–500 ms | `https://host:PORT/stream.mjpg` | Yes |
| **WebSocket + JPEG** | `olab-camera[websocket]` | 100–300 ms | see [`docs/deployment.md`](https://github.com/optimatorlab/olab_code/blob/main/packages/olab_camera/docs/deployment.md) | Yes |
| **WebRTC** | `olab-camera[webrtc]` | 50–150 ms | `https://host:PORT/webrtc` | Yes |

```python
camera.startStream(port=8000)                     # MJPEG, default
camera.startStream(port=8001, protocol='websocket')
camera.startStream(port=8002, protocol='webrtc')
```

## Further reading

- Usage tutorial (camera init, ArUco, barcode/QR, face detection, YOLO
  variants, tracking, frame decoration): [`docs/usage_guide.md`](https://github.com/optimatorlab/olab_code/blob/main/packages/olab_camera/docs/usage_guide.md)
- Streaming protocols, custom TLS certs, reverse-proxy deployment: [`docs/deployment.md`](https://github.com/optimatorlab/olab_code/blob/main/packages/olab_camera/docs/deployment.md)
- Local HTTPS camera/feature browser playground: install `olab-playground`
  alongside this package; see `packages/olab_playground/README.md` in the
  olab_code checkout.
- Extending the package (adding a camera class or feature class, code
  organization, testing your changes): [`docs/developer_guide.md`](https://github.com/optimatorlab/olab_code/blob/main/packages/olab_camera/docs/developer_guide.md)
