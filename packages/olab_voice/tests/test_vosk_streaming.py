from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from olab_voice.audio.models import AudioFrame
from olab_voice.stt.vosk import VoskStreamingTranscriber


class _FakeRecognizer:
    def __init__(self, _model, sample_rate):
        self.sample_rate = sample_rate
        self.calls = 0
        self.words_enabled = False

    def SetWords(self, enabled):
        self.words_enabled = enabled

    def AcceptWaveform(self, _data):
        self.calls += 1
        return self.calls == 2

    def PartialResult(self):
        return json.dumps({"partial": "hello"})

    def Result(self):
        return json.dumps({"text": "hello world"})

    def FinalResult(self):
        return json.dumps({"text": ""})


def _install_fake_vosk(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "vosk",
        SimpleNamespace(Model=lambda path: {"path": path}, KaldiRecognizer=_FakeRecognizer),
    )


def test_vosk_streaming_emits_hypothesis_and_final(monkeypatch, tmp_path):
    _install_fake_vosk(monkeypatch)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = VoskStreamingTranscriber(model_path)
        await transcriber.start()
        event_stream = transcriber.events()

        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, seq=1, session_id="service", timestamp=10.0)
        )
        hypothesis = await anext(event_stream)

        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, seq=2, session_id="service", timestamp=10.01)
        )
        final = await anext(event_stream)
        await transcriber.stop()

        return hypothesis, final

    hypothesis, final = asyncio.run(run())

    assert hypothesis.text == "hello"
    assert hypothesis.type == "transcript.hypothesis"
    assert hypothesis.segment_id == "service:1"
    assert hypothesis.revision == 0
    assert final.text == "hello world"
    assert final.type == "transcript.segment_final"
    assert final.segment_id == hypothesis.segment_id
    assert final.revision == 1
    assert final.capture_start_time == 10.0
    assert final.capture_end_time == pytest.approx(10.02)


def test_vosk_streaming_validates_audio_format(monkeypatch, tmp_path):
    _install_fake_vosk(monkeypatch)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = VoskStreamingTranscriber(model_path)
        await transcriber.start()
        with pytest.raises(ValueError, match="pcm_s16le"):
            await transcriber.submit_frame(AudioFrame(data=b"abc", format="audio/wav"))
        await transcriber.stop(flush=False)

    asyncio.run(run())
