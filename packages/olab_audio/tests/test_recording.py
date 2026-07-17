import numpy as np
import pytest

from olab_audio.recording import Recording_bytes, Recording_np, append, saveAudio


def test_append_concatenates_same_framerate_arrays():
    a = np.array([1.0, 2.0], dtype=np.float32)
    b = np.array([3.0, 4.0], dtype=np.float32)

    result = append(a, b)

    assert list(result) == [1.0, 2.0, 3.0, 4.0]


def test_recording_np_append_does_not_concatenate_per_chunk(monkeypatch):
    """Real bug: append() previously did np.concatenate() on every single
    PortAudio callback, copying the entire accumulated recording every
    time -- O(n) work per chunk, so total recording cost grows
    quadratically with duration (allocation pressure and callback-overrun
    risk on a long recording). Chunks must accumulate in a list during
    capture; concatenation happens at most once, lazily, on ys access."""
    concat_calls = []
    real_concatenate = np.concatenate
    monkeypatch.setattr(np, "concatenate", lambda *a, **k: concat_calls.append(1) or real_concatenate(*a, **k))

    rec = Recording_np(samplerateMic=44100, samplerateRec=44100)
    for _ in range(50):
        rec.append(np.zeros(10, dtype=np.float32))

    assert concat_calls == []  # no concatenation at all during capture itself

    ys = rec.ys  # first access after capture: concatenates once
    assert len(ys) == 500
    assert len(concat_calls) == 1

    _ = rec.ys  # repeated access must not re-concatenate (cached)
    assert len(concat_calls) == 1

    rec.append(np.zeros(10, dtype=np.float32))  # one more chunk after caching
    assert len(concat_calls) == 1  # appending itself still doesn't concatenate

    ys2 = rec.ys  # accessing again after a new chunk: cache was invalidated, re-concatenates once
    assert len(ys2) == 510
    assert len(concat_calls) == 2


def test_recording_np_same_rate_append_never_imports_soxr(monkeypatch):
    """The normal/default path (recordStart() with no explicit samplerateRec
    uses the mic's own native rate) must never touch soxr at all -- not even
    to check whether it's installed. Recording_np always constructs a
    StreamResampler, but a same-rate one is a passthrough (see
    StreamResampler.__init__) and must not import soxr itself."""
    import olab_audio._resample as resample_module

    monkeypatch.setattr(
        resample_module, "_require_soxr",
        lambda: (_ for _ in ()).throw(AssertionError("must not need soxr on the same-rate path")),
    )

    rec = Recording_np(samplerateMic=44100, samplerateRec=44100)
    rec.append(np.array([0.1, 0.2, 0.3], dtype=np.float32))

    assert len(rec.ys) == 3
    assert rec.duration == pytest.approx(3 / 44100)


def test_recording_np_cross_rate_fails_early_without_resample_extra(monkeypatch):
    """Must fail at construction (synchronously, before recording ever
    starts) if resample extra isn't installed and a cross-rate recording
    was requested -- never surface only later from inside the PortAudio
    callback thread's first append()."""
    import olab_audio._resample as resample_module

    def _require_soxr_missing():
        raise RuntimeError("olab-audio needs the 'resample' extra")

    monkeypatch.setattr(resample_module, "_require_soxr", _require_soxr_missing)

    with pytest.raises(RuntimeError, match="resample"):
        Recording_np(samplerateMic=32000, samplerateRec=16000)


def test_recording_np_cross_rate_append_uses_streaming_resampler():
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")

    rec = Recording_np(samplerateMic=32000, samplerateRec=16000)
    chunk = np.zeros(320, dtype=np.float32)  # 10ms @ 32kHz

    rec.append(chunk)
    rec._flush_stream_resampler()

    # 10ms @ 16kHz should be ~160 samples -- allow soxr's normal small variance.
    assert 140 <= len(rec.ys) <= 180


def test_recording_np_append_rejects_a_different_orig_sr_mid_recording():
    """The StreamResampler is constructed once, for one fixed input rate --
    switching orig_sr per call would silently break its internal state."""
    rec = Recording_np(samplerateMic=44100, samplerateRec=44100)

    with pytest.raises(ValueError, match="samplerateMic"):
        rec.append(np.zeros(10, dtype=np.float32), orig_sr=22050)


