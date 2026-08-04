"""Tests for the openmv_profiles package: config validation, the concrete
render_script() composition (exact string, not a substring check), and the
envelope contract. Kept minimal/high-signal per the project's stated
test-thoroughness preference."""

import importlib.resources
import json
import sys
import types

import pytest

from olab_camera.openmv_profiles import PROFILES
from olab_camera.openmv_profiles.contract import (
    ENVELOPE_SCHEMA_VERSION, EnvelopeDecodeError, EnvelopeVersionError,
    decode_envelope, encode_envelope,
)
from olab_camera.openmv_profiles.genx_histogram_preview import (
    FIXED_RESOLUTION, GenxHistogramPreviewConfig, render_profile_body, render_script,
)


# ---- config validation --------------------------------------------------

def test_default_config_is_valid():
    config = GenxHistogramPreviewConfig()
    assert config.resolution == FIXED_RESOLUTION


@pytest.mark.parametrize('resolution', [(160, 160), (320, 240), (0, 0)])
def test_resolution_must_be_fixed_320x320(resolution):
    with pytest.raises(ValueError):
        GenxHistogramPreviewConfig(resolution=resolution)


@pytest.mark.parametrize('rate', [0, -1, 19, 351, None])
def test_histogram_rate_hz_out_of_range_rejected(rate):
    # Confirmed range from OpenMV's own GENX320 docs: ~20-350 FPS.
    with pytest.raises(ValueError):
        GenxHistogramPreviewConfig(histogram_rate_hz=rate)


def test_bias_preset_must_be_known_value():
    with pytest.raises(ValueError):
        GenxHistogramPreviewConfig(bias_preset='not_a_real_preset')


def test_anti_flicker_must_be_known_value():
    with pytest.raises(ValueError):
        GenxHistogramPreviewConfig(anti_flicker='not_a_real_mode')


def test_spatio_temporal_filtering_must_be_bool():
    with pytest.raises(ValueError):
        GenxHistogramPreviewConfig(spatio_temporal_filtering='yes')


def test_hot_pixel_calibration_must_be_known_policy():
    with pytest.raises(ValueError):
        GenxHistogramPreviewConfig(hot_pixel_calibration='not_a_real_policy')


# ---- render_script() exact composition ----------------------------------

def _helper_asset_text():
    return importlib.resources.files('olab_camera.openmv_profiles') \
        .joinpath('assets/helper.py').read_text(encoding='utf-8')


def test_render_script_is_helper_plus_profile_body_exactly():
    config = GenxHistogramPreviewConfig(histogram_rate_hz=45, contrast=10)

    script = render_script(config)
    expected = _helper_asset_text() + '\n\n\n' + render_profile_body(config)

    assert script == expected


def test_render_script_contains_no_bare_import_helper():
    script = render_script(GenxHistogramPreviewConfig())
    assert 'import helper' not in script
    assert 'class _OmvHelper:' in script
    assert '_OmvHelper.publish(' in script


def test_render_profile_body_reflects_config_values():
    config = GenxHistogramPreviewConfig(histogram_rate_hz=120, baseline_brightness=200, contrast=77)
    body = render_profile_body(config)

    assert 'HISTOGRAM_RATE_HZ = 120' in body
    assert 'BASELINE_BRIGHTNESS = 200' in body
    assert 'CONTRAST = 77' in body


class _StopFrameLoop(Exception):
    """Sentinel used to deterministically break the rendered script's
    `while True: csi0.snapshot(); ...` loop from a stubbed snapshot()."""


