"""MicArray tests: fake PortAudio (no hardware); the captured stream callback is
driven synchronously with synthetic buffers/time_info. Writers, files and
manifests are real and read back with the stdlib `wave`/`json`."""

import json
import threading
import wave

import numpy as np
import pyaudio
import pytest

import olab_audio
from olab_audio import device, micarray
from olab_audio.micarray import MicArray

_real_wave_open = wave.open
SR = 48000
FPB = 16          # tiny buffers keep the files tiny
JACKS = (1, 3, 6)
WIDTH = 18        # the fake device's maxInputChannels: the stream always opens this many


class _FakeStream:
    '''Mimics the real UMC1820/ALSA failure: get_time() raises OSError(-9986) and
    kills the stream (is_active() then raises 'Stream not open', no callbacks).'''

    def __init__(self, inactive=False):
        self.stopped = False
        self.closed = False
        self.dead = False
        self.inactive = inactive
        self.fail_stop = False

    def stop_stream(self):
        self.stopped = True
        if self.fail_stop:
            raise OSError(-9983, 'Stream is stopped')

    def close(self):
        self.closed = True

    def get_time(self):
        self.dead = True
        raise OSError(-9986, 'Internal Error')

    def is_active(self):
        if self.dead:
            raise OSError('Stream not open')
        return not self.inactive


class _FakeAudio:
    DEVICES = [('hw:0,0 Intel', 2), ('UMC1820 hw:1,0', 18), ('vdownmix', 2), ('pipewire', 2)]

    def __init__(self):
        self.opened = []
        self.fail_open = False
        self.inactive = False
        self.callback = None
        self.stream = None

    def get_device_count(self):
        return len(self.DEVICES)

    def get_device_info_by_index(self, i):
        name, ch = self.DEVICES[i]
        return {'index': i, 'name': name, 'maxInputChannels': ch, 'defaultSampleRate': 44100.0}

    def get_host_api_info_by_index(self, i):
        return {'deviceCount': len(self.DEVICES), 'type': pyaudio.paALSA}

    def get_device_info_by_host_api_device_index(self, host, i):
        return self.get_device_info_by_index(i)

    def get_default_input_device_info(self):
        return self.get_device_info_by_index(0)

    def open(self, **kwargs):
        if self.fail_open:
            raise OSError('[Errno -9997] Invalid sample rate')
        self.opened.append(kwargs)
        self.callback = kwargs['stream_callback']
        self.stream = _FakeStream(self.inactive)
        return self.stream


@pytest.fixture
def fake(monkeypatch):
    f = _FakeAudio()
    monkeypatch.setattr(micarray, 'audio', f)
    monkeypatch.setattr(device, 'audio', f)
    monkeypatch.setattr(micarray.time, 'time', lambda: 1000.0)
    return f


class Rig:
    '''A MicArray on the fake device plus helpers to feed synthetic buffers.'''

    def __init__(self, fake, tmp_path, max_queue_buffers=128, excFunc=None, **kw):
        self.fake = fake
        self.dir = str(tmp_path / 'out')
        self.errs = []
        self.channels = [{'label': 'B1', 'channel': JACKS[0], 'meta': {'sensitivity': 1.5, 'nested': {'a': [1, 2]}}},
                         {'label': 'B2', 'channel': JACKS[1]},
                         {'label': 'B3', 'channel': JACKS[2], 'meta': {'x': 'y'}}]
        self.array = MicArray(self.channels, deviceName='umc', samplerate=SR, frames_per_buffer=FPB,
                              max_queue_buffers=max_queue_buffers,
                              excFunc=excFunc or (lambda msg: self.errs.append(msg)), **kw)
        self.t = 100.0       # synthetic ADC time of the next buffer
        self.off = 0         # sample-value offset of the next buffer

    def buf(self, n=FPB):
        a = np.zeros((n, WIDTH), dtype=np.int32)
        for c in range(WIDTH):
            a[:, c] = (c + 1) * 100000 + self.off + np.arange(n)
        return a

    def feed(self, n=FPB, flagged=False, adc=None):
        a = self.buf(n)
        assert not self.fake.stream.dead, 'a dead stream delivers no callbacks'
        t = self.t if adc is None else adc
        info = {'input_buffer_adc_time': t, 'current_time': t + 0.2}
        self.fake.callback(a.tobytes(), n, info, pyaudio.paInputOverflow if flagged else 0)
        self.t += n / SR
        self.off += n
        return a

    def skip(self, frames):
        self.t += frames / SR


