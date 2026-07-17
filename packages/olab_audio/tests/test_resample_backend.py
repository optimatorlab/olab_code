import numpy as np
import pytest

from olab_audio._resample import StreamResampler, resample


def test_resample_same_rate_is_a_true_noop_and_never_imports_soxr(monkeypatch):
    import olab_audio._resample as resample_module

    def _require_soxr_should_not_be_called():
        raise AssertionError("resample() must not need soxr on the same-rate path")

    monkeypatch.setattr(resample_module, "_require_soxr", _require_soxr_should_not_be_called)

    ys = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    result = resample(ys, 16000, 16000)

    assert result is ys  # literally the same object, not just equal


def test_resample_missing_soxr_raises_clear_error(monkeypatch):
    import olab_audio._resample as resample_module

    def _require_soxr_missing():
        raise RuntimeError("olab-audio needs the 'resample' extra to convert between sample rates.")

    monkeypatch.setattr(resample_module, "_require_soxr", _require_soxr_missing)

    with pytest.raises(RuntimeError, match="resample"):
        resample(np.zeros(10, dtype=np.float32), 32000, 16000)


def test_resample_32k_to_16k_halves_the_length():
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")

    ys = np.zeros(3200, dtype=np.float32)  # 100ms @ 32kHz
    result = resample(ys, 32000, 16000)

    assert 1550 <= len(result) <= 1650  # ~1600 samples @ 16kHz, allow soxr's edge variance


def test_stream_resampler_same_rate_passthrough_never_imports_soxr(monkeypatch):
    import olab_audio._resample as resample_module

    monkeypatch.setattr(
        resample_module, "_require_soxr",
        lambda: (_ for _ in ()).throw(AssertionError("must not import soxr for same-rate stream")),
    )

    sr = StreamResampler(orig_sr=16000, target_sr=16000)
    chunk = np.array([1.0, 2.0, 3.0], dtype=np.float32)

    out = sr.process(chunk)
    assert list(out) == [1.0, 2.0, 3.0]

    flushed = sr.flush()
    assert len(flushed) == 0


def test_stream_resampler_frame_continuity_across_chunks():
    """Chunk-by-chunk conversion must use one continuous converter, not a
    fresh one per chunk (which would cause boundary artifacts) -- verified
    by checking total output length tracks total input length across many
    small chunks, not just a single big one."""
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")

    orig_sr, target_sr = 32000, 16000
    total_samples = 3200  # 100ms @ 32kHz
    chunk_size = 320  # 10ms chunks -- 10 chunks total

    sr = StreamResampler(orig_sr=orig_sr, target_sr=target_sr)
    chunks_out = []
    for i in range(0, total_samples, chunk_size):
        chunk = np.zeros(chunk_size, dtype=np.float32)
        chunks_out.append(sr.process(chunk))

    flushed = sr.flush()
    total_out = sum(len(c) for c in chunks_out) + len(flushed)

    expected = total_samples * target_sr / orig_sr  # 1600
    assert abs(total_out - expected) <= 32  # small streaming-boundary tolerance


def test_stream_resampler_flush_emits_remaining_buffered_samples():
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")

    sr = StreamResampler(orig_sr=32000, target_sr=16000)
    sr.process(np.zeros(100, dtype=np.float32))  # a small, non-block-aligned chunk

    flushed = sr.flush()

    # flush() must return an array (possibly containing the tail end of the
    # conversion), not raise and not silently drop buffered state.
    assert isinstance(flushed, np.ndarray)


def test_stream_resampler_stereo_stays_flat_interleaved_in_and_out():
    """Real bug: a flat interleaved stereo buffer passed straight to
    soxr.ResampleStream.resample_chunk() (which requires 2D
    (frames, channels) for anything but mono) gets silently misinterpreted
    as a mono signal twice as long. process()/flush() must reshape to 2D
    only internally, around the soxr call, and always take/return flat
    interleaved 1D."""
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")

    sr = StreamResampler(orig_sr=32000, target_sr=16000, channels=2)

    # 160 interleaved stereo frames @ 32kHz (10ms) = 320 array elements.
    chunk = np.zeros(320, dtype=np.float32)
    out = sr.process(chunk)
    assert out.ndim == 1  # flat, not left as (frames, 2)

    flushed = sr.flush()
    assert flushed.ndim == 1

    total_elements = len(out) + len(flushed)
    # 10ms @ 16kHz, stereo -> ~80 frames -> ~160 interleaved elements.
    assert 140 <= total_elements <= 180
    # A whole number of stereo frames -- not an odd element count, which
    # would indicate channels got scrambled during reshape/flatten.
    assert total_elements % 2 == 0


def test_stream_resampler_stereo_frame_count_matches_mono_equivalent():
    """The bug this guards against would treat a flat stereo buffer as a
    mono buffer twice as long, roughly doubling the apparent frame count
    soxr sees. Compares against the known-correct mono case at the same
    total sample count to catch that class of error."""
    pytest.importorskip("soxr", reason="soxr is not installed; install olab-audio[resample]")

    orig_sr, target_sr = 32000, 16000
    frames = 320  # 10ms @ 32kHz

    mono = StreamResampler(orig_sr=orig_sr, target_sr=target_sr, channels=1)
    mono_out = mono.process(np.zeros(frames, dtype=np.float32))
    mono_flushed = mono.flush()
    mono_total = len(mono_out) + len(mono_flushed)  # ~160 frames

    stereo = StreamResampler(orig_sr=orig_sr, target_sr=target_sr, channels=2)
    stereo_out = stereo.process(np.zeros(frames * 2, dtype=np.float32))  # same #frames, interleaved
    stereo_flushed = stereo.flush()
    stereo_total_frames = (len(stereo_out) + len(stereo_flushed)) // 2

    assert abs(stereo_total_frames - mono_total) <= 2
