# Issue #53: `olab-camera-playground` plan

## Goal

Add a simple, local-first browser playground for `olab_camera`.  It is a
developer-facing lab and course tool: users can see every practical public
constructor/feature argument, start one camera session, exercise features,
view the stream and results, then copy the equivalent Python into a prototype.
It must compose the existing camera classes and feature APIs; it must not add
or replace camera backend behavior.

## Product decisions

- The server owns camera state.  A browser refresh or another browser (such as
  a phone on the lab LAN) reconnects to the same session; Stop or process exit
  releases its hardware.  V1's UI permits one active session, while server
  state is keyed by a session ID so a later multi-camera UI does not require a
  structural rewrite.
- The UI is local/trusted-LAN tooling, not an authenticated production service.
  The launcher binds to `127.0.0.1` by default and accepts an explicit
  `--host 0.0.0.0` LAN override.  It serves the control UI over HTTPS as well
  as using HTTPS/WSS for camera streams, so browser-to-server control traffic
  is encrypted on the lab LAN.
- Render ordinary arguments as typed controls, known choices as selects, and
  list/tuple/dict values as valid JSON.  A field's visible default is explicit;
  an `use default` control omits that argument from the call.  Never parse
  arbitrary Python expressions or use `eval`.
- Display callback/object-injection arguments (for example `postFunction`,
  `logger`, `rs_module`, `device_class`) as visible **Python-only advanced API
  parameters**, including their defaults, but do not make them editable.
- All inference stays local.  YOLO/RF-DETR panels require an already-present
  local model/checkpoint and never download models, use an account, or call a
  hosted API.
- The stream protocol selector exposes MJPEG (default), WebSocket+JPEG, and
  WebRTC.  WebRTC is labelled experimental.  All three use the existing TLS
  stream implementation: WebRTC is not an alternative to the self-signed
  certificate.

## Implementation

1. Add a `olab_camera.playground` module and an
   `olab-camera-playground` console entry point in the package's
   `pyproject.toml`.  Follow the standard-library `ThreadingHTTPServer` shape
   of `olab_voice.web_demo`, with CLI options for host, port, model roots, and
   output roots.  Wrap the control server with TLS, using the same existing
   generated `olab_camera` certificate flow by default and accepting an
   explicit `sslPath` for a lab-issued leaf certificate.  Print both the local
   URL and, when bound publicly, the LAN URL.  Ensure process shutdown stops
   the active camera and every feature.

2. Give the module one server-owned `PlaygroundSession`/manager.  It will hold
   the active camera object, selected backend, submitted call history,
   feature/decoration handles, errors, and generated Python statements.  Keep
   a session-ID-keyed registry but cap V1 to one active entry.  Expose narrow
   JSON APIs for status, create/start/stop camera, start/stop features,
   add/remove decorations, rescan hardware, and browse approved directories;
   do not expose generic method invocation or filesystem APIs.

3. Define a small UI metadata table for every supported public backend and
   action.  It records the callable, its signature/defaults/doc help, allowed
   select values, JSON-shaped fields, Python-only fields, and result adapter.
   It covers `CameraUSB`, `CameraPi`, `CameraPi2`, `CameraGazebo`, `CameraROS`,
   `CameraRealSense`, `CameraWebSocket`, `CameraOpenMV`, and the separate
   `AVWebcam` composition.  For the common Camera surface include
   `addAruco`, `addQR`, `addBarcode`, `addCalibrate`, `addFaceDetect`,
   `addROI`, `addTimelapse`, `addUltralytics`, `addRFDETR`, `addTracker`,
   `addCircle`, `addText`, stream controls, and ROS-publishing controls where
   applicable.  Keep this metadata in the playground only; do not modify the
   backend API or invent a replacement abstraction.

4. Build one self-contained HTML/CSS/JavaScript page served by that module.
   It has a backend selector and full constructor form; start/replace/stop
   controls; a protocol picker; a stream area; a collapsible panel/div for
   every supported feature; inline validation/errors; and a compact status
   card plus expandable raw-JSON details for every running feature.  Disable
   panels with unavailable optional packages and show the precise install
   extra.  Preserve form values during status refreshes.