def test_recording_np_multichunk_matches_one_continuous_conversion():
    """Reproduces the reviewer's concern directly: chunked, streaming
    conversion (what Recording_np.append() actually does, called once per
    PortAudio callback) must match a single one-shot conversion of the same
    full signal -- not just be "close enough" per chunk. Compares total
    length and RMS energy (a stateless per-chunk resample() call instead of
    a persistent StreamResampler would introduce boundary-artifact energy
    the one-shot reference doesn't have)."""
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")
    from olab_audio._resample import resample

    orig_sr, target_sr = 32000, 16000
    rng = np.random.default_rng(0)
    full_signal = rng.uniform(-0.5, 0.5, size=3200).astype(np.float32)  # 100ms @ 32kHz
    chunk_size = 320  # 10ms chunks

    # Reference: one continuous, non-streaming conversion of the whole signal.
    reference = resample(full_signal, orig_sr, target_sr)

    # Actual: what Recording_np does -- chunk-by-chunk through append(),
    # flushed once at save()/make_wave() time.
    rec = Recording_np(samplerateMic=orig_sr, samplerateRec=target_sr)
    for i in range(0, len(full_signal), chunk_size):
        rec.append(full_signal[i:i + chunk_size])
    rec._flush_stream_resampler()

    assert abs(len(rec.ys) - len(reference)) <= 4
    # RMS energy should match closely -- a fresh one-shot resample() call
    # per chunk (the bug this test guards against) introduces discontinuity
    # energy at every chunk boundary that a persistent streaming converter
    # does not.
    n = min(len(rec.ys), len(reference))
    rms_diff = np.sqrt(np.mean((rec.ys[:n] - reference[:n]) ** 2))
    assert rms_diff < 0.05


def test_recording_bytes_does_not_resample():
    rec = Recording_bytes(samplerateMic=44100, samplerateRec=44100)
    rec.append(b'\x00\x00' * 100)

    assert rec.ys == [b'\x00\x00' * 100]


def test_recording_bytes_rejects_cross_rate_construction():
    """Recording_bytes never resamples -- silently accepting a different
    samplerateRec would save original-rate bytes labeled with the wrong
    rate, producing audibly wrong playback speed/pitch with no error."""
    with pytest.raises(ValueError, match="cross-rate"):
        Recording_bytes(samplerateMic=44100, samplerateRec=22050)


def test_recording_bytes_duration_counts_frames_not_chunks():
    """Real bug (present in the original ub_audio.py too): duration was
    len(self.ys)/samplerateRec, and self.ys is a list of *chunks* for
    Recording_bytes, not samples -- massively undercounting duration and
    breaking timeLimitSec. int16 mono @ 8000Hz: 2 bytes/frame."""
    rec = Recording_bytes(samplerateMic=8000, samplerateRec=8000, channels=1)

    one_chunk = (b'\x00\x00') * 800  # 800 frames per chunk
    rec.append(one_chunk)
    rec.append(one_chunk)

    assert rec.duration == pytest.approx(1600 / 8000)


def test_recording_np_duration_accounts_for_channels():
    """Real bug: duration was len(self.ys)/samplerateRec, and Recording_np's
    ys is an interleaved multi-channel array -- for stereo, len(ys) is
    2x the actual frame count."""
    rec = Recording_np(samplerateMic=44100, samplerateRec=44100, channels=2)

    # 100 interleaved stereo frames = 200 array elements.
    interleaved = np.zeros(200, dtype=np.float32)
    rec.append(interleaved)

    assert rec.duration == pytest.approx(100 / 44100)


