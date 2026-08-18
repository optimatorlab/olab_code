"""Histogram-frame GENX320 profile with compact on-board movement telemetry."""

from .genx_movement_regions import (
	GenxMovementRegionsConfig, GenxMovementRegionsProfile,
)

PROFILE_ID = 'genx_histogram_regions'


class GenxHistogramRegionsProfile(GenxMovementRegionsProfile):
	"""The proven movement script, consumed alongside normal frame streaming."""

	profile_id = PROFILE_ID
	config_cls = GenxMovementRegionsConfig
	capabilities = frozenset(('frames', 'histogram', 'movement_regions'))
