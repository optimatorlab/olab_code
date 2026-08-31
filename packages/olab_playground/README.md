# olab_playground

`olab-playground` is a local-first HTTPS browser interface for exploring
existing `olab_code` package APIs. V1 provides `olab_camera` constructors,
streams, and feature APIs; future `olab_audio` and `olab_rf` panels belong here
without changing the package boundary. It is a
pure-Python process: it does not require NATS, Node.js, npm, or a web framework.

```bash
pip install -e packages/olab_camera -e packages/olab_playground
olab-playground
olab-playground --port 8765 --stream-port-range 8000:8099
```

The control page is permanently loopback-only at `https://127.0.0.1:8765`.
This is intentional: it can control physical hardware and browse local paths,
so it must not be reachable by a guest who learns a stream URL. The CameraUSB
form starts private streams on loopback by default. Its unchecked
**LAN-visible** toggle deliberately exposes only the chosen stream on the LAN;
it also shows the stream URL, lets the user override the advertised IP/name,
and offers current, generated development, or selected certificate material.

The control page and streams use HTTPS/WSS. On a phone or another workstation,
trust the stream certificate first. The default generated certificate is for
`localhost`; for LAN IP/DNS access, generate a certificate with a matching SAN
or provide a lab-issued pair through the loopback certificate picker. See
`packages/olab_camera/docs/deployment.md` for the existing certificate commands.

The page owns one active camera/AVWebcam session.  Reloading reconnects to that
same session.  Starting another backend replaces it.  It exposes MJPEG,
WebSocket+JPEG, and experimental WebRTC; every selected stream also has a
direct link for troubleshooting/certificate acceptance.

CameraUSB performs one source discovery when the page opens and has an explicit
Rescan control because probing hardware can be visible to drivers. It excludes
the active device and recommends usable color sources while keeping secondary
interfaces in a collapsible section. CameraUSB shows
discovered sources, a custom path/URL field, common settings, docstring-derived
tooltips, ROS-only topics, list editors for stream IP rules, and advanced raw
JSON/override controls. Model/output file browsing is restricted to `--model-root` (default
`~/Projects/olab_models`) and `--output-root` (default `~/Videos`).  Local
YOLO/RF-DETR paths are required; the playground never downloads weights or uses
hosted inference.

`CameraBosonDual` (RHP-BOS-DS-IF thermal+visible board, via an HDMI-to-USB
capture dongle) has its own guided form too, mirroring CameraUSB's: a
device-source dropdown, IP allowlist/blocklist list editors, and collapsed
Override/Advanced sections. Its hardware scan is a separate, slower endpoint
from CameraUSB's -- this capture dongle needs a few seconds (and the exact
V4L2 backend/FOURCC/resolution CameraBosonDual itself uses) to lock onto its
incoming HDMI signal before it produces a frame, so a bare, instant scan
(the kind every other backend's discovery uses) never finds it at all. The
Resolution field (`720p60`/`1080p60`) only tells the capture dongle what to
request -- it does not configure the board's own output mode, which must
already be set (Windows GUI or SBUS/PWM -- see `docs/usage_guide.md`'s
CameraBosonDual section and issue #60) before starting.

## Explicit local YOLO provisioning

Before starting the browser UI, activate the `olab_code` virtual environment,
change to the configured model root (normally `~/Projects/olab_models`), and
instantiate exactly the models you want in a Python REPL. This deliberately
puts any one-time download under the user's control:

```pycon
>>> from ultralytics import YOLO
>>> YOLO("yolo11n.pt")
>>> YOLO("yolo11n-seg.pt")
>>> YOLO("yolo11n-cls.pt")
>>> YOLO("yolo11n-pose.pt")
>>> YOLO("yolo11n-obb.pt")
>>> YOLO("yolo26n.pt")
>>> YOLO("yolo26n-seg.pt")
>>> YOLO("yolo26n-cls.pt")
>>> YOLO("yolo26n-pose.pt")
>>> YOLO("yolo26n-obb.pt")
```

Those are the YOLO11/YOLO26 detect, segment, classify, pose, and OBB small
filenames. `track` uses the same plain detect checkpoint as `detect`; there is
no `-track` filename family. Browser startup never downloads a model.

If a manual Ultralytics installation replaces the contrib OpenCV build, recover
CSRT support with the existing `olab_camera` procedure:

```bash
python -m pip uninstall -y opencv-python opencv-contrib-python
python -m pip install "opencv-contrib-python>=4.10.0"
python -c "import cv2; print(cv2.__version__, hasattr(cv2, 'TrackerCSRT_create'))"
```

The browser shows all ordinary public arguments and defaults. Structured
values use JSON; callback and test-injection arguments are shown as Python-only
because a web form cannot safely construct them. `addAruco` is the exception:
it has a guided marker-dictionary selector, integer ID list, optional drawing
overrides, and a fixed local callback catalog. The page never accepts backend
Python source; the current demo callback only logs detected IDs (and optional
centers) locally. Successful actions appear in the copyable Python-equivalent
pane.

## Manual QA

Before relying on the page in class, validate it with an actual available
camera/model: start/stop, each applicable feature, all three stream modes,
reload/session rehydration, and a LAN phone for an explicitly LAN-visible
stream. Verify certificate SAN/trust behavior separately for the loopback
control page and each stream.