def test_save_stereo_recording_np_writes_correct_wav_header(tmp_path):
    """Real bug: Recording.save() called saveAudio() without passing
    self.channels/self.frmt, so saveAudio() defaulted to mono -- a stereo
    recording's interleaved samples got a one-channel WAV header, doubling
    apparent duration and corrupting playback interpretation."""
    rec = Recording_np(samplerateMic=44100, samplerateRec=44100, channels=2,
                        filepath=str(tmp_path), filename="stereo_np.wav")

    # 50 interleaved stereo frames = 100 array elements.
    rec.append(np.zeros(100, dtype=np.float32))
    rec.save()

    from wave import open as open_wave
    with open_wave(str(tmp_path / "stereo_np.wav"), "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getframerate() == 44100
        assert wf.getnframes() == 50
        assert wf.getnframes() / wf.getframerate() == pytest.approx(rec.duration)


def test_save_stereo_recording_bytes_writes_correct_wav_header(tmp_path):
    rec = Recording_bytes(samplerateMic=44100, samplerateRec=44100, channels=2,
                           filepath=str(tmp_path), filename="stereo_bytes.wav")

    # int16, stereo: 4 bytes/frame. 50 frames = 200 bytes.
    rec.append(b'\x00\x00' * 100)
    rec.save()

    from wave import open as open_wave
    with open_wave(str(tmp_path / "stereo_bytes.wav"), "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getframerate() == 44100
        assert wf.getnframes() == 50
        assert wf.getnframes() / wf.getframerate() == pytest.approx(rec.duration)


def test_cross_rate_stereo_recording_shape_and_metadata(tmp_path):
    """Real bug: Mic._callback_np() produces a flat interleaved array, but
    Recording_np passed that flat buffer straight to soxr.ResampleStream,
    which requires 2D (frames, channels) for anything but mono -- silently
    misinterpreting a stereo buffer as a much-longer mono one. Covers
    output shape/frame count/duration/saved WAV metadata end-to-end for a
    32kHz -> 16kHz stereo recording."""
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")

    rec = Recording_np(samplerateMic=32000, samplerateRec=16000, channels=2,
                        filepath=str(tmp_path), filename="stereo_cross_rate.wav")

    # 160 interleaved stereo frames @ 32kHz (10ms) = 320 array elements.
    rec.append(np.zeros(320, dtype=np.float32))
    rec._flush_stream_resampler()

    # 10ms @ 16kHz, stereo -> ~80 frames -> ~160 interleaved elements.
    assert rec.ys.ndim == 1  # still flat/interleaved, not left as 2D
    assert 140 <= len(rec.ys) <= 180
    assert len(rec.ys) % rec.channels == 0
    expected_frames = len(rec.ys) // rec.channels
    assert rec._frame_count == expected_frames
    assert rec.duration == pytest.approx(expected_frames / 16000)

    rec.save()
    from wave import open as open_wave
    with open_wave(str(tmp_path / "stereo_cross_rate.wav"), "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == expected_frames


def test_recording_np_resample_after_cross_rate_capture_does_not_double_convert():
    """Real bug: after a 32kHz -> 16kHz Recording_np has been captured,
    self.ys is already at 16kHz -- but .resample() defaulted framerateOrig
    to self.samplerateMic (32kHz), treating already-16kHz data as if it
    were still 32kHz and converting it *again* to 16kHz. framerateOrig must
    default to self.samplerateRec (the rate self.ys is actually at), not
    self.samplerateMic."""
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")
    librosa = pytest.importorskip("librosa", reason="librosa is not installed; install olab-audio[analysis]")

    rec = Recording_np(samplerateMic=32000, samplerateRec=16000)
    rec.append(np.zeros(3200, dtype=np.float32))  # 100ms @ 32kHz
    rec._flush_stream_resampler()
    frames_after_capture = rec._frame_count
    assert 1550 <= frames_after_capture <= 1650  # ~1600 @ 16kHz

    # Calling resample() with no args (both default) must be a same-rate
    # no-op on the already-16kHz data -- NOT a second 32kHz->16kHz pass.
    rec.resample()

    assert rec.samplerateRec == 16000
    assert rec._frame_count == pytest.approx(frames_after_capture, abs=2)
    assert len(rec.ys) == pytest.approx(frames_after_capture, abs=2)


def test_recording_np_resample_to_a_third_rate_after_cross_rate_capture():
    """Explicit resample to yet another rate (e.g. 16kHz -> 8kHz) after a
    cross-rate capture must treat the data as what it actually is (16kHz),
    not the mic's original native rate (32kHz)."""
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")
    librosa = pytest.importorskip("librosa", reason="librosa is not installed; install olab-audio[analysis]")

    rec = Recording_np(samplerateMic=32000, samplerateRec=16000)
    rec.append(np.zeros(3200, dtype=np.float32))  # 100ms @ 32kHz -> ~1600 @ 16kHz
    rec._flush_stream_resampler()

    rec.resample(framerateNew=8000)

    assert rec.samplerateRec == 8000
    # ~1600 frames @ 16kHz -> ~800 frames @ 8kHz (not ~400, which would
    # happen if the buggy code treated the input as still being at 32kHz).
    assert 750 <= len(rec.ys) <= 850


def test_make_wave_requires_analysis_extra_when_missing(monkeypatch):
    rec = Recording_np(samplerateMic=44100, samplerateRec=44100)
    rec.append(np.array([0.1, 0.2], dtype=np.float32))

    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        # `from .analysis import Wave` inside make_wave() calls
        # __import__('analysis', ..., level=1) -- the bare module name, not
        # the dotted 'olab_audio.analysis' path.
        if name == 'analysis' or name.endswith('.analysis'):
            raise ImportError("No module named 'librosa'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(RuntimeError, match="analysis"):
        rec.make_wave()


def test_save_audio_np_array_uses_stdlib_wave_no_soundfile_needed(tmp_path):
    """Core recording save must work with only pyaudio+pulsectl+numpy
    installed -- no soundfile/analysis extra required."""
    data = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)

    saveAudio(filepath=str(tmp_path), filename="out.wav", data=data, samplerate=16000)

    from wave import open as open_wave
    with open_wave(str(tmp_path / "out.wav"), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2  # int16
        assert wf.getnframes() == len(data)


def test_save_audio_bytes_list_uses_stdlib_wave(tmp_path):
    """No PyAudio instance needed -- saveAudio() uses the module-level,
    hardware-free pyaudio.get_sample_size() function, not a PyAudio()
    instance method, so this never touches the lazy audio singleton."""
    saveAudio(filepath=str(tmp_path), filename="out_bytes.wav", data=[b'\x00\x00\x00\x00'], samplerate=8000)

    from wave import open as open_wave
    with open_wave(str(tmp_path / "out_bytes.wav"), "rb") as wf:
        assert wf.getframerate() == 8000
