"""GENX320 on-device movement-region telemetry profile."""

from dataclasses import dataclass

import olab_utils

PROFILE_ID = 'genx_movement_regions'


@dataclass
class GenxMovementRegionsConfig:
	"""Measured initial settings for compact GENX320 movement regions."""

	resolution: tuple = (320, 320)
	histogram_rate_hz: int = 100
	report_rate_hz: int = 25
	pixels_threshold: int = 20
	area_threshold: int = 9
	max_regions: int = 3
	display_palette: str = 'grayscale'

	def __post_init__(self):
		self.resolution = tuple(self.resolution)
		if self.resolution != (320, 320):
			raise ValueError('resolution must be exactly (320, 320)')
		olab_utils.validateIntInRangeOrNone('histogram_rate_hz', self.histogram_rate_hz, 20, 350)
		olab_utils.validateIntInRangeOrNone('report_rate_hz', self.report_rate_hz, 1, 100)
		olab_utils.validatePositiveIntOrNone('pixels_threshold', self.pixels_threshold)
		olab_utils.validatePositiveIntOrNone('area_threshold', self.area_threshold)
		olab_utils.validatePositiveIntOrNone('max_regions', self.max_regions)
		if self.report_rate_hz > self.histogram_rate_hz:
			raise ValueError('report_rate_hz cannot exceed histogram_rate_hz')
		if self.display_palette not in ('grayscale', 'turbo'):
			raise ValueError("display_palette must be 'grayscale' or 'turbo'")


_TEMPLATE = '''\
import csi
import protocol
import time

REPORT_INTERVAL_MS = {report_interval_ms}
THRESHOLDS = [(0, 111), (145, 255)]
PIXELS_THRESHOLD = {pixels_threshold}
AREA_THRESHOLD = {area_threshold}
MAX_REGIONS = {max_regions}

csi0 = csi.CSI(cid=csi.GENX320)
csi0.reset()
csi0.ioctl(csi.IOCTL_GENX320_SET_MODE, csi.GENX320_MODE_HISTO)
csi0.pixformat(csi.GRAYSCALE)
csi0.framesize((320, 320))
csi0.framerate({histogram_rate_hz})

_record = b""
_ready = False
class RegionChannel:
    def size(self): return len(_record) if _ready else 0
    def read(self, offset, size):
        global _ready
        if _ready:
            end = offset + size
            data = _record[offset:end]
            if end == len(_record): _ready = False
            return data
        return bytes(size)
    def poll(self): return _ready
protocol.register(name="genx_regions", backend=RegionChannel())

clock = time.clock()
seq = 0
last_report_ms = time.ticks_ms()
while True:
    clock.tick()
    img = csi0.snapshot()
    blobs = img.find_blobs(THRESHOLDS, pixels_threshold=PIXELS_THRESHOLD,
                           area_threshold=AREA_THRESHOLD, merge=True)
    now_ms = time.ticks_ms()
    # Do not replace _record while the host is fetching it. The channel read
    # may be fragmented, so replacing it mid-read would splice two JSON
    # records together under high-motion load.
    if not _ready and time.ticks_diff(now_ms, last_report_ms) >= REPORT_INTERVAL_MS:
        candidates = [(b.pixels, b.x, b.y, b.w, b.h, b.cx, b.cy) for b in blobs]
        candidates.sort(reverse=True)
        parts = []
        for pixels, x, y, w, h, cx, cy in candidates[:MAX_REGIONS]:
            parts.append('{{"x":%d,"y":%d,"w":%d,"h":%d,"cx":%d,"cy":%d,"pixels":%d}}' %
                         (x, y, w, h, cx, cy, pixels))
        seq += 1
        _record = ('{{"seq":%d,"t_ms":%d,"fps":%.1f,"regions":[%s]}}\\n' %
                   (seq, now_ms, clock.fps(), ','.join(parts))).encode()
        _ready = True
        last_report_ms = now_ms
'''


def render_script(config):
	return _TEMPLATE.format(
		report_interval_ms=1000 // config.report_rate_hz,
		pixels_threshold=config.pixels_threshold,
		area_threshold=config.area_threshold,
		max_regions=config.max_regions,
		histogram_rate_hz=config.histogram_rate_hz,
	)


class GenxMovementRegionsProfile:
	profile_id = PROFILE_ID
	config_cls = GenxMovementRegionsConfig
	capabilities = frozenset(('movement_regions',))

	def __init__(self, **config_kwargs):
		self.config = GenxMovementRegionsConfig(**config_kwargs)

	def render_script(self):
		return render_script(self.config)
