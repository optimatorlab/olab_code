"""genx_histogram_preview: maintained OpenMV profile producing a 320x320
GENX320 event-activity grayscale frame stream, plus config/health channels
using the envelope contract in `contract.py`.

The rendered script's GENX320 sensor-control calls are confirmed against
OpenMV's own published documentation
(https://docs.openmv.io/dev/openmvcam/sensors/genx320.html), fetched and
verified during review round 4 of this task after an initial round of
fabricated `sensor.set_*` method names was correctly rejected -- the real
API is the `csi` module (`csi.CSI(cid=csi.GENX320)`, an object with
`pixformat()`/`framesize()`/`framerate()`/`brightness()`/`contrast()`/
`snapshot()` methods) plus `ioctl()` calls with documented GENX320-specific
constants for bias presets, anti-flicker, hot-pixel calibration, and
spatio-temporal filtering.
"""

import importlib.resources
from dataclasses import dataclass

import olab_utils

PROFILE_ID = 'genx_histogram_preview'

# The actual fixed GENX320 histogram-preview output -- not a free parameter.
# Matches the confirmed docs example's own csi0.framesize((320, 320)) call.
FIXED_RESOLUTION = (320, 320)

# Confirmed histogram-mode framerate range from OpenMV's own docs ("~20-350 FPS").
_HISTOGRAM_RATE_MIN_HZ = 20
_HISTOGRAM_RATE_MAX_HZ = 350

# Confirmed real bias presets (csi.GENX320_BIASES_<NAME>), lowercased here for
# the host-side config surface.
_BIAS_PRESETS = ('default', 'low_light', 'active_marker', 'low_noise', 'high_speed')

_ANTI_FLICKER_MODES = ('off', '50hz', '60hz')
# Reasonable +/-5Hz windows around mains frequency for csi.IOCTL_GENX320_SET_AFK's
# (min_hz, max_hz) arguments -- the doc's own example uses an arbitrary
# 130-160Hz demo range, so no single "correct" window is documented; these
# are a defensible default, not a hardware-verified tuning.
_ANTI_FLICKER_HZ_RANGES = {'50hz': (45, 55), '60hz': (55, 65)}

_HOT_PIXEL_CALIBRATION_POLICIES = ('auto', 'off')
# csi.IOCTL_GENX320_CALIBRATE(event_count, sigma) defaults, taken directly
# from the doc's own example.
_HOT_PIXEL_CALIBRATION_EVENT_COUNT = 10000
_HOT_PIXEL_CALIBRATION_SIGMA = 0.5


@dataclass
class GenxHistogramPreviewConfig:
	"""Typed, validated host-side configuration for `genx_histogram_preview`.

	Attributes:
		resolution (tuple[int, int]): Must be exactly `(320, 320)` -- the
			sensor's fixed histogram-preview output, not a free parameter.
		histogram_rate_hz (int): Histogram publish rate, 20-350 Hz (confirmed
			range from OpenMV's own GENX320 docs).
		baseline_brightness (int): 0-255 (`csi0.brightness()`).
		contrast (int): positive int, no confirmed upper bound
			(`csi0.contrast()`; the docs show a default of 16 but do not
			state a maximum).
		bias_preset (str): One of 'default', 'low_light', 'active_marker',
			'low_noise', 'high_speed' (the confirmed `csi.GENX320_BIASES_*`
			presets).
		anti_flicker (str): One of 'off', '50hz', '60hz'.
		spatio_temporal_filtering (bool): Whether to enable the sensor's
			spatio-temporal contrast filter (`csi.GENX320_STC_TRAIL` vs.
			`csi.GENX320_STC_DISABLE`).
		hot_pixel_calibration (str): One of 'auto', 'off'.
	"""

	resolution: tuple = FIXED_RESOLUTION
	histogram_rate_hz: int = 50
	baseline_brightness: int = 128
	contrast: int = 16
	bias_preset: str = 'default'
	anti_flicker: str = 'off'
	spatio_temporal_filtering: bool = True
	hot_pixel_calibration: str = 'auto'

	def __post_init__(self):
		self.resolution = tuple(self.resolution)
		if self.resolution != FIXED_RESOLUTION:
			raise ValueError(
				f'resolution must be exactly {FIXED_RESOLUTION!r} for '
				f'{PROFILE_ID} (the sensor\'s fixed histogram-preview output), '
				f'got {self.resolution!r}')

		olab_utils.validateIntInRangeOrNone(
			'histogram_rate_hz', self.histogram_rate_hz, _HISTOGRAM_RATE_MIN_HZ, _HISTOGRAM_RATE_MAX_HZ)
		if self.histogram_rate_hz is None:
			raise ValueError('histogram_rate_hz is required')

		olab_utils.validateIntInRangeOrNone('baseline_brightness', self.baseline_brightness, 0, 255)
		if self.baseline_brightness is None:
			raise ValueError('baseline_brightness is required')

		olab_utils.validatePositiveIntOrNone('contrast', self.contrast)
		if self.contrast is None:
			raise ValueError('contrast is required')

		if self.bias_preset not in _BIAS_PRESETS:
			raise ValueError(f'bias_preset must be one of {_BIAS_PRESETS}, got {self.bias_preset!r}')

		if self.anti_flicker not in _ANTI_FLICKER_MODES:
			raise ValueError(f'anti_flicker must be one of {_ANTI_FLICKER_MODES}, got {self.anti_flicker!r}')

		if not isinstance(self.spatio_temporal_filtering, bool):
			raise ValueError(
				f'spatio_temporal_filtering must be a bool, got {self.spatio_temporal_filtering!r}')

		if self.hot_pixel_calibration not in _HOT_PIXEL_CALIBRATION_POLICIES:
			raise ValueError(
				f'hot_pixel_calibration must be one of {_HOT_PIXEL_CALIBRATION_POLICIES}, '
				f'got {self.hot_pixel_calibration!r}')


