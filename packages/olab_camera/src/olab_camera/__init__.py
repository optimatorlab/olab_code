"""
olab_camera - Unified Camera Interface Module

This module provides a comprehensive camera interface for USB cameras, Raspberry Pi cameras,
and ROS camera topics with extensive computer vision capabilities.

Main Features:
    - Multiple camera backends (USB, Raspberry Pi Camera Module, Gazebo Transport topics, ROS topics)
    - Video streaming over HTTPS — three protocol options:
        * MJPEG  (default, no extra deps)
        * WebSocket + JPEG  (pip install olab-camera[websocket])
        * WebRTC            (pip install olab-camera[webrtc])
    - ROS topic publishing (compressed and raw images)
    - ArUco marker detection and tracking
    - QR code detection (skew-robust, selectable decoder)
    - Barcode/QR code detection (generic pyzbar-based scanning)
    - Face detection
    - Camera calibration tools
    - Timelapse capture
    - Region of Interest (ROI) tracking
    - YOLO object detection (via Ultralytics)
    - Configurable frame rates and resolutions

Classes:
    Camera: Base camera class with common functionality
    CameraPi: Raspberry Pi camera implementation
    CameraGazebo: Gazebo Transport topic subscriber/publisher
    CameraROS: ROS camera topic subscriber/publisher
    CameraUSB: USB camera and RTSP stream implementation
    CameraWebSocket: Virtual camera — accepts pushed JPEG frames via WSS (sim/Cesium use)

Dependencies:
    - numpy
    - opencv-contrib-python (for ArUco support)
    - websockets>=12.0          (optional, for WebSocket streaming)
    - aiortc>=1.9.0, aiohttp>=3.9.0  (optional, for WebRTC streaming)
    - rospy, cv_bridge, sensor_msgs  (optional, for ROS support)
    - olab_utils (custom utility module)

Basic Usage:
    # USB Camera
    camera = CameraUSB(paramDict={'res_rows': 480, 'res_cols': 640, 'fps_target': 30})
    camera.start()

    # Start streaming (MJPEG default — backward compatible)
    camera.startStream(port=8000)
    # Visit https://localhost:8000/stream.mjpg

    # WebSocket streaming (lower latency)
    camera.startStream(port=8001, protocol='websocket')

    # WebRTC streaming (lowest latency, built-in browser viewer)
    camera.startStream(port=8002, protocol='webrtc')
    # Visit https://localhost:8002/webrtc

    # Add ArUco marker detection
    camera.addAruco('DICT_APRILTAG_36h11', fps_target=20)

    # Add QR code detection, robust to skewed/oblique viewing angles
    # (cv2.QRCodeDetector by default; decoder='pyzbar' is also available):
    camera.addQR('default')

    # Like ArUco/barcode, addQR() only reports raw detections (payload data +
    # corners) each cycle -- it does not compute distance or pose itself. To
    # get distance/pose, call olab_utils.findTagPose() (and, for a
    # world-frame position, olab_utils.findTagPoseGlobal()) from your own
    # postFunction with your own known tag size, exactly the way it's done
    # for ArUco -- see docs/usage_guide.md for a full worked example,
    # including precision-landing-style pose composition via setPose()/
    # setExtrinsics()/findCameraPoseGlobal().

    # Start ROS publishing
    camera.startROStopic()
    # Publishes to /camera/image/compressed and /camera/image/raw

    # Timelapse capture
    camera.addTimelapse(outputDir="/path/to/output", secBetwPhotos=2)

    # Cleanup
    camera.shutdown()

For more examples and detailed usage, see the module-level comments below.

Author: Optimator Lab
"""

from importlib.metadata import PackageNotFoundError, version

try:
	__version__ = version("olab-camera")
except PackageNotFoundError:
	__version__ = "0.0.0"

from .cv_features import _Aruco, _Calibrate, _Barcode, _QRCode, _FaceDetect, _Timelapse, _ROI, _Ultralytics
from .streaming import (
	StreamingHandler, StreamingServer, WebSocketStreamingServer, CameraVideoTrack,
	WebRTCStreamingServer, _make_fps_dict, STREAM_MAX_WAIT_TIME_SEC,
)
from .camera import Camera, ROSPUB_MAX_WAIT_TIME_SEC
from .camera_pi import CameraPi, CameraPi2
from .camera_gazebo import CameraGazebo
from .camera_ros import CameraROS
from .camera_usb import CameraUSB
from .camera_websocket import CameraWebSocket, _WebSocketReceiveServer


'''
try:
	system = platform.system()
	if (system == 'Linux'):
		# For Linux/Mac
		HOME_DIRECTORY = os.environ['HOME']
	else:
		# For Windows
		HOME_DIRECTORY = os.environ['USERPROFILE']
except Exception as e:
	print(f'Error - Could not set HOME_DIRECTORY: {e}')
'''


'''
import olab_camera
camera = olab_camera.CameraUSB(paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort':8000})
camera.startStream(port=8000)
# Visit https://localhost:8000/stream.mjpg


camera = olab_camera.CameraPi(paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort':8000}, initROSnode=False)
camera = olab_camera.CameraUSB(paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort':8000}, initROSnode=False)
camera = olab_camera.CameraUSB(paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort':8000}, device='/dev/video2', fourcc='MJPG', initROSnode=False)

camera = olab_camera.CameraUSB(paramDict={'res_rows':480, 'res_cols':640, 'fps_target':30, 'outputPort':8000}, device='rtsp://192.168.0.114:8900/live', fourcc='MJPG', initROSnode=False)


camera.start()

camera.startStream(port=8000)
# Visit https://localhost:8000/stream.mjpg

camera.addAruco('DICT_APRILTAG_36h11', fps_target=20)

camera.startROStopic()
# Starts publishing `/camera/image/compressed` and `/camera/image/raw`

# camera.addBarcode()
# camera.addCalibrate()

outputDir = f"{os.environ['HOME']}/Downloads/Timelapse/test2"
camera.addTimelapse(outputDir=outputDir, secBetwPhotos=2, timeLimitSec=None, delayStartSec=0, res_rows=None, res_cols=None, postPostFunction=None)
# ... wait some time ...
camera.timelapse['default'].stop()
olab_utils.pics2video(sourcePath=outputDir, filename="myVideo.mp4", fps=2)

camera.shutdown()

exit()

'''
