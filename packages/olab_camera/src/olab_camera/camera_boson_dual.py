"""RHP-BOS-DS-IF (dual thermal + visible interface board) camera backend."""

import cv2

from .camera_usb import CameraUSB


_RESOLUTION_PRESETS = {
	# name        (res_rows, res_cols, fps_target)
	'720p60':     (720,  1280, 60),
	'1080p60':    (1080, 1920, 60),
}


class CameraBosonDual(CameraUSB):
	"""RHP-BOS-DS-IF Dual Thermal Sensor Interface board, captured via an
	HDMI-to-USB (UVC) capture dongle plugged into the host.

	A thin subclass of CameraUSB -- video-only in this release (see issue
	#58 / .pairwork/camera-boson-dual.md for the full hardware investigation
	this class is based on). There is no independent
	capture behavior here: the board's HDMI output IS the video source, and
	once it reaches the host through a UVC capture dongle it behaves exactly
	like any other cv2.VideoCapture device.

	Physical wiring (none of this is visible from the code, so it's recorded
	here):
		- Mini-HDMI  -> capture dongle -> host (this is the video path this
		  class actually uses).
		- Micro-USB  -> a Windows-only "RHP Boson Camera Controller GUI".
		  This class does not use this port at all -- there is no documented
		  serial/USB protocol for it, no SDK. Mode/palette/zoom/FFC must be
		  configured through that GUI (or SBUS/PWM/button, see below) before
		  this class is ever instantiated.
		- 14-pin JST -> board power (5-26VDC) and/or SBUS (16ch) / PWM (5ch)
		  / push-button control, RC-style. Also unused by this class.
		- 6-pin JST  -> IMU (SDA/SCL) + the Boson core's own raw USB (D+/D-),
		  wired out toward an external gimbal, not toward the host. Not
		  exposed by this class.

	Video-only, GUI-only control (v1 scope):
		- The board composites whichever "HD Window Mode" is currently
		  configured (Full-Thermal, Full-Visible, Split-IR/Visible,
		  PiP-Visible-IR, PiP-Thermal-Vis) into a single HDMI stream --
		  entirely a board-side setting. This class cannot see or change it;
		  it only captures whatever composite is currently being output.
		- There are no control-plane methods here (no setWindowMode(),
		  setPalette(), zoom, or FFC) -- not even stubs. Live software control
		  is a deliberately deferred follow-up (it needs either a
		  reverse-engineered USB protocol for the Windows GUI, or an
		  SBUS/PWM-driven approach), tracked separately from this class.
		- The thermal image delivered here is the board's on-board AGC
		  8-bit display output -- it is not radiometric. No per-pixel
		  temperature data is available over this interface.

	`resolution` does not configure the board:
		`resolution` only tells OpenCV/the capture dongle what to *request*
		of the already-arriving HDMI signal -- it has no effect on the
		board's actual output mode, which must already match (via the
		Windows GUI/SBUS/PWM) before this class runs. A mismatch will not
		raise -- it typically shows up as dongle-side scaling or a
		failed/garbage capture. After calling start(), check
		self.res_rows/self.res_cols/self.fps_target against the requested
		preset before assuming the mode actually took; see also the FOURCC
		hazard note below, which can independently cause the same symptom.

		This class also inherits CameraUSB.changeResolutionFramerate(),
		whose docstring advertises arbitrary resolution changes -- that is
		not meaningful for this device. '720p60' and '1080p60' are the only
		two valid board-side HD output modes per the manual.

	Known hazard -- FOURCC set after frame size: CameraUSB.start() calls
	cap.set(CAP_PROP_FRAME_WIDTH/HEIGHT/FPS) and only then
	cap.set(CAP_PROP_FOURCC). On V4L2 backends, setting FOURCC after frame
	size commonly resets frame size back to that format's default -- and
	this is the first class in the package to ship a non-None fourcc by
	default, so it's the first to actually exercise that ordering. Until
	this is validated against real hardware, treat a result that doesn't
	match the requested preset as a symptom of this, not necessarily of the
	`resolution`-vs-board-mode mismatch above. If it bites: either construct
	with fourcc=None (uncompressed, may not sustain the requested
	framerate/resolution over USB2), or override start() to set FOURCC
	before frame size.

	paramDict is a deliberate, unvalidated escape hatch: passing
	res_rows/res_cols/fps_target keys in paramDict overrides the resolution
	preset entirely (matching CameraUSB's own paramDict-as-escape-hatch
	precedent). That is intentional, not a second supported configuration
	path -- 720p60/1080p60 remain the only two valid board-side modes.

	Attributes:
		resolution (str): The resolution preset requested at construction
			('720p60' or '1080p60'). Purely a record of what was asked for
			-- see the caveats above about it not configuring the board.

	Usage Example:
		>>> # Board's HD Window Mode already set via the Windows GUI/SBUS.
		>>> cam = CameraBosonDual(resolution='720p60', device='/dev/video2')
		>>> cam.start(startStream=True, port=8000)
		>>> # Confirm the requested mode actually took:
		>>> assert (cam.res_rows, cam.res_cols, cam.fps_target) == (720, 1280, 60)
		>>> cam.shutdown()
	"""

	def __init__(self, paramDict=None, resolution='720p60', device='/dev/video0',
			apiPref=cv2.CAP_V4L2, fourcc=('M','J','P','G'), logger=None, sslPath=None,
			pubCamStatusFunction=None, imgTopic=None, compImgTopic=None,
			initROSnode=False, showFPS=True, ipAllowlist=[], ipBlocklist=[]):
		"""Initialize the CameraBosonDual interface.

		Args:
			paramDict (dict, optional): Configuration dictionary, same keys
				as CameraUSB ('res_rows', 'res_cols', 'fps_target',
				'outputPort', 'device', 'fourcc'). Any key given here
				overrides the value derived from `resolution` for that key
				-- see the paramDict escape-hatch note in the class
				docstring. Defaults to None (nothing overridden).
			resolution (str, optional): One of '720p60' or '1080p60' -- the
				only two valid HD output modes for this board. Resolves to
				(res_rows, res_cols, fps_target); any other value raises
				ValueError before any capture attempt. Defaults to '720p60'.
			device (str, optional): Video source path for the HDMI capture
				dongle (e.g. '/dev/video2' on Linux). Defaults to
				'/dev/video0', same as CameraUSB -- there is no
				device-discovery in this release, so this should almost
				always be set explicitly.
			apiPref (int, optional): OpenCV VideoCapture API preference.
				Defaults to cv2.CAP_V4L2 (this class only makes sense behind
				a Linux V4L2 capture dongle); pass a different value (e.g.
				cv2.CAP_ANY) for other platforms.
			fourcc (tuple, optional): FOURCC codec as a 4-character tuple.
				Defaults to ('M','J','P','G') -- most HDMI capture dongles
				cannot sustain uncompressed video at 720p60/1080p60 over
				USB2. See the FOURCC hazard note in the class docstring.
			logger, sslPath, pubCamStatusFunction, imgTopic, compImgTopic,
			initROSnode, showFPS, ipAllowlist, ipBlocklist: Passed straight
				through to CameraUSB.__init__() -- see its docstring.
		"""
		try:
			res_rows, res_cols, fps_target = _RESOLUTION_PRESETS[resolution]
		except (KeyError, TypeError):
			raise ValueError(
				f"resolution must be one of {sorted(_RESOLUTION_PRESETS)!r}, got {resolution!r}") from None

		merged = {'res_rows': res_rows, 'res_cols': res_cols, 'fps_target': fps_target, 'outputPort': 8000}
		if paramDict:
			merged.update(paramDict)

		super().__init__(merged, device=device, apiPref=apiPref, fourcc=fourcc, logger=logger,
			sslPath=sslPath, pubCamStatusFunction=pubCamStatusFunction, imgTopic=imgTopic,
			compImgTopic=compImgTopic, initROSnode=initROSnode, showFPS=showFPS,
			ipAllowlist=ipAllowlist, ipBlocklist=ipBlocklist)

		self.resolution = resolution