5. Embed the selected stream using the existing endpoint: a persistent `<img>`
   for MJPEG, a persistent browser WebSocket JPEG client/canvas for WebSocket,
   and the existing WebRTC viewer/video endpoint for WebRTC.  Show the exact
   direct stream URL and an open-in-new-tab action.  Do not recreate an active
   preview node during status updates; update its source/connection in place,
   following OFM `rig.html`'s real-hardware-proven pattern that avoids orphaned
   MJPEG connections.  Include a TLS/LAN help panel: phones must trust both
   the control-server and stream certificates, and the existing certificate
   command needs an IP/DNS SAN for LAN URLs to avoid a hostname-mismatch
   warning.

6. Implement result adapters that read the latest existing feature state and
   serialize it safely (including NumPy arrays/scalars).  Report detector IDs,
   classes/confidences/corners; tracker objects; calibration progress/result;
   timelapse output/state; active overlay IDs and arguments; and camera stream
   protocol/URL/client count/FPS/errors.  Reflect existing concurrency rules:
   named multi-instance features may run together, while barcode, calibration,
   ROI, and timelapse retain their current singleton behavior.  Stop controls
   call the existing feature object's `stop()`; decorations use their returned
   ID with `removeDecoration()`.

7. Add safe, on-demand discovery.  A **Rescan hardware** action will enumerate
   usable V4L2 cameras with a friendly name and stable `/dev/v4l/by-id` alias
   when available; RealSense model/serial devices when its dependency is
   installed; OpenMV serial-port candidates and USB descriptions; and
   AVWebcam-compatible camera/microphone choices.  Pi/ROS/Gazebo/WebSocket
   show configuration/availability guidance rather than fictitious local
   discovery.  Make clear that probing opens a device briefly and may produce
   driver noise.  Retain editable raw fields for URLs, device paths, topics,
   and all non-discoverable inputs.

8. Add a server-side path picker limited to configured roots.  Default model
   root is `~/Projects/olab_models`; default output root is a suitable user
   media directory, with `--model-root` and `--output-root` overrides.  Return
   only children under resolved approved roots, reject traversal/symlink escapes,
   and use it only for local model and output-path form fields.  Never expose a
   user's arbitrary filesystem to LAN clients.

9. Generate a copyable **Python equivalent** pane from successful submitted
   actions.  It starts with the selected constructor and `start(...)`, appends
   each active feature/decorative call, omits server/UI-only details, and adds
   cleanup.  Use the original public argument names and JSON-to-Python literal
   rendering so it is a useful prototype starting point; update it whenever
   the server-owned session changes.

10. Document launch, trusted-LAN warning, certificate SAN preparation,
    optional extras, path-picker roots, hardware discovery behavior, stream
    selection, generated-code limits, and all-backend/feature workflow in the
    `olab_camera` README or a focused playground document linked from it.

## Verification

1. Add focused unit tests for metadata completeness, field decoding/default
   omission, JSON validation, Python-only parameter suppression, dependency
   diagnostics, path-root containment, session replacement/cleanup, status
   serialization, feature lifecycle routing, generated-code output, and HTTP
   handler error responses.  Use fake camera/feature payloads and optional
   dependency stubs; do not import heavyweight detector packages in ordinary
   tests.

2. Add browser-level checks against a fake camera session for form rendering,
   persistent preview update behavior, status refresh, direct stream links,
   start/stop UI state, and unavailable-extra messages.  Verify a normal
   browser reload rehydrates the active server-owned session.

3. Perform real end-to-end hardware validation before commit, using the
   available USB webcam, RealSense, OpenMV/GENX, both Raspberry Pi camera
   variants as applicable, Gazebo/ROS/WebSocket source, and AVWebcam.  For
   each, rescan/select or configure it, start the camera, exercise an
   applicable feature, inspect its latest status, stop it, and confirm cleanup.
   Validate local model paths with actual local YOLO/RF-DETR checkpoints only.

4. On the lab LAN, validate MJPEG, WebSocket, and WebRTC from both the host
   browser and a phone.  Record protocol FPS/latency observations, certificate
   trust/SAN behavior, direct-link behavior, and reconnect/reload behavior;
   treat WebRTC as experimental until this evidence is captured.
