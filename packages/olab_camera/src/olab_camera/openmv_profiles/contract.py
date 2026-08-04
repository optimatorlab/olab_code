"""Versioned payload envelope shared by every OpenMV profile's helper/channel
contract -- the format published by `assets/helper.py` on the device and
decoded here on the host.
"""

import json

ENVELOPE_SCHEMA_VERSION = 1

VALID_KINDS = ('config', 'health', 'result', 'error')


class EnvelopeDecodeError(ValueError):
	"""Raised when raw channel bytes cannot be parsed as a valid envelope."""


class EnvelopeVersionError(EnvelopeDecodeError):
	"""Raised when a decoded envelope's schema_version doesn't match what the host expects."""


def encode_envelope(profile_id, kind, payload, device_seq=0, device_time_ms=0):
	"""Build the wire-format bytes for one envelope.

	Args:
		profile_id (str): Identifier of the profile that produced this payload.
		kind (str): One of `VALID_KINDS`.
		payload: JSON-serializable envelope payload.
		device_seq (int): Device-side sequence number for this message.
		device_time_ms (int): Device-side monotonic time in milliseconds.

	Returns:
		bytes: UTF-8-encoded JSON envelope.

	Raises:
		ValueError: `kind` is not one of `VALID_KINDS`.
	"""
	if kind not in VALID_KINDS:
		raise ValueError(f'kind must be one of {VALID_KINDS}, got {kind!r}')

	envelope = {
		'schema_version': ENVELOPE_SCHEMA_VERSION,
		'profile_id': profile_id,
		'device_seq': device_seq,
		'device_time_ms': device_time_ms,
		'kind': kind,
		'payload': payload,
	}
	return json.dumps(envelope).encode('utf-8')


def decode_envelope(raw):
	"""Decode and validate a channel payload produced by `encode_envelope()`
	(or the on-device `_OmvHelper`).

	Args:
		raw (bytes or str): Raw channel payload.

	Returns:
		dict: The decoded envelope, containing at least `schema_version`,
			`profile_id`, `device_seq`, `device_time_ms`, `kind`, `payload`.

	Raises:
		EnvelopeDecodeError: `raw` is not valid JSON, does not decode to an
			object, is missing a required field, or has an invalid `kind`.
		EnvelopeVersionError: the decoded `schema_version` does not match
			`ENVELOPE_SCHEMA_VERSION` (subclass of `EnvelopeDecodeError`).
	"""
	try:
		text = raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw
		envelope = json.loads(text)
	except (json.JSONDecodeError, UnicodeDecodeError) as e:
		raise EnvelopeDecodeError(f'malformed envelope payload: {e}') from e

	if not isinstance(envelope, dict):
		raise EnvelopeDecodeError(f'envelope must decode to an object, got {type(envelope).__name__}')

	for field in ('schema_version', 'profile_id', 'device_seq', 'device_time_ms', 'kind', 'payload'):
		if field not in envelope:
			raise EnvelopeDecodeError(f'envelope missing required field: {field!r}')

	if envelope['schema_version'] != ENVELOPE_SCHEMA_VERSION:
		raise EnvelopeVersionError(
			f"envelope schema_version {envelope['schema_version']!r} does not match "
			f"this host's expected {ENVELOPE_SCHEMA_VERSION!r}")

	if envelope['kind'] not in VALID_KINDS:
		raise EnvelopeDecodeError(f"envelope kind must be one of {VALID_KINDS}, got {envelope['kind']!r}")

	return envelope
