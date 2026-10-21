import numpy as np
import pytest

from olab_audio.speaker import Speaker


class _FakeStream:
    def __init__(self, fail_stop=False, fail_write=False):
        self.fail_stop = fail_stop
        self.fail_write = fail_write
        self.stopped = False
        self.closed = False

    def write(self, data):
        if self.fail_write:
            raise OSError('write boom')

    def stop_stream(self):
        self.stopped = True
        if self.fail_stop:
            raise OSError('stop boom')

    def close(self):
        self.closed = True


class _FakeAudio:
    def __init__(self, stream):
        self.stream = stream

    def open(self, **kwargs):
        return self.stream


def test_speaker_stop_closes_stream_even_if_stop_stream_raises():
    spk = Speaker(deviceID=0)
    stream = spk.stream = _FakeStream(fail_stop=True)
    with pytest.raises(OSError, match='stop boom'):
        spk.stop()
    assert stream.closed is True
    assert spk.stream is None
    spk.stop()  # second stop is a no-op


def test_speaker_play_closes_stream_even_if_write_raises(monkeypatch):
    stream = _FakeStream(fail_write=True)
    monkeypatch.setattr('olab_audio.speaker.audio', _FakeAudio(stream))
    spk = Speaker(deviceID=0)
    with pytest.raises(OSError, match='write boom'):
        spk.play(np.zeros(4))
    assert stream.stopped is True and stream.closed is True
    assert spk.stream is None
