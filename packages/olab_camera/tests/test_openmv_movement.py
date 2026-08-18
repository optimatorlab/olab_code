import pytest

from olab_camera.openmv_movement import (
	MovementRecordDecodeError, decode_movement_record, render_movement_regions,
)
from olab_camera.openmv_profiles.genx_movement_regions import (
	GenxMovementRegionsProfile,
)
from olab_camera.openmv_profiles.genx_histogram_regions import (
	GenxHistogramRegionsProfile,
)


def test_decode_and_render_movement_record():
	record = decode_movement_record(
		b'{"seq":4,"t_ms":12,"regions":[{"x":2,"y":3,"w":4,"h":5,"cx":4,"cy":5,"pixels":20}]}')
	assert record['seq'] == 4
	canvas = render_movement_regions(record)
	assert canvas.shape == (320, 320, 3)
	assert canvas[3, 2].any()


def test_decode_rejects_invalid_region():
	with pytest.raises(MovementRecordDecodeError):
		decode_movement_record('{"seq":1,"t_ms":2,"regions":[{}]}')


def test_profile_does_not_replace_an_unread_channel_record():
	source = GenxMovementRegionsProfile().render_script()
	assert 'if not _ready and time.ticks_diff(now_ms, last_report_ms)' in source
	compile(source, 'genx_movement_regions.py', 'exec')


def test_regions_script_selects_auxiliary_stream_source():
	# olab_code#46: without stream=True the GENX320 aux CSI delivers region
	# telemetry but no host frames. genx_histogram_regions inherits the same
	# script, so the public mode is covered too.
	for profile in (GenxMovementRegionsProfile(), GenxHistogramRegionsProfile()):
		source = profile.render_script()
		assert 'csi.CSI(cid=csi.GENX320, stream=True)' in source
		assert 'csi.IOCTL_GENX320_SET_MODE, csi.GENX320_MODE_HISTO' in source
