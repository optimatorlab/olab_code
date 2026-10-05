#!/usr/bin/env python3
"""H7 Plus MT9V034 capture, browser stream, and AprilTag example.

Run only after confirming the attached H7 Plus/MT9V034 with OpenMV IDE or an
equivalent diagnostic. The documented mode list still requires target-hardware
validation; this example does not update firmware.
"""

import time

from olab_camera import CameraOpenMV


camera = CameraOpenMV(
	'/dev/ttyACM0',
	paramDict={'res_rows': 240, 'res_cols': 320, 'fps_target': 30, 'outputPort': 8000},
	profile='mt9v034',
	# None keeps OpenMV automatic exposure/gain. Set verified initial manual
	# values here, for example exposure_us=5000, gain_db=0.0.
	profile_kwargs={'exposure_us': None, 'gain_db': None},
)

try:
	# Numeric requests are supported. Use framerate='max' to omit the sensor
	# rate limiter and request the documented default maximum instead.
	camera.start(res_rows=240, res_cols=320, framerate='max', startStream=True, port=8000)
	# Later, use the same ordinary restart API for a verified mode/rate:
	# camera.changeResolutionFramerate(res_rows=120, res_cols=160, framerate=60)
	camera.addAruco('DICT_APRILTAG_36h11', fps_target=15)
	print('Browser stream: https://localhost:8000/stream.mjpg')
	while True:
		time.sleep(2)
		print(f'requested rate={camera.framerate!r}; achieved host capture FPS={camera.fps["capture"].actual}')
		# getFrameAndMeta() reports host receipt time plus a host sequence number,
		# not sensor exposure time and not a count of pre-host losses.
finally:
	camera.shutdown()