def read_wav(path):
    w = _real_wave_open(path, 'rb')
    try:
        return (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), \
            np.frombuffer(w.readframes(w.getnframes()), dtype='<i4')
    finally:
        w.close()


def manifest(take):
    with open(take['manifest']) as fh:
        return json.load(fh)


def run_with_timeout(fn, timeout=5):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), 'call hung'
    return out[0] if out else None


def test_demux_label_order_span_and_levels(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    got = []
    rig.array.subscribe(lambda data, labels: got.append((data.copy(), labels)))
    assert rig.array.start() is True
    kw = fake.opened[0]
    assert kw['channels'] == WIDTH and kw['rate'] == SR and kw['format'] == pyaudio.paInt32
    assert kw['input_device_index'] == 1 and kw['frames_per_buffer'] == FPB
    assert rig.array.labels == ['B1', 'B2', 'B3'] and rig.array.micOn is True
    a = rig.feed()
    data, labels = got[0]
    assert labels == ['B1', 'B2', 'B3'] and data.dtype == np.float32 and data.shape == (FPB, 3)
    for k, jack in enumerate(JACKS):
        np.testing.assert_allclose(data[:, k], a[:, jack - 1] / 2147483648.0, rtol=1e-6)
    assert all(-120 < v < 0 for v in rig.array.db.values())
    # full-scale sample -> clip indicator; reset clears it
    full = np.zeros((FPB, WIDTH), dtype=np.int32)
    full[0, :] = -2 ** 31
    fake.callback(full.tobytes(), FPB, {'input_buffer_adc_time': rig.t}, 0)
    assert rig.array.peak_db['B2'] >= -0.1
    rig.array.reset_peaks()
    assert rig.array.peak_db == rig.array.db       # reset restarts the peak at the current level
    assert olab_audio.MicArray is MicArray


def test_start_fails_cleanly_when_opened_stream_is_not_active(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(micarray, '_ACTIVE_WAIT_SECONDS', 0.0)
    rig = Rig(fake, tmp_path)
    fake.inactive = True
    assert rig.array.start() is False
    assert rig.array.micOn is False and fake.stream.closed is True
    assert any('not active' in e for e in rig.errs)
    assert rig.array.recordStart(rig.dir, 'take') is False
    rig.array.stop()                                   # safe, no stream to close
    fake.inactive = False
    assert rig.array.start() is True and rig.array.micOn is True


def test_open_channels_override_and_jack_too_high(fake, tmp_path):
    rig = Rig(fake, tmp_path, open_channels=8)
    assert rig.array.start() is True and fake.opened[0]['channels'] == 8     # override, not the device's 18
    with pytest.raises(ValueError):
        MicArray(rig.channels, open_channels=5)                              # below the highest jack (6)
    with pytest.raises(ValueError):
        MicArray(rig.channels, open_channels=True)
    too_wide = Rig(fake, tmp_path, open_channels=20)
    assert too_wide.array.start() is False and len(fake.opened) == 1         # device only has 18
    assert any('exceeds' in e for e in too_wide.errs)


def test_peak_db_never_below_db_and_liveness(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    assert rig.array.lastCallbackTime is None and rig.array.db['B1'] is None and rig.array.peak_db['B1'] is None
    rig.array.start()
    loud = rig.buf()
    quiet = np.full_like(loud, 7)             # about -141 dBFS: below the old -60 floor
    for data in (quiet, loud, quiet, quiet):
        fake.callback(data.tobytes(), FPB, {'input_buffer_adc_time': rig.t, 'current_time': rig.t}, 0)
        rig.t += FPB / SR
        db, peak = rig.array.db, rig.array.peak_db
        assert all(peak[l] >= db[l] for l in db)
        json.dumps(peak, allow_nan=False)      # stays JSON-serialisable for consumers
    assert rig.array.peak_db['B1'] > rig.array.db['B1']
    rig.array.reset_peaks()
    assert rig.array.peak_db == rig.array.db and rig.array.peak_db['B1'] < -100
    t = rig.array.lastCallbackTime
    assert isinstance(t, float)
    rig.array.stop()
    rig.array.start()
    assert rig.array.lastCallbackTime is None  # reset for the new stream


def test_start_validation_and_restart_after_failed_open(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    for kwargs in ({'deviceID': 9}, {'deviceName': 'nomatch'}, {'deviceID': 0}):   # range / name / too few channels
        arr = MicArray(rig.channels, excFunc=lambda msg: rig.errs.append(msg), **kwargs)
        assert arr.start() is False and arr.micOn is False
    assert fake.opened == [] and len(rig.errs) == 3
    fake.fail_open = True
    assert rig.array.start() is False and rig.array.micOn is False
    rig.array.stop()   # safe after a failed start
    fake.fail_open = False
    assert rig.array.start() is True and rig.array.micOn is True


@pytest.mark.parametrize('bad', [
    [], [{'label': 'a/b', 'channel': 1}], [{'label': 'A', 'channel': 0}], [{'label': 'A', 'channel': True}],
    [{'label': 'A', 'channel': 1}, {'label': 'A', 'channel': 2}],
    [{'label': 'A', 'channel': 1}, {'label': 'B', 'channel': 1}],
    [{'label': 'A', 'channel': 1, 'meta': {'f': float('nan')}}], [{'label': 'A', 'channel': 1, 'meta': {'f': object()}}],
])
def test_bad_channel_specs_rejected(bad):
    with pytest.raises(ValueError):
        MicArray(bad)


@pytest.mark.parametrize('kw', [{'max_queue_buffers': 0}, {'max_queue_buffers': -1}, {'max_queue_buffers': True},
                                {'samplerate': True}, {'frames_per_buffer': 0}])
def test_bad_constructor_ints_rejected(kw):
    with pytest.raises(ValueError):
        MicArray([{'label': 'A', 'channel': 1}], **kw)


@pytest.mark.parametrize('kw', [{'timeLimitSec': float('inf')}, {'timeLimitSec': float('nan')}, {'timeLimitSec': 0},
                                {'timeLimitSec': True}, {'labels': 'B1'}, {'labels': ['nope']}, {'basename': '../x'}])
def test_record_start_bad_args_return_false(fake, tmp_path, kw):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    args = {'filepath': rig.dir, 'basename': 'take'}
    args.update(kw)
    assert rig.array.recordStart(**args) is False and rig.array.isRecording is False
    assert rig.errs
    assert not list((tmp_path / 'out').glob('*'))


def test_recording_subset_files_readback_boundaries_and_manifest(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    rig.feed()
    rig.feed()
    assert rig.array.recordStart(rig.dir, 'take', labels=['B3', 'B1']) is True
    assert rig.array.isRecording is True
    rig.channels[0]['meta']['sensitivity'] = 999     # caller mutation must not leak into the manifest
    bufs = [rig.feed() for _ in range(3)]
    take = rig.array.recordStop()
    assert rig.array.isRecording is False and take['error'] is None and take['frames'] == 3 * FPB
    assert set(take['files']) == {'B1', 'B3'}
    for label, jack in (('B1', 1), ('B3', 6)):
        params, samples = read_wav(take['files'][label])
        assert params == (1, 4, SR, 3 * FPB)
        np.testing.assert_array_equal(samples, np.concatenate([b[:, jack - 1] for b in bufs]))
    m = manifest(take)
    assert m['samplerate'] == SR and m['frames_per_buffer'] == FPB and m['totalFrames'] == 3 * FPB
    assert m['startFrameIndex'] == 2 * FPB and m['gaps'] == [] and m['error'] is None
    # anchor = the take's first buffer's callback (wall 1000.0, current_time = adc + 0.2)
    assert m['startedAtWall'] == pytest.approx(1000.0 - 0.2)
    assert m['startedAtWallNote'] is None
    assert m['measuredFramesPerWallSecond'] == pytest.approx(SR)
    assert m['labels']['B1']['meta'] == {'sensitivity': 1.5, 'nested': {'a': [1, 2]}}
    assert m['labels']['B1']['channel'] == 1 and m['labels']['B3']['meta'] == {'x': 'y'}
    assert 'ESTIMATES' in m['gapNote']


def test_time_limit_stops_on_first_boundary_at_or_after_limit(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    done = threading.Event()
    got = []
    rig.array.recordStart(rig.dir, 'take', timeLimitSec=40 / SR,
                          postFunc=lambda take: (got.append(take), done.set()))
    for _ in range(5):
        rig.feed()
    assert done.wait(5)
    assert got[0]['frames'] == 3 * FPB          # 16, 32 < 40 <= 48: crossing buffer included, later ones not
    assert rig.array.isRecording is False and rig.array.lastTake is got[0]
    assert read_wav(got[0]['files']['B2'])[0][3] == 3 * FPB


def test_flagged_gap_zero_fill_all_channels(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    rig.array.recordStart(rig.dir, 'take', labels=['B1', 'B2'])
    rig.feed()
    rig.feed()
    rig.skip(100)
    last = rig.feed(flagged=True)
    take = rig.array.recordStop()
    assert take['gaps'] == [{'at_frame': 2 * FPB, 'est_lost_frames': 100, 'method': 'adc_time'}]
    for label, jack in (('B1', 1), ('B2', 3)):
        params, s = read_wav(take['files'][label])
        assert params[3] == 3 * FPB + 100
        assert not s[2 * FPB:2 * FPB + 100].any()
        np.testing.assert_array_equal(s[2 * FPB + 100:], last[:, jack - 1])
    assert manifest(take)['gaps'] == take['gaps']


def test_flagged_gap_edges_and_unusable_adc(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    rig.array.recordStart(rig.dir, 'a')
    rig.feed(flagged=True)     # first buffer of a take: any gap precedes the file, ignored
    rig.feed(flagged=True)     # flagged but contiguous ADC time: estimate 0, nothing inserted
    take = rig.array.recordStop()
    assert take['gaps'] == [{'at_frame': FPB, 'est_lost_frames': 0, 'method': 'adc_time'}] and take['frames'] == 2 * FPB

    rig.array.recordStart(rig.dir, 'b')      # ADC time stuck at 0: estimator unavailable
    rig.feed(adc=0.0)
    rig.feed(flagged=True, adc=0.0)
    rig.feed(flagged=True, adc=0.0)
    take = rig.array.recordStop()
    assert [g['method'] for g in take['gaps']] == ['flag_only', 'flag_only']
    assert take['gaps'][0]['est_lost_frames'] is None and take['frames'] == 3 * FPB
    assert manifest(take)['measuredFramesPerWallSecond'] is None
    assert sum('ADC time unusable' in e for e in rig.errs) == 1

    rig.array.recordStart(rig.dir, 'c')      # implausible forward jump is capped, never filled
    rig.feed()
    rig.skip(11 * SR)
    rig.feed(flagged=True)
    take = rig.array.recordStop()
    assert take['gaps'][0]['method'] == 'flag_only' and take['gaps'][0]['adc_gap_frames'] > 10 * SR
    assert take['frames'] == 2 * FPB


def test_unflagged_gap_logged_not_filled(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    rig.array.recordStart(rig.dir, 'take')
    rig.feed()
    rig.feed()
    rig.skip(100)
    rig.feed()
    take = rig.array.recordStop()
    assert take['gaps'] == [{'at_frame': 2 * FPB, 'est_lost_frames': None, 'gap_frames': 100, 'method': 'unflagged_gap'}]
    assert take['frames'] == 3 * FPB
    assert any('unflagged' in e for e in rig.errs)


class _BlockingWave:
    '''wave writer wrapper: the first writeframes blocks until `gate` is set;
    `reached[n]` is set when the n-th writeframes call begins; optional failure.'''

    def __init__(self, inner, shared):
        self.inner, self.s = inner, shared

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def writeframes(self, data):
        self.s['n'] += 1
        ev = self.s['reached'].get(self.s['n'])
        if ev is not None:
            ev.set()
        if self.s['n'] == 1:
            self.s['gate'].wait(10)
        if self.s.get('fail_at') == self.s['n']:
            raise OSError('disk full')
        self.inner.writeframes(data)


def _patch_wave(monkeypatch, **shared):
    shared.setdefault('n', 0)
    shared.setdefault('reached', {})
    shared.setdefault('gate', threading.Event())
    monkeypatch.setattr(micarray.wave, 'open', lambda f, mode: _BlockingWave(_real_wave_open(f, mode), shared))
    return shared


def test_queue_drop_is_exact_and_zeros_land_in_place(fake, tmp_path, monkeypatch):
    shared = _patch_wave(monkeypatch)
    shared['reached'] = {1: threading.Event(), 5: threading.Event()}
    rig = Rig(fake, tmp_path, max_queue_buffers=2)
    rig.array.start()
    rig.array.recordStart(rig.dir, 'take', labels=['B1', 'B2'])
    bufs = [rig.feed()]
    assert shared['reached'][1].wait(5)          # writer holds buf0 inside writeframes
    bufs += [rig.feed(), rig.feed()]             # fill the queue
    rig.feed()                                   # dropped (queue full)
    rig.feed()                                   # dropped, coalesced with the previous drop
    shared['gate'].set()
    assert shared['reached'][5].wait(5)          # writer reached buf2 -> queue empty
    bufs.append(rig.feed())                      # pending zeros flush first, then this buffer
    take = rig.array.recordStop()
    assert take['gaps'] == [{'at_frame': 3 * FPB, 'est_lost_frames': 2 * FPB, 'method': 'queue_drop'}]
    assert sum('queue full' in e for e in rig.errs) == 1
    for label, jack in (('B1', 1), ('B2', 3)):
        params, s = read_wav(take['files'][label])
        assert params[3] == 6 * FPB
        np.testing.assert_array_equal(s[:3 * FPB], np.concatenate([b[:, jack - 1] for b in bufs[:3]]))
        assert not s[3 * FPB:5 * FPB].any()
        np.testing.assert_array_equal(s[5 * FPB:], bufs[3][:, jack - 1])


def test_queue_drop_pending_at_take_end_becomes_tail_zeros(fake, tmp_path, monkeypatch):
    shared = _patch_wave(monkeypatch)
    shared['reached'] = {1: threading.Event()}
    rig = Rig(fake, tmp_path, max_queue_buffers=1)
    rig.array.start()
    rig.array.recordStart(rig.dir, 'take', labels=['B1'])
    rig.feed()
    assert shared['reached'][1].wait(5)
    rig.feed()           # queued
    rig.feed()           # dropped; zeros still pending when the take ends
    res = []
    t = threading.Thread(target=lambda: res.append(rig.array.recordStop()), daemon=True)
    t.start()
    shared['gate'].set()
    t.join(5)
    take = res[0]
    assert take['gaps'] == [{'at_frame': 2 * FPB, 'est_lost_frames': FPB, 'method': 'queue_drop'}]
    params, s = read_wav(take['files']['B1'])
    assert params[3] == 3 * FPB and not s[2 * FPB:].any()


@pytest.mark.parametrize('mode', ['plain', 'recordStop', 'stop'])
def test_writer_error_ends_take_reported_by_finalizer_not_writer(fake, tmp_path, monkeypatch, mode):
    shared = _patch_wave(monkeypatch, fail_at=1)
    shared['gate'].set()
    threads = []
    holder = {}

    def exc(msg):
        threads.append(threading.current_thread())
        if mode == 'recordStop':
            holder['rs'] = holder['rig'].array.recordStop()
        elif mode == 'stop':
            holder['rig'].array.stop()
        holder['seen'].set()

    holder['seen'] = threading.Event()
    rig = Rig(fake, tmp_path, excFunc=exc)
    holder['rig'] = rig
    rig.array.start()
    done = threading.Event()
    got = []
    rig.array.recordStart(rig.dir, 'take', labels=['B1', 'B2'], postFunc=lambda take: (got.append(take), done.set()))
    sess = rig.array._active
    rig.feed()
    for _ in range(5000):      # ~5 s deadline: a regression fails instead of hanging pytest
        if sess.writer_error is not None:
            break
        sess.writer_thread.join(0.001)
    assert sess.writer_error is not None
    rig.feed()          # callback sees writer_error and ends the take at this boundary
    assert done.wait(5) and holder['seen'].wait(5)
    take = got[0]
    assert 'disk full' in take['error'] and manifest(take)['error'] == take['error']
    assert sess.writer_thread not in threads and len(threads) == 1
    assert rig.array.isRecording is False
    if mode == 'recordStop':
        assert holder['rs'] is None
    if mode == 'stop':
        assert rig.array.micOn is False


def test_lifecycle_rejections(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    assert rig.array.recordStart(rig.dir, 'take') is False            # not started
    assert rig.array.start() is True and rig.array.start() is False   # double start
    assert len(fake.opened) == 1 and any('already started' in e for e in rig.errs)
    assert rig.array.recordStart(rig.dir, 'take', labels=['nope']) is False
    assert rig.array.recordStop() is None
    n_errs = len(rig.errs)
    assert rig.array.recordStop() is None and len(rig.errs) == n_errs   # silent
    assert rig.array.recordStart(rig.dir, 'take') is True
    assert rig.array.recordStart(rig.dir, 'other') is False           # already recording; not replaced
    rig.feed()
    take = rig.array.recordStop()
    assert take['frames'] == FPB
    assert rig.array.recordStart(rig.dir, 'take') is False            # never overwrite existing take files
    assert read_wav(take['files']['B1'])[0][3] == FPB


def test_stop_while_recording_finalizes_and_second_take_is_fresh(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    got = []
    rig.array.recordStart(rig.dir, 'one', postFunc=lambda take: got.append(take))
    rig.feed()
    rig.feed()
    rig.array.stop()
    assert fake.stream.closed and rig.array.micOn is False and rig.array.isRecording is False
    assert got[0]['frames'] == 2 * FPB and rig.array.lastTake is got[0] and got[0]['error'] is None
    assert rig.array.start() is True
    assert rig.array.recordStart(rig.dir, 'two') is True
    rig.feed()
    take = rig.array.recordStop()
    assert take['frames'] == FPB and manifest(take)['startFrameIndex'] == 0
    assert set(take['files'].values()).isdisjoint(got[0]['files'].values())


def test_subscribers_isolation_subset_and_reentrancy(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    seen = []

    def bad(data, labels):
        raise RuntimeError('boom')

    def subset(data, labels):
        seen.append((data.shape, labels))
        rig.array.unsubscribe(subset)      # unsubscribe from inside the callback

    stopped = []
    rig.array.subscribe(bad)
    rig.array.subscribe(subset, labels=['B3', 'B1'])
    rig.array.subscribe(lambda data, labels: stopped.append(rig.array.recordStop()))
    rig.array.recordStart(rig.dir, 'take')
    run_with_timeout(lambda: rig.feed())      # recordStop() from a subscriber must not deadlock
    assert any('boom' in e for e in rig.errs)
    assert seen == [((FPB, 2), ['B3', 'B1'])]
    assert stopped[0]['frames'] == FPB and rig.array.isRecording is False
    rig.feed()
    assert len(seen) == 1                      # unsubscribed


def test_lock_and_thread_discipline(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    # (a) stop()'s postFunc may call start(): _life_lock is not held while it runs
    rig.array.start()
    run_with_timeout(lambda: rig.array.stop(postFunc=lambda: rig.array.start()))
    assert rig.array.micOn is True and len(fake.opened) == 2
    # (b) a take postFunc may call stop(): finalize_lock is not held while it runs
    rig.array.recordStart(rig.dir, 'take', postFunc=lambda take: rig.array.stop())
    rig.feed()
    take = run_with_timeout(lambda: rig.array.recordStop())
    assert take['frames'] == FPB and rig.array.micOn is False
    # (c) start()/stop() on the audio callback thread are refused; the stream keeps running
    rig.array.start()
    results = []
    rig.array.subscribe(lambda data, labels: results.extend([rig.array.stop(), rig.array.start()]))
    stream = fake.stream
    rig.feed()
    assert results == [None, False] and stream.closed is False and rig.array.micOn is True
    assert sum('callback thread' in e for e in rig.errs) == 2


def test_empty_take_is_valid(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    got = []
    rig.array.recordStart(rig.dir, 'e1', postFunc=lambda take: got.append(take))
    take = rig.array.recordStop()          # no callback ran in between
    assert take['frames'] == 0 and take['error'] is None and len(got) == 1
    assert read_wav(take['files']['B1'])[0] == (1, 4, SR, 0)
    m = manifest(take)
    assert m['totalFrames'] == 0 and m['startFrameIndex'] is None and m['startedAtWall'] is None
    assert m['measuredFramesPerWallSecond'] is None and m['gaps'] == []
    rig.array.recordStart(rig.dir, 'e2')
    rig.array.stop()                       # same via stop()
    assert rig.array.lastTake['frames'] == 0 and rig.array.lastTake['error'] is None


def test_get_input_device_info(monkeypatch):
    class Dev(_FakeAudio):
        DEVICES = [('vdownmix', 8), ('Out only', 0), ('My UMC1820 hw:1,0', 18), ('UMC again hw:2,0', 18)]

        def get_device_info_by_index(self, i):
            d = super().get_device_info_by_index(i)
            d['index'] = i + 10        # distinct global index: must be what is returned
            return d

        def get_default_input_device_info(self):
            return self.get_device_info_by_index(2)

    monkeypatch.setattr(device, 'audio', Dev())
    info = device.get_input_device_info('umc')          # case-insensitive, first match, pseudo/output devices skipped
    assert info == {'deviceID': 12, 'name': 'My UMC1820 hw:1,0', 'maxInputChannels': 18, 'defaultSampleRate': 44100.0}
    assert device.get_input_device_info('vdown') is None     # ALSA plugin node filtered
    assert device.get_input_device_info('nothing') is None
    assert device.get_input_device_info()['deviceID'] == 12


def test_stop_closes_stream_even_if_stop_stream_raises(fake, tmp_path):
    rig = Rig(fake, tmp_path)
    rig.array.start()
    got = []
    rig.array.recordStart(rig.dir, 'take', postFunc=lambda take: got.append(take))
    rig.feed()
    stream = fake.stream
    stream.fail_stop = True
    rig.array.stop()
    assert stream.closed is True and rig.array.micOn is False
    assert sum('Could not stop stream' in e for e in rig.errs) == 1
    assert got[0]['frames'] == FPB and got[0]['error'] is None      # active take still finalised
    assert rig.array.start() is True and fake.stream is not stream   # restart works