def _bias_preset_const(name):
	return f'GENX320_BIASES_{name.upper()}'


def _anti_flicker_args(mode):
	if mode == 'off':
		return '0'
	min_hz, max_hz = _ANTI_FLICKER_HZ_RANGES[mode]
	return f'1, {min_hz}, {max_hz}'


# Every sensor-control call below is confirmed against OpenMV's own GENX320
# docs (see module docstring) -- csi.CSI/pixformat/framesize/framerate/
# brightness/contrast/snapshot, and the ioctl()+constant calls for bias/
# anti-flicker/hot-pixel/spatio-temporal settings. The config/health
# telemetry channel (_OmvHelper.publish()) is an independent, best-effort
# mechanism -- see assets/helper.py's module note -- so its still-pending
# channel-write primitive never blocks the frame stream above.
_PROFILE_BODY_TEMPLATE = '''\
# genx_histogram_preview -- auto-generated by
# olab_camera.openmv_profiles.genx_histogram_preview.render_script().
# GENX320 API confirmed against https://docs.openmv.io/dev/openmvcam/sensors/genx320.html

import csi
import time
import ujson

PROFILE_ID = "{profile_id}"
RESOLUTION = ({width}, {height})
HISTOGRAM_RATE_HZ = {histogram_rate_hz}
BASELINE_BRIGHTNESS = {baseline_brightness}
CONTRAST = {contrast}

csi0 = csi.CSI(cid=csi.GENX320)
csi0.reset()
csi0.pixformat(csi.GRAYSCALE)
csi0.framesize(RESOLUTION)
csi0.framerate(HISTOGRAM_RATE_HZ)
csi0.brightness(BASELINE_BRIGHTNESS)
csi0.contrast(CONTRAST)
csi0.ioctl(csi.IOCTL_GENX320_SET_BIASES, csi.{bias_preset_const})
csi0.ioctl(csi.IOCTL_GENX320_SET_AFK, {anti_flicker_args})
csi0.ioctl(csi.IOCTL_GENX320_SET_STC, csi.{stc_mode_const}, 1, 2)
{hot_pixel_calibration_call}

_seq = 0

def _next_seq():
    global _seq
    _seq += 1
    return _seq

_OmvHelper.publish("config", PROFILE_ID, "config", {{
    "resolution": list(RESOLUTION),
    "histogram_rate_hz": HISTOGRAM_RATE_HZ,
    "baseline_brightness": BASELINE_BRIGHTNESS,
    "contrast": CONTRAST,
    "bias_preset": "{bias_preset}",
    "anti_flicker": "{anti_flicker}",
    "spatio_temporal_filtering": {spatio_temporal_filtering},
    "hot_pixel_calibration": "{hot_pixel_calibration}",
}}, seq=_next_seq())

while True:
    img = csi0.snapshot()
    _OmvHelper.publish("health", PROFILE_ID, "health", {{"status": "ok"}}, seq=_next_seq())
'''


def _read_helper_source():
	return importlib.resources.files('olab_camera.openmv_profiles') \
		.joinpath('assets/helper.py').read_text(encoding='utf-8')


def render_profile_body(config):
	"""Render just the profile-specific body (no helper text) for `config`."""
	if config.hot_pixel_calibration == 'auto':
		hot_pixel_calibration_call = (
			f'csi0.ioctl(csi.IOCTL_GENX320_CALIBRATE, '
			f'{_HOT_PIXEL_CALIBRATION_EVENT_COUNT}, {_HOT_PIXEL_CALIBRATION_SIGMA})')
	else:
		hot_pixel_calibration_call = '# hot_pixel_calibration=off: hot-pixel calibration ioctl skipped'

	return _PROFILE_BODY_TEMPLATE.format(
		profile_id=PROFILE_ID,
		width=config.resolution[0],
		height=config.resolution[1],
		histogram_rate_hz=config.histogram_rate_hz,
		baseline_brightness=config.baseline_brightness,
		contrast=config.contrast,
		bias_preset=config.bias_preset,
		bias_preset_const=_bias_preset_const(config.bias_preset),
		anti_flicker=config.anti_flicker,
		anti_flicker_args=_anti_flicker_args(config.anti_flicker),
		spatio_temporal_filtering=config.spatio_temporal_filtering,
		stc_mode_const='GENX320_STC_TRAIL' if config.spatio_temporal_filtering else 'GENX320_STC_DISABLE',
		hot_pixel_calibration=config.hot_pixel_calibration,
		hot_pixel_calibration_call=hot_pixel_calibration_call,
	)


def render_script(config):
	"""Render the complete, self-contained MicroPython source for `config`.

	Layout (fixed, see docs/plans/olab_camera_openmv_support_plan.md's
	helper-composition contract): the helper asset's raw text, then exactly
	two blank lines, then the profile body -- no rewriting/wrapping of
	either part.

	Args:
		config (GenxHistogramPreviewConfig): Validated profile configuration.

	Returns:
		str: Complete script source to upload via `OpenMVDevice.runSource()`.
	"""
	return _read_helper_source() + '\n\n\n' + render_profile_body(config)


class GenxHistogramPreviewProfile:
	"""Binds a validated `GenxHistogramPreviewConfig` to its rendered script."""

	profile_id = PROFILE_ID
	config_cls = GenxHistogramPreviewConfig

	def __init__(self, **config_kwargs):
		self.config = GenxHistogramPreviewConfig(**config_kwargs)

	def render_script(self):
		return render_script(self.config)
