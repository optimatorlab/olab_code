# olab_camera

Camera capture, local recording, and network streaming (MJPEG/WebSocket/WebRTC)
for lab robotics projects, plus ArUco, barcode/QR, face-detection, and YOLO
computer-vision helpers. Requires [`olab_utils`](../olab_utils/). Unlike a
normal PyPI dependency, `olab-utils` isn't published anywhere `pip` can
resolve it from by name — **install both together explicitly**, as shown
below; `pip install olab-camera` alone will fail to resolve `olab-utils`.

Migrated from `~/Projects/ub_code/ub_camera` (a flat, non-`src/`-layout
single-file module — `ub_code` never had automated tests) per
[`docs/plans/olab_packages_reorg_plan.md`](../../docs/plans/olab_packages_reorg_plan.md),
Migration sequence step 4.

## Installing

Normal installation (no `olab_code` checkout required) — base `olab-camera`
plus its required `olab-utils` dependency:

```bash
python3 -m venv venv
source venv/bin/activate
pip install \
  "olab-utils @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_utils" \
  "olab-camera @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_camera"
```

Once release wheels exist, prefer pinning each release's exact URL and
SHA-256 hash instead of a git reference — both `olab-utils`'s and
`olab-camera`'s.

Add extras as needed by appending `[...]` to the `olab-camera` line above,
one at a time:

| Extra | Adds | Notes |
|---|---|---|
| `yolo` | `ultralytics` (YOLO object detection) | Handles the `opencv-contrib-python`/`opencv-python` conflict below correctly on its own. |
| `websocket` | `websockets` | WebSocket + JPEG streaming. |
| `webrtc` | `aiortc`, `aiohttp` | WebRTC streaming. |
| `ros` | `rospy`, `cv-bridge`, `sensor-msgs` | **Not installable via plain `pip`** — these packages aren't on PyPI. Only add this extra inside an existing ROS-configured environment (e.g. `apt`-installed ROS packages already on the Python path); untested/undocumented outside that setup. |

`all` bundles `yolo`, `ros`, `websocket`, and `webrtc` together — since
`ros` isn't plain-pip-installable, only use `all` inside a ROS environment;
otherwise request extras individually.

**Local development**, against an `olab_code` checkout:

```bash
pip install -e "packages/olab_utils"
pip install -e "packages/olab_camera"                       # base
pip install -e "packages/olab_camera[yolo,websocket,webrtc]" # + extras (ros needs a ROS env, see above)
```

**Watch the install order if you separately install `ultralytics`** (the
`yolo` extra already handles this correctly): `ultralytics` pulls in
`opencv-python`, which conflicts with `opencv-contrib-python` (required here
for ArUco/face-detection support) — both cannot be installed at once. If you
hit ArUco/DNN import errors after installing extra packages by hand:

```bash
pip uninstall -y opencv-python opencv-contrib-python
pip install "opencv-contrib-python>=4.10.0"
```

After installation:

```python
import olab_camera, olab_utils
```

## TLS certificates

Every streaming protocol (including the MJPEG default) serves over
HTTPS/WSS. `olab_camera` auto-generates a fresh, machine-local self-signed
certificate the first time you actually start a stream (not when you
construct a `Camera`) at `~/.olab_camera/ssl/` (owner-only permissions),
via the `cryptography` library — no bundled/shared private key, no
platform-specific tooling. Capture-only use of a `Camera` never touches
the filesystem for TLS. See [`docs/deployment.md`](docs/deployment.md)
for custom certificates and reverse-proxy deployment (zero browser TLS
warnings).

## Streaming Protocols

| Protocol | Extra install | Typical latency | Browser endpoint | Multi-client |
|---|---|---|---|---|
| **MJPEG** (default) | None | 200–500 ms | `https://host:PORT/stream.mjpg` | Yes |
| **WebSocket + JPEG** | `olab-camera[websocket]` | 100–300 ms | see [`docs/deployment.md`](docs/deployment.md) | Yes |
| **WebRTC** | `olab-camera[webrtc]` | 50–150 ms | `https://host:PORT/webrtc` | Yes |

```python
camera.startStream(port=8000)                     # MJPEG, default
camera.startStream(port=8001, protocol='websocket')
camera.startStream(port=8002, protocol='webrtc')
```

## Further reading

- Usage tutorial (camera init, ArUco, barcode/QR, face detection, YOLO
  variants, tracking, frame decoration): [`docs/usage_guide.md`](docs/usage_guide.md)
- Streaming protocols, custom TLS certs, reverse-proxy deployment: [`docs/deployment.md`](docs/deployment.md)
- Extending the package (adding a camera class or feature class, code
  organization, testing your changes): [`docs/developer_guide.md`](docs/developer_guide.md)
