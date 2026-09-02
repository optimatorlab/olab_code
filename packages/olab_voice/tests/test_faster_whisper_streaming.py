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


class _RecordingModel:
    """Fake model that echoes each call's initial_prompt into the transcript."""

    def __init__(self):
        self.prompts: list[str | None] = []

    def transcribe(self, samples, **kwargs):
        assert len(samples)
        prompt = kwargs.get("initial_prompt")
        self.prompts.append(prompt)
        text = f"[{prompt}] said the word" if prompt else "said the word"
        segment = SimpleNamespace(text=text, avg_logprob=-0.1)
        return iter([segment]), SimpleNamespace()


def _install_fake_faster_whisper(monkeypatch, model=None):
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: model or _FakeModel()),
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


def test_interval_final_carries_prior_text_as_initial_prompt(monkeypatch, tmp_path):
    """A mid-utterance interval split should give Faster-Whisper the tail of the
    previous chunk as context, since that's what prevents it from re-decoding
    (and stuttering) the first word of the new chunk in isolation."""
    model = _RecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=0.005,
            endpoint_silence_seconds=100.0,
        )
        await transcriber.start()
        stream = transcriber.events()
        for _ in range(2):
            await transcriber.submit_frame(
                AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
            )
        events = [await anext(stream), await anext(stream)]
        await transcriber.stop(flush=False)
        return events

    events = asyncio.run(run())

    assert model.prompts[0] is None
    assert model.prompts[1] == "said the word"
    assert events[0].text == "said the word"
    assert events[0].type == "transcript.interval_final"
    assert events[1].text == "[said the word] said the word"
    assert events[1].type == "transcript.interval_final"


def test_segment_final_does_not_carry_context_across_a_pause(monkeypatch, tmp_path):
    """A real pause (endpoint silence) ends the utterance, so the next segment
    should not be biased by the previous sentence's trailing words."""
    model = _RecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=100.0,
            endpoint_silence_seconds=0.005,
        )
        await transcriber.start()
        stream = transcriber.events()
        # First "utterance": speech followed by enough silence to trigger
        # transcript.segment_final via endpoint_silence_seconds.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=10.01)
        )
        first = await anext(stream)
        # Second, unrelated utterance.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=20.0)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=20.01)
        )
        second = await anext(stream)
        await transcriber.stop(flush=False)
        return first, second

    first, second = asyncio.run(run())

    assert first.type == "transcript.segment_final"
    assert second.type == "transcript.segment_final"
    assert model.prompts == [None, None]
    assert second.text == "said the word"
