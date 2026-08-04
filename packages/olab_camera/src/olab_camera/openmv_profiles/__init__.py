"""Maintained OpenMV MicroPython profiles for `olab_camera.CameraOpenMV`.

Each profile pairs a typed, validated host-side configuration with a
`render_script()` that produces a complete, self-contained MicroPython
source (helper + profile body) ready for `OpenMVDevice.runSource()`.
"""

from .genx_histogram_preview import GenxHistogramPreviewConfig, GenxHistogramPreviewProfile

PROFILES = {
	'genx_histogram_preview': GenxHistogramPreviewProfile,
}
