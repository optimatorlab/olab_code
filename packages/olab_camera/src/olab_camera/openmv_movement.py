"""Host utilities for GENX320 compact movement-region telemetry."""

import json

import cv2
import numpy as np


class MovementRecordDecodeError(ValueError):
	"""Raised when a device movement record is malformed."""


def decode_movement_record(raw):
	"""Decode and validate one newline-delimited GENX movement record."""
	try:
		value = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
	except (UnicodeDecodeError, json.JSONDecodeError) as e:
		raise MovementRecordDecodeError(f'malformed movement record: {e}') from e
	if not isinstance(value, dict) or not isinstance(value.get('regions'), list):
		raise MovementRecordDecodeError('record must be an object with a regions list')
	for field in ('seq', 't_ms'):
		if not isinstance(value.get(field), int):
			raise MovementRecordDecodeError(f'record field {field!r} must be an int')
	for region in value['regions']:
		if not isinstance(region, dict) or any(not isinstance(region.get(k), int)
				for k in ('x', 'y', 'w', 'h', 'cx', 'cy', 'pixels')):
			raise MovementRecordDecodeError('each region needs integer geometry and pixels')
	return value


def render_movement_regions(record, shape=(320, 320)):
	"""Render region-only telemetry on a blank BGR coordinate canvas."""
	height, width = shape
	canvas = np.zeros((height, width, 3), dtype=np.uint8)
	for region in record['regions']:
		x, y, w, h = (region[k] for k in ('x', 'y', 'w', 'h'))
		cv2.rectangle(canvas, (x, y), (x + w - 1, y + h - 1), (0, 255, 0), 1)
		cv2.drawMarker(canvas, (region['cx'], region['cy']), (0, 200, 255), cv2.MARKER_CROSS, 7, 1)
	return canvas
