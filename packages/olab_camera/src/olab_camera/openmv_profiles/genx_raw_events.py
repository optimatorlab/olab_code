"""GENX320 maintained raw EVT2.0 event-stream profile."""

from dataclasses import dataclass

import olab_utils

PROFILE_ID = 'genx_raw_events'
FIXED_RESOLUTION = (320, 320)


@dataclass
class GenxRawEventsConfig:
	"""Configuration for raw polarity events and their host preview."""

	resolution: tuple = FIXED_RESOLUTION
	preview_rate_hz: int = 30
	preview_enabled: bool = True
	event_buffer_size: int = 8192
	callback_queue_size: int = 8

	def __post_init__(self):
		self.resolution = tuple(self.resolution)
		if self.resolution != FIXED_RESOLUTION:
			raise ValueError(f'resolution must be exactly {FIXED_RESOLUTION!r}')
		olab_utils.validateIntInRangeOrNone('preview_rate_hz', self.preview_rate_hz, 1, 120)
		if not isinstance(self.preview_enabled, bool):
			raise ValueError(f'preview_enabled must be a bool, got {self.preview_enabled!r}')
		if self.event_buffer_size < 1024 or self.event_buffer_size > 65536 or self.event_buffer_size & (self.event_buffer_size - 1):
			raise ValueError('event_buffer_size must be a power of two in 1024..65536')
		olab_utils.validatePositiveIntOrNone('callback_queue_size', self.callback_queue_size)


_TEMPLATE = '''\
import csi
import protocol

EVT_RES = {event_buffer_size}
csi0 = csi.CSI(cid=csi.GENX320)
csi0.reset()
csi0.ioctl(csi.IOCTL_GENX320_SET_MODE, csi.GENX320_MODE_EVENT, EVT_RES)
csi0.framebuffers(8)
events = csi0.ioctl(csi.IOCTL_GENX320_READ_EVENTS_RAW)
events_mv = memoryview(events.bytearray())
frame_available = True

class RawEventChannel:
    def size(self): return len(events_mv)
    def shape(self): return (len(events_mv), 1)
    def read(self, offset, size):
        global frame_available
        if frame_available:
            end = offset + size
            data = events_mv[offset:end]
            if end == len(events_mv): frame_available = False
            return data
        return bytes(size)
    def poll(self): return frame_available

protocol.register(name='raw_events', backend=RawEventChannel())
while True:
    if not frame_available:
        events = csi0.ioctl(csi.IOCTL_GENX320_READ_EVENTS_RAW)
        events_mv = memoryview(events.bytearray())
        frame_available = True
'''


def render_script(config):
	return _TEMPLATE.format(event_buffer_size=config.event_buffer_size)


class GenxRawEventsProfile:
	profile_id = PROFILE_ID
	config_cls = GenxRawEventsConfig
	capabilities = frozenset(('raw_events', 'preview_frames'))

	def __init__(self, **config_kwargs):
		self.config = GenxRawEventsConfig(**config_kwargs)

	def render_script(self):
		return render_script(self.config)