def _make_fake_csi_module():
    """A stub of the real `csi` module (confirmed against
    https://docs.openmv.io/dev/openmvcam/sensors/genx320.html), just
    faithful enough to prove the rendered script's calls resolve to
    real, documented names -- not that the numeric ioctl behavior is
    hardware-verified, which remains deferred to bring-up."""
    fake_csi = types.ModuleType('csi')
    fake_csi.GENX320 = 'GENX320'
    fake_csi.GRAYSCALE = 'GRAYSCALE'
    for const in (
        'GENX320_BIASES_DEFAULT', 'GENX320_BIASES_LOW_LIGHT', 'GENX320_BIASES_ACTIVE_MARKER',
        'GENX320_BIASES_LOW_NOISE', 'GENX320_BIASES_HIGH_SPEED',
        'GENX320_STC_DISABLE', 'GENX320_STC_TRAIL',
        'IOCTL_GENX320_SET_BIASES', 'IOCTL_GENX320_SET_AFK', 'IOCTL_GENX320_SET_STC',
        'IOCTL_GENX320_CALIBRATE',
    ):
        setattr(fake_csi, const, const)

    class _FakeCSIInstance:
        def __init__(self, cid):
            self.cid = cid

        def reset(self):
            pass

        def pixformat(self, fmt):
            pass

        def framesize(self, size):
            pass

        def framerate(self, hz):
            pass

        def brightness(self, val):
            pass

        def contrast(self, val):
            pass

        def ioctl(self, *args):
            pass

        def snapshot(self):
            raise _StopFrameLoop()

    fake_csi.CSI = _FakeCSIInstance
    return fake_csi


def test_render_script_produces_frames_with_telemetry_channel_best_effort(capsys):
    """A string-equality test alone can't catch a script that references an
    undefined name (NameError) once actually run, nor prove the frame
    stream itself doesn't depend on the unconfirmed channel-write
    primitive. This execs the rendered script under CPython against a stub
    for `csi`/`time`/`ujson` matching the real, documented GENX320 API
    shape and proves:

    1. Every symbol the script references resolves (no NameError/
       AttributeError) -- the `csi.CSI`/`pixformat`/`framesize`/
       `framerate`/`brightness`/`contrast`/`ioctl`/`snapshot` calls and
       every `csi.GENX320_*`/`csi.IOCTL_GENX320_*` constant used are the
       real, confirmed names from OpenMV's own GENX320 documentation, not
       fabricated ones.
    2. The initial config-telemetry publish (before the frame loop) hits
       the intentionally-unimplemented channel-write primitive but does
       NOT stop the script -- `_OmvHelper.publish()` catches it and prints
       a non-fatal notice (confirmed via `capsys`).
    3. Execution reaches `csi0.snapshot()` inside the frame loop -- the
       stubbed `snapshot()` raises a sentinel to deterministically break
       the otherwise-infinite loop, proving the frame-producing path is
       reached and not gated behind the unconfirmed telemetry channel.
    """
    script = render_script(GenxHistogramPreviewConfig())

    fake_csi = _make_fake_csi_module()

    fake_time = types.ModuleType('time')
    fake_time.ticks_ms = lambda: 0

    fake_ujson = types.ModuleType('ujson')
    fake_ujson.dumps = json.dumps

    fakes = {'csi': fake_csi, 'time': fake_time, 'ujson': fake_ujson}
    saved = {name: sys.modules.get(name) for name in fakes}
    sys.modules.update(fakes)
    try:
        with pytest.raises(_StopFrameLoop):
            exec(compile(script, '<genx_histogram_preview>', 'exec'), {})
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    # The config-telemetry publish's caught failure was printed (non-fatal,
    # visible to the host via OpenMVDevice.readStdout()), not swallowed silently.
    assert 'channel-write primitive' in capsys.readouterr().out


