# MT9V034 on OpenMV Cam H7 Plus

## Evidence status

The Phase 1 code path is based on OpenMV's v5.0.0 Global Shutter module and
CSI documentation. Those documents identify the MT9V034 family, grayscale CSI
setup, QVGA/QQVGA/QQQVGA performance candidates, and automatic/manual
exposure/gain controls. They are documentation evidence, not evidence that a
particular H7 Plus, firmware, USB transport, or laptop workload achieves those
results.

Hardware observations are recorded below and remain separate from
documentation evidence. Before making performance or visual-usefulness claims,
record the actual sensor identity, requested setting, capture/browser rates,
and visible-tag observations. Do not update firmware without a separate
decision.

### 2026-10-02 diagnostic attempt

The manager host observed `/dev/ttyACM0` at sysfs path `3-3:1.0`, but it was
absent when this workspace ran the guarded discovery commands:

```text
ls -l /dev/ttyACM0 /dev/ttyACM* /dev/ttyUSB*
udevadm info --query=property --name=/dev/ttyACM0
```

The first `udevadm` invocation reported `Unknown device "/dev/ttyACM0"` after
the node disappeared; the later guarded command did not attempt
`OpenMVDevice.connect()` because the node was absent. This is an environmental
availability observation, not a camera or sensor failure. Board identity,
sensor CID, firmware version, transport frame format/dimensions, supported
modes, exposure/gain behavior, capture rate, browser rate, and AprilTag rate
are therefore unavailable.

Host-only evidence: `venv/bin/python` reports installed distribution metadata
`openmv 1.0.7`, while that package's module attribute reports `2.0.0`.
Distribution metadata is the recorded client version; the differing module
attribute is not treated as firmware or board evidence. That earlier isolated
discovery attempt did not perform a firmware action, profile upload, stream
enablement, or device write.

### 2026-10-02 physical evidence (outside sandbox)

The later stable `/dev/ttyACM0` probe connected successfully without changing
firmware. `OpenMVDevice.connect()` reported protocol `(1, 0, 2)`, bootloader
`(1, 0, 3)`, and firmware `(5, 0, 0)`. Its system identity reported USB
VID:PID `37c5:124a`, DRAM and JPEG present, a 1024-kB stream buffer, and no
NPU/GPU/ISP. This is physical board/protocol evidence; it does not independently
identify the sensor CID.

The approved temporary `mt9v034` QVGA profile (`320x240`, numeric `30` fps,
automatic exposure/gain) was uploaded through the normal host integration and
returned one actual BGR `uint8` frame of shape `(240, 320, 3)`. The first host
metadata record had sequence `1` and host receipt time only, as designed. The
host `DICT_APRILTAG_36h11` worker started and produced a result record; no
physical AprilTag target/detection is claimed. An HTTP request to the stream
endpoint was rejected with a TLS `HTTP_REQUEST` handshake error, confirming
the example's HTTPS endpoint rather than validating browser delivery.

The user subsequently completed IDE recovery and a firmware update; the device
now reports firmware `5.0.0`. The user also confirmed that the Python example
streams in a browser and detects AprilTags.

### Controlled host publish-loop measurements

With exclusive host access, the observed host publish-loop rates were QVGA
`37.4/s`, QQVGA `45.9/s`, and QQQVGA `54.2/s`. These are host publish-loop
measurements, not proven unique sensor-frame FPS: retained/latest-frame
semantics and host scheduling can publish repeated sensor frames. Browser
streaming and AprilTag processing lower the observed display rate. These
measurements do not establish sensor CID, a lossless-delivery guarantee, or a
sensor-rate performance guarantee.

The host frame metadata is receipt time and host sequence only. It is not an
exposure timestamp and cannot reveal losses before the host receives a frame.

## Phase 2 feasibility only

This is a documentation-based feasibility record, not authorization or an
implementation. OpenMV's documented `Image.find_apriltags()` and
`Image.find_blobs()` operate on captured images. Grayscale is the documented
8-bpp format and is described as the fastest format for common vision work,
including AprilTag detection. The returned AprilTag/Blob objects provide the
documented detection geometry such as tag family/id/rotation or blob centroid,
bounding box, area, and code. Firmware availability of those APIs must still
be checked on the connected H7 Plus; documentation does not establish that its
installed firmware exposes them or meets a target rate.

Two future transport choices are plausible. A compact result record can carry
a host-assigned sequence plus detection type, tag id/family, centroid/corners
or rectangle, rotation, and an optional device tick if a validated API exposes
one. This minimizes USB traffic, but needs an explicit result-to-preview/frame
association contract. A preview path can transmit the image (or a lower-rate
preview) beside results; it aids visual inspection but consumes capture/USB
budget. The existing host adapter has named-channel read support
(`OpenMVDevice.readChannelStatus()` and `readChannel()`), which is a host-side
starting point only, not evidence of a device-side channel or a Phase 2 design.

Before a Phase 2 proposal, benchmark on the actual H7 Plus: each candidate
resolution/rate, detection latency and sustained rate under representative
lighting, number/size of result records, preview bandwidth/quality, dropped
or delayed observations, and whether a device timestamp/frame counter can be
correlated with the host receipt sequence. No onboard processing or transport
protocol is added by this task.

Sources: OpenMV [image API](https://docs.openmv.io/v5.0.0/library/omv.image.html)
and [CSI API](https://docs.openmv.io/v5.0.0/library/omv.csi.html), accessed
2026-10-02. These are documentation evidence only.
