"""Tests for _util.py's spectrum_db() -- the live mic spectrogram helper
(see .pairwork/rig-mic-spectrogram/plan.md in the ofm repo for the full
design history).

Loaded by direct file path rather than `from olab_audio._util import ...`,
bypassing `olab_audio/__init__.py` entirely -- that package `__init__`
eagerly imports `.device` -> `pyaudio`, which needs `libportaudio.so.2` to
even import. That shared library isn't installed in every dev sandbox this
suite runs in, which would otherwise make the ENTIRE test_util.py file
uncollectable even though `_util.py` itself (numpy only, like the rest of
this module) has no such dependency. This loader mechanism was verified to
work with no portaudio present before being adopted here.
"""

import importlib.util
import os

import numpy as np
import pytest

_UTIL_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'src', 'olab_audio', '_util.py'
)
_spec = importlib.util.spec_from_file_location('_util_standalone', _UTIL_PATH)
_util = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_util)

spectrum_db = _util.spectrum_db
_DB_FLOOR_DBFS = _util._DB_FLOOR_DBFS
_EXCEPTION_SENTINEL_DBFS = _util._EXCEPTION_SENTINEL_DBFS
_FALLBACK_N_BINS = _util._FALLBACK_N_BINS
_DEFAULT_N_BINS = _util._DEFAULT_N_BINS


def _sine(freq, samplerate=44100, n=512, amplitude=1.0):
    t = np.arange(n) / samplerate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# Shape / basic binning
# ---------------------------------------------------------------------------

def test_returns_n_bins_floats():
    result = spectrum_db(_sine(2000), 44100, n_bins=32)
    assert len(result) == 32
    assert all(isinstance(v, float) for v in result)


def test_default_n_bins_is_64():
    result = spectrum_db(_sine(2000), 44100)
    assert len(result) == _DEFAULT_N_BINS == 64


# ---------------------------------------------------------------------------
# I1 -- dBFS-like normalization (matches convert_to_db's -60..0 convention)
# ---------------------------------------------------------------------------

def test_full_scale_tone_reads_near_zero_db():
    # A bin-centered full-scale sine should read close to 0 dB, not the
    # +45.7 dB an unnormalized FFT magnitude would give (round-1 review
    # finding) -- 2.0/data.size normalization is what fixes this.
    result = spectrum_db(_sine(2000, amplitude=1.0), 44100, n_bins=64)
    assert max(result) == pytest.approx(0.0, abs=3.0)


def test_quiet_tone_reads_lower_than_loud_tone():
    loud = spectrum_db(_sine(2000, amplitude=1.0), 44100, n_bins=64)
    quiet = spectrum_db(_sine(2000, amplitude=0.001), 44100, n_bins=64)
    assert max(loud) > max(quiet)


# ---------------------------------------------------------------------------
# I9 -- the display floor is a genuine floor, not just an amplitude clamp
# ---------------------------------------------------------------------------

def test_no_bin_ever_below_the_display_floor():
    for freq in (150, 500, 2000, 6000):
        for amplitude in (1.0, 0.1, 0.001):
            result = spectrum_db(_sine(freq, amplitude=amplitude), 44100, n_bins=64)
            assert all(v >= _DB_FLOOR_DBFS for v in result), (freq, amplitude, min(result))


def test_silence_is_exactly_the_floor():
    silent = np.zeros(512, dtype=np.float32)
    result = spectrum_db(silent, 44100, n_bins=16)
    assert result == [_DB_FLOOR_DBFS] * 16


# ---------------------------------------------------------------------------
# I2 -- low-frequency bins are interpolated, not left at a dead sentinel
# ---------------------------------------------------------------------------

def test_low_frequency_bins_are_filled_not_permanently_dead():
    # At CHUNK=512/44100Hz with 64 log bins over 80-8000Hz, a naive
    # per-bin mask (no interpolation) leaves ~24/64 bins with no FFT
    # energy at all -- round-1 review measured this. A signal with real
    # broadband energy (white noise, not a pure tone -- a pure tone only
    # populates a couple of bins to begin with) should come back with no
    # bin sitting at the bare silence floor once interpolation fills the
    # gaps between populated bins.
    rng = np.random.default_rng(0)
    noise = rng.uniform(-0.5, 0.5, size=512).astype(np.float32)
    result = spectrum_db(noise, 44100, n_bins=64, fmin=80.0, fmax=8000.0)
    assert all(v > _DB_FLOOR_DBFS for v in result)


# ---------------------------------------------------------------------------
# Degrade-gracefully paths (B3)
# ---------------------------------------------------------------------------

def test_empty_buffer_returns_floor():
    result = spectrum_db(np.array([], dtype=np.float32), 44100, n_bins=8)
    assert result == [_DB_FLOOR_DBFS] * 8


def test_zero_samplerate_returns_floor():
    result = spectrum_db(_sine(2000), 0, n_bins=8)
    assert result == [_DB_FLOOR_DBFS] * 8


def test_negative_samplerate_returns_floor():
    result = spectrum_db(_sine(2000), -44100, n_bins=8)
    assert result == [_DB_FLOOR_DBFS] * 8


def test_inverted_fmin_fmax_returns_floor():
    result = spectrum_db(_sine(2000), 44100, n_bins=8, fmin=8000.0, fmax=80.0)
    assert result == [_DB_FLOOR_DBFS] * 8


def test_zero_width_range_returns_floor():
    result = spectrum_db(_sine(2000), 44100, n_bins=8, fmin=1000.0, fmax=1000.0)
    assert result == [_DB_FLOOR_DBFS] * 8


def test_non_positive_fmin_returns_floor():
    result = spectrum_db(_sine(2000), 44100, n_bins=8, fmin=0.0, fmax=8000.0)
    assert result == [_DB_FLOOR_DBFS] * 8
    result = spectrum_db(_sine(2000), 44100, n_bins=8, fmin=-10.0, fmax=8000.0)
    assert result == [_DB_FLOOR_DBFS] * 8


def test_float_n_bins_coerces_cleanly():
    # A JSON payload naturally produces a float for what's conceptually an
    # int (round-1 review's n_bins=64.0 finding) -- must not raise, and
    # should behave identically to the int form.
    result = spectrum_db(_sine(2000), 44100, n_bins=64.0)
    assert len(result) == 64


def test_numeric_string_n_bins_coerces_cleanly():
    result = spectrum_db(_sine(2000), 44100, n_bins='32')
    assert len(result) == 32


def test_non_numeric_n_bins_returns_exception_sentinel_fallback_length():
    result = spectrum_db(_sine(2000), 44100, n_bins='not-a-number')
    assert result == [_EXCEPTION_SENTINEL_DBFS] * _FALLBACK_N_BINS


def test_zero_n_bins_returns_exception_sentinel_fallback_length():
    result = spectrum_db(_sine(2000), 44100, n_bins=0)
    assert result == [_EXCEPTION_SENTINEL_DBFS] * _FALLBACK_N_BINS


def test_negative_n_bins_returns_exception_sentinel_fallback_length():
    result = spectrum_db(_sine(2000), 44100, n_bins=-5)
    assert result == [_EXCEPTION_SENTINEL_DBFS] * _FALLBACK_N_BINS


def test_none_n_bins_returns_exception_sentinel_fallback_length():
    result = spectrum_db(_sine(2000), 44100, n_bins=None)
    assert result == [_EXCEPTION_SENTINEL_DBFS] * _FALLBACK_N_BINS


def test_none_data_returns_exception_sentinel():
    result = spectrum_db(None, 44100, n_bins=8)
    assert result == [_EXCEPTION_SENTINEL_DBFS] * 8
