import numpy as np
import pytest

librosa = pytest.importorskip("librosa", reason="librosa is not installed; install olab-audio[analysis]")

from olab_audio import analysis  # noqa: E402


def test_ftt_freq_does_not_raise_nameerror():
    """Real bug found in the original source: ftt_freq()'s body referenced
    `nfft` while its parameter was named `n_fft` -- a NameError on any call."""
    freqs = analysis.ftt_freq(samplerate=22050, n_fft=2048)

    assert len(freqs) == 1 + 2048 // 2


def test_wave_zero_pad_does_not_raise_nameerror():
    """Real bug found in the original source: Wave.zero_pad() called a
    module-level zero_pad() that was never defined anywhere in the file."""
    wave = analysis.Wave(np.array([1.0, 2.0, 3.0], dtype=np.float32), framerate=100)

    wave.zero_pad(5)

    assert len(wave.ys) == 5
    assert list(wave.ys[:3]) == [1.0, 2.0, 3.0]
    assert list(wave.ys[3:]) == [0.0, 0.0]


def test_wave_add_mismatched_times_does_not_raise_nameerror(monkeypatch):
    """Real bug found in the original source: Wave.__add__() called
    warnings.warn(...) but `warnings` was never imported anywhere in the
    file -- a NameError specifically on the code path meant to warn about
    misaligned waveforms, not crash on it. Force the misalignment branch
    deterministically (rather than relying on fragile floating-point
    grid-alignment luck) by making find_index() return an index whose
    time is far from the wave's actual start.
    """
    # The warn condition only fires when ts[i] is *ahead* of wave.start (a
    # positive diff/dt ratio), and the slice ys[i:i+len(wave)] must stay in
    # bounds. Pin find_index() to index 1: for the first wave (start=0.0),
    # ts[1]=0.01 is enough ahead of 0.0 to exceed the 0.1*dt threshold,
    # while len(wave)=3 keeps the resulting slice safely in bounds.
    monkeypatch.setattr(analysis, "find_index", lambda x, xs: 1)

    a = analysis.Wave(np.array([1.0, 2.0, 3.0], dtype=np.float32), ts=np.array([0.0, 0.01, 0.02]), framerate=100)
    b = analysis.Wave(np.array([1.0, 2.0, 3.0], dtype=np.float32), ts=np.array([5.0, 5.01, 5.02]), framerate=100)

    with pytest.warns(UserWarning):
        result = a + b

    assert isinstance(result, analysis.Wave)


def test_read_wave_uses_frombuffer_not_deprecated_fromstring(tmp_path):
    """np.fromstring for binary data is deprecated/removed in modern numpy
    -- read_wave() now uses np.frombuffer, matching the rest of the module."""
    from wave import open as open_wave

    path = tmp_path / "test.wav"
    with open_wave(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(np.array([100, -100, 200], dtype=np.int16).tobytes())

    wave = analysis.read_wave(str(path))

    assert len(wave.ys) == 3


def test_create_tone_returns_wave():
    tone = analysis.createTone(440.0, sr=8000, duration=0.01)

    assert isinstance(tone, analysis.Wave)
    assert tone.framerate == 8000


def test_analysis_resample_is_reexported_and_usable():
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")

    ys = np.zeros(320, dtype=np.float32)
    result = analysis.resample(ys, 32000, 16000)

    assert len(result) < len(ys)
