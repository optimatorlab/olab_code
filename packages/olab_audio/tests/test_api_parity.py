"""API-parity coverage: the original ub_audio module exposed everything
(Mic, Wave, Spectrum, resample, createTone, etc.) as flat module attributes.
olab_audio splits analysis-only symbols into olab_audio.analysis (so core
doesn't pay for librosa/soundfile/matplotlib), but still exposes them at
package root via lazy __getattr__ (PEP 562) -- this file verifies that
re-export mechanism itself, independent of whether the analysis extra
happens to be installed in a given test run."""

import pytest

import olab_audio


def test_resample_is_the_function_not_the_private_submodule():
    """Regression test for a real naming conflict: the resample backend
    module is named _resample.py (private) specifically so that
    olab_audio.resample can be the original callable function, matching
    ub_audio.resample's original API, with no ambiguity between "the
    function" and "a submodule named resample"."""
    assert callable(olab_audio.resample)
    ys = [1.0, 2.0, 3.0]
    import numpy as np
    result = olab_audio.resample(np.array(ys, dtype='float32'), 16000, 16000)
    assert list(result) == ys  # same-rate is a no-op, doesn't need soxr


def test_core_names_available_without_analysis_extra():
    # Just confirms these don't accidentally require __getattr__/analysis.
    assert olab_audio.Mic is not None
    assert olab_audio.Speaker is not None
    assert olab_audio.Recording_np is not None
    assert callable(olab_audio.get_input_devices)


def test_unknown_attribute_raises_normal_attributeerror():
    with pytest.raises(AttributeError, match="no attribute"):
        olab_audio.definitely_not_a_real_name


def test_analysis_names_resolve_when_extra_is_installed():
    librosa = pytest.importorskip("librosa", reason="librosa is not installed; install olab-audio[analysis]")

    # Every one of these must resolve via __getattr__ to the real
    # olab_audio.analysis symbol -- this is the "preserve the original flat
    # namespace" requirement, not just an olab_audio.analysis.X path.
    assert olab_audio.Wave is not None
    assert olab_audio.Spectrum is not None
    assert olab_audio.Spectrogram is not None
    assert callable(olab_audio.createTone)
    assert callable(olab_audio.createChirp)
    assert callable(olab_audio.createPitch)
    assert callable(olab_audio.trim)
    assert callable(olab_audio.read_wave)
    assert callable(olab_audio.read_wave_librosa)
    assert callable(olab_audio.decorate)
    assert callable(olab_audio.legend)
    assert callable(olab_audio.unbias)
    assert callable(olab_audio.normalize)
    assert callable(olab_audio.ftt_freq)
    assert isinstance(olab_audio.pitch_map, dict)

    from olab_audio import analysis as analysis_module
    assert olab_audio.Wave is analysis_module.Wave


def test_analysis_name_raises_clear_attributeerror_when_extra_missing(monkeypatch):
    """When the analysis extra genuinely isn't installed, accessing an
    analysis-only root name must raise a clear, actionable AttributeError
    -- not a raw ImportError from deep inside analysis.py's own `import
    librosa` line."""
    import sys

    # If another test in this run already imported olab_audio.analysis
    # successfully (e.g. when the analysis extra genuinely is installed),
    # it's cached both in sys.modules AND as an attribute directly on the
    # olab_audio package object itself (Python sets that automatically on
    # first submodule import) -- `from . import analysis` can resolve via
    # either one without re-attempting the import. Evict both.
    monkeypatch.delitem(sys.modules, "olab_audio.analysis", raising=False)
    monkeypatch.delattr(olab_audio, "analysis", raising=False)

    # Simulate librosa genuinely not being installed -- a `None` entry in
    # sys.modules is the standard way to make `import librosa` raise
    # ImportError immediately, without needing to intercept __import__
    # itself (fragile: `from . import analysis` calls __import__('', ...,
    # fromlist=('analysis',)), not __import__('analysis', ...)).
    monkeypatch.setitem(sys.modules, "librosa", None)

    with pytest.raises(AttributeError, match="analysis"):
        olab_audio.Wave
