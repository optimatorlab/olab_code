"""Guards the public re-export surface promised by `olab_camera/__init__.py`
after the module was split into `cv_features.py`/`streaming.py`/`camera.py`/
per-backend files (see issue #12). A missing re-export or an import-order/
circular-import regression in the split should fail here rather than only
showing up as an AttributeError deep in a caller."""

import inspect

import olab_camera

PUBLIC_CLASS_NAMES = [
    "Camera",
    "CameraPi",
    "CameraPi2",
    "CameraGazebo",
    "CameraROS",
    "CameraUSB",
    "CameraWebSocket",
    "StreamingHandler",
    "StreamingServer",
    "WebSocketStreamingServer",
    "CameraVideoTrack",
    "WebRTCStreamingServer",
]


def test_all_public_classes_resolve_at_root():
    for name in PUBLIC_CLASS_NAMES:
        obj = getattr(olab_camera, name)
        assert inspect.isclass(obj), f"olab_camera.{name} did not resolve to a class"


def test_camera_backends_are_camera_subclasses():
    for name in ["CameraPi", "CameraPi2", "CameraGazebo", "CameraROS", "CameraUSB", "CameraWebSocket"]:
        backend = getattr(olab_camera, name)
        assert issubclass(backend, olab_camera.Camera), f"{name} is not a Camera subclass"


def test_root_level_constants_survive_the_split():
    # These were plain module-level names in the pre-split monolith (not
    # underscore-prefixed, so part of the public surface), not just
    # implementation-detail imports -- both must still resolve at the root.
    assert olab_camera.STREAM_MAX_WAIT_TIME_SEC == 2
    assert olab_camera.ROSPUB_MAX_WAIT_TIME_SEC == 2
