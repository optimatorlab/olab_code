"""H7 Plus MT9V034 global-shutter frame profile.

The profile uses OpenMV's documented primary-CSI grayscale API. Its supported
mode list comes from the Global Shutter module documentation; hardware
validation on the target H7 Plus remains required before making performance
claims about any mode or USB-delivered rate.
"""

from dataclasses import dataclass
import math


PROFILE_ID = 'mt9v034'
# (width, height), matching csi.framesize() and CameraOpenMV frame headers.
SUPPORTED_RESOLUTIONS = ((752, 480), (320, 240), (160, 120), (80, 60))
DEFAULT_RESOLUTION = (320, 240)
MAX_FRAMERATE = 'max'


@dataclass
class MT9V034Config:
	"""Initial configuration for the maintained MT9V034 frame profile."""

	resolution: tuple = DEFAULT_RESOLUTION
	framerate: object = 30
	exposure_us: object = None
	gain_db: object = None

	def __post_init__(self):
		try:
			self.resolution = tuple(self.resolution)
		except TypeError as error:
			raise ValueError(f'resolution must be a width, height pair, got {self.resolution!r}') from error
		if (len(self.resolution) != 2
				or any(isinstance(value, bool) or not isinstance(value, int) for value in self.resolution)):
			raise ValueError(f'resolution must be a pair of integer width, height values, got {self.resolution!r}')
		if self.resolution not in SUPPORTED_RESOLUTIONS:
			raise ValueError(
				f'resolution must be one of {SUPPORTED_RESOLUTIONS!r} for {PROFILE_ID}, '
				f'got {self.resolution!r}')
		if self.framerate != MAX_FRAMERATE:
			if isinstance(self.framerate, bool) or not isinstance(self.framerate, int) or self.framerate <= 0:
				raise ValueError(f'framerate must be a positive int or {MAX_FRAMERATE!r}, got {self.framerate!r}')
		if self.exposure_us is not None:
			if isinstance(self.exposure_us, bool) or not isinstance(self.exposure_us, int) or self.exposure_us <= 0:
				raise ValueError(f'exposure_us must be a positive int or None, got {self.exposure_us!r}')
		if self.gain_db is not None:
			if isinstance(self.gain_db, bool) or not isinstance(self.gain_db, (int, float)) or not math.isfinite(self.gain_db):
				raise ValueError(f'gain_db must be a finite number or None, got {self.gain_db!r}')


def render_script(config):
	"""Render a complete primary-CSI capture script for ``config``."""
	width, height = config.resolution
	rate_call = '' if config.framerate == MAX_FRAMERATE else f'csi0.framerate({config.framerate})\n'
	exposure_call = (
		'csi0.auto_exposure(True)'
		if config.exposure_us is None else
		f'csi0.auto_exposure(False, exposure_us={config.exposure_us})')
	gain_call = (
		'csi0.auto_gain(True)'
		if config.gain_db is None else
		f'csi0.auto_gain(False, gain_db={config.gain_db!r})')
	return '\n'.join((
		'import csi',
		'',
		'csi0 = csi.CSI()',
		'csi0.reset()',
		'csi0.pixformat(csi.GRAYSCALE)',
		f'csi0.framesize(({width}, {height}))',
		rate_call.rstrip(),
		exposure_call,
		gain_call,
		'',
		'while True:',
		'    csi0.snapshot()',
		'',
	))


class MT9V034Profile:
	"""Binds validated MT9V034 configuration to its rendered script."""

	profile_id = PROFILE_ID
	config_cls = MT9V034Config
	capabilities = frozenset(('frames', 'mt9v034'))

	def __init__(self, **config_kwargs):
		self.config = MT9V034Config(**config_kwargs)

	def render_script(self):
		return render_script(self.config)
