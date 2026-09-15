import numpy as np


def defaultFromNone(val, default, test=None):
    """If user doesn't specify a value, return a default."""
    try:
        if val is None:
            val = default

        if test in (int, float, str):
            return test(val)
        else:
            return val
    except Exception as e:
        raise Exception(f'Error in defaultFromNone: {e}')


def convert_to_db(data):
    try:
        amp = np.max(np.abs(data))
        if amp == 0:
            return -60
        else:
            return 20 * np.log10(amp)  # or 20*log10(amp/ref), where ref = 1
    except Exception:
        return -61


# Live mic spectrogram (rig.html Sensors/Media panel) support — see
# .pairwork/rig-mic-spectrogram/plan.md in the ofm repo for the full design
# history. Kept in this small, dependency-free module (plain numpy only,
# same as convert_to_db above) rather than analysis.py, since analysis.py's
# heavier deps (librosa/soundfile/matplotlib) are reserved for the opt-in
# `analysis` extra.
_FALLBACK_N_BINS = 64        # only used if n_bins itself can't be interpreted as a positive int
_DB_FLOOR_DBFS = -60.0       # single source of truth for the DISPLAY floor — matches
                             # convert_to_db's own -60 dBFS floor; every "no signal here"
                             # value in spectrum_db's returned array uses this.
_EXCEPTION_SENTINEL_DBFS = -61.0  # deliberately a DIFFERENT, more-negative value: distinguishes
                                   # "spectrum_db itself failed" from a genuinely measured silent
                                   # bin. Intentionally not unified with _DB_FLOOR_DBFS — the two
                                   # sentinels mean different things and collapsing them would lose that.
_DEFAULT_N_BINS = 64
_DEFAULT_FMIN = 80.0
_DEFAULT_FMAX = 8000.0


def spectrum_db(data, samplerate, n_bins=_DEFAULT_N_BINS, fmin=_DEFAULT_FMIN, fmax=_DEFAULT_FMAX):
    """Compute a log-frequency-binned, dB-scale spectrum from one buffer of
    audio samples. Mirrors convert_to_db's degrade-gracefully style: never
    raises, returns a sentinel-filled list instead.

    data       - 1-D numpy array of samples (e.g. Mic.np_data).
    samplerate - sample rate of `data`, in Hz.
    n_bins     - number of log-spaced frequency bins to return.
    fmin, fmax - frequency range (Hz) the bins span.

    Returns a list of `n_bins` floats, log-spaced over [fmin, fmax), on a
    dBFS-like scale consistent with convert_to_db's -60..0 convention (the
    FFT magnitude is normalized by 2.0/data.size so a full-scale input
    reads ~0 dB, matching convert_to_db's own full-scale-is-0dB behavior).
    No returned value is ever below _DB_FLOOR_DBFS.

    No window function is applied before rfft — for a heat-map display
    this is fine, but an off-bin tone reads a couple dB low from
    rectangular-window scalloping loss; don't add a window casually, since
    it would change the 2.0/data.size coherent-gain constant above too.

    Bins with no FFT energy in their log-spaced range (common at the low
    end when a log bin is narrower than one FFT bin's frequency
    resolution) are filled by interpolating from the nearest populated
    bin rather than left at the silence floor — at a 512-sample buffer and
    the 80-8000Hz/64-bin defaults, roughly a third of the low-frequency
    bins at common samplerates are interpolated, not directly measured;
    this trades exact low-end resolution for a continuous-looking display
    rather than a permanently blank low band.
    """
    try:
        n = int(n_bins)
        if n <= 0:
            raise ValueError('n_bins must be positive')
    except Exception:
        return [_EXCEPTION_SENTINEL_DBFS] * _FALLBACK_N_BINS

    try:
        if data.size == 0 or samplerate <= 0 or fmin <= 0 or fmax <= fmin:
            return [_DB_FLOOR_DBFS] * n

        mag = np.abs(np.fft.rfft(data)) * (2.0 / data.size)
        freqs = np.fft.rfftfreq(data.size, d=1.0 / samplerate)
        edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)

        bin_idx = np.searchsorted(edges, freqs, side='right') - 1
        valid = (bin_idx >= 0) & (bin_idx < n) & (freqs >= fmin) & (freqs < fmax)
        amp = np.zeros(n, dtype=np.float64)
        np.maximum.at(amp, bin_idx[valid], mag[valid])

        populated = amp > 0
        if populated.any() and not populated.all():
            idx = np.arange(n)
            amp = np.interp(idx, idx[populated], amp[populated])

        db = np.where(amp > 0, 20 * np.log10(np.maximum(amp, 1e-12)), _DB_FLOOR_DBFS)
        db = np.maximum(db, _DB_FLOOR_DBFS)   # floor the RESULT, not just the amplitude —
                                               # a real bin can otherwise measure far below
                                               # -60dB and be indistinguishable from silence
        return [float(b) for b in db]
    except Exception:
        return [_EXCEPTION_SENTINEL_DBFS] * n


def bytes2np(bytesarray, dtype):
    """bytesarray is raw sound data. dtype is an np data type, e.g. 'int16', 'float32'."""
    return np.frombuffer(bytesarray, dtype=dtype)


def np2bytes(nparray):
    return nparray.tobytes()


def np2np(ys, newDtype, scaling=1):
    """Convert a given np array (`ys`) to type `newDtype`, optionally scaling (normalizing) it."""
    return ys.astype(newDtype) * scaling
