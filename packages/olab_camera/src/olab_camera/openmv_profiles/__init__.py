"""Maintained OpenMV MicroPython profiles for `olab_camera.CameraOpenMV`.

Each profile pairs a typed, validated host-side configuration with a
`render_script()` that produces a complete, self-contained MicroPython
source (helper + profile body) ready for `OpenMVDevice.runSource()`.
"""

from .genx_histogram_preview import GenxHistogramPreviewConfig, GenxHistogramPreviewProfile
from .genx_raw_events import GenxRawEventsConfig, GenxRawEventsProfile
from .genx_histogram_regions import GenxHistogramRegionsProfile
from .mt9v034 import MT9V034Profile

PROFILES = {
	'genx_histogram_preview': GenxHistogramPreviewProfile,
	'genx_raw_events': GenxRawEventsProfile,
	'genx_histogram_regions': GenxHistogramRegionsProfile,
	'mt9v034': MT9V034Profile,
}