def test_omv_helper_publish_disables_itself_after_first_failure(capsys):
    """The frame loop calls publish() once per frame (20-350 FPS) -- review
    round 5 caught that catching-and-printing on every call would flood
    stdout/USB and allocate an exception every single frame. This execs
    just the helper asset (not a full profile) and calls publish() many
    times, asserting exactly one warning is printed and the underlying
    channel-write primitive is attempted exactly once, not once per call.
    """
    helper_source = importlib.resources.files('olab_camera.openmv_profiles') \
        .joinpath('assets/helper.py').read_text(encoding='utf-8')

    fake_time = types.ModuleType('time')
    fake_time.ticks_ms = lambda: 0
    fake_ujson = types.ModuleType('ujson')
    fake_ujson.dumps = json.dumps

    namespace = {'time': fake_time, 'ujson': fake_ujson}
    exec(compile(helper_source, '<helper>', 'exec'), namespace)
    omv_helper = namespace['_OmvHelper']

    write_calls = []
    original_channel_write = omv_helper._channel_write

    def _counting_channel_write(channel, data):
        write_calls.append((channel, data))
        return original_channel_write(channel, data)
    omv_helper._channel_write = staticmethod(_counting_channel_write)

    for i in range(20):
        omv_helper.publish('health', 'p', 'health', {'status': 'ok'}, seq=i)

    assert len(write_calls) == 1  # never retried after the first failure
    assert capsys.readouterr().out.count('channel-write primitive') == 1
    assert omv_helper._telemetry_disabled is True


@pytest.mark.parametrize('preset', ['default', 'low_light', 'active_marker', 'low_noise', 'high_speed'])
def test_render_profile_body_uses_confirmed_bias_preset_constant(preset):
    body = render_profile_body(GenxHistogramPreviewConfig(bias_preset=preset))
    assert f'csi.GENX320_BIASES_{preset.upper()}' in body


@pytest.mark.parametrize('mode,expected', [
    ('off', 'IOCTL_GENX320_SET_AFK, 0'),
    ('50hz', 'IOCTL_GENX320_SET_AFK, 1, 45, 55'),
    ('60hz', 'IOCTL_GENX320_SET_AFK, 1, 55, 65'),
])
def test_render_profile_body_anti_flicker_args(mode, expected):
    body = render_profile_body(GenxHistogramPreviewConfig(anti_flicker=mode))
    assert expected in body


def test_render_profile_body_hot_pixel_calibration_off_skips_ioctl():
    body = render_profile_body(GenxHistogramPreviewConfig(hot_pixel_calibration='off'))
    assert 'csi0.ioctl(csi.IOCTL_GENX320_CALIBRATE' not in body

    body_on = render_profile_body(GenxHistogramPreviewConfig(hot_pixel_calibration='auto'))
    assert 'csi0.ioctl(csi.IOCTL_GENX320_CALIBRATE, 10000, 0.5)' in body_on


def test_profile_registry_binds_config_and_render_script():
    profile = PROFILES['genx_histogram_preview'](histogram_rate_hz=20)
    assert profile.config.histogram_rate_hz == 20
    assert profile.render_script() == render_script(profile.config)


# ---- envelope contract ---------------------------------------------------

def test_encode_decode_envelope_roundtrip():
    raw = encode_envelope('genx_histogram_preview', 'health', {'status': 'ok'}, device_seq=5, device_time_ms=123)
    envelope = decode_envelope(raw)

    assert envelope['schema_version'] == ENVELOPE_SCHEMA_VERSION
    assert envelope['profile_id'] == 'genx_histogram_preview'
    assert envelope['kind'] == 'health'
    assert envelope['payload'] == {'status': 'ok'}
    assert envelope['device_seq'] == 5
    assert envelope['device_time_ms'] == 123


def test_encode_envelope_rejects_invalid_kind():
    with pytest.raises(ValueError):
        encode_envelope('p', 'not_a_kind', {})


def test_decode_envelope_rejects_schema_version_mismatch():
    raw = encode_envelope('p', 'config', {})
    import json
    tampered = json.loads(raw)
    tampered['schema_version'] = ENVELOPE_SCHEMA_VERSION + 1
    with pytest.raises(EnvelopeVersionError):
        decode_envelope(json.dumps(tampered).encode('utf-8'))


def test_decode_envelope_rejects_malformed_json():
    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(b'not json at all')


def test_decode_envelope_rejects_missing_field():
    import json
    incomplete = {'schema_version': ENVELOPE_SCHEMA_VERSION, 'kind': 'config'}
    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(json.dumps(incomplete).encode('utf-8'))
