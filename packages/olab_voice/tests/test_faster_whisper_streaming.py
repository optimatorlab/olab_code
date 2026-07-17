from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from olab_voice.audio.models import AudioFrame
from olab_voice.stt.faster_whisper_streaming import (
    FasterWhisperStreamingTranscriber,
    StreamingBackpressureError,
)


class _FakeSegment:
    text = " corrected text "
    avg_logprob = -0.25


class _FakeModel:
    def transcribe(self, samples, **kwargs):
        assert len(samples)
        assert kwargs["vad_filter"] is True
        return iter([_FakeSegment()]), SimpleNamespace()


def _install_fake_faster_whisper(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: _FakeModel()),
    )


def test_faster_whisper_streaming_emits_interval_final(monkeypatch, tmp_path):
    _install_fake_faster_whisper(monkeypatch)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=0.005,
            endpoint_silence_seconds=1.0,
        )
        await transcriber.start()
        stream = transcriber.events()
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        event = await anext(stream)
        await transcriber.stop()
        return event

    event = asyncio.run(run())

    assert event.text == "corrected text"
    assert event.type == "transcript.interval_final"
    assert event.engine == "faster_whisper"
    assert event.segment_id == "service:1"
    assert event.capture_start_time == 10.0
    assert event.capture_end_time == pytest.approx(10.01)
    assert event.confidence == -0.25


def test_faster_whisper_streaming_reports_queue_backpressure(monkeypatch, tmp_path):
    _install_fake_faster_whisper(monkeypatch)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(model_path, max_queued_frames=1)
        await transcriber.start()
        await transcriber.submit_frame(AudioFrame(data=b"\x00\x00" * 160, seq=1))
        with pytest.raises(StreamingBackpressureError):
            await transcriber.submit_frame(AudioFrame(data=b"\x00\x00" * 160, seq=2))
        await transcriber.stop(flush=False)

    asyncio.run(run())
