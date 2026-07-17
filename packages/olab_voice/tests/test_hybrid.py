from __future__ import annotations

import asyncio

from olab_voice.audio.models import AudioFrame
from olab_voice.stt.base import TranscriptEvent
from olab_voice.stt.hybrid import HybridStreamingTranscriber


class _FakeStreamingTranscriber:
    def __init__(self, events, delay=0.0):
        self._events = events
        self._delay = delay
        self.frames = []

    async def start(self):
        return None

    async def submit_frame(self, frame):
        self.frames.append(frame)

    async def events(self):
        for event in self._events:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield event

    async def stop(self, *, flush=True):
        return None


def test_hybrid_replaces_vosk_final_with_whisper_revision():
    async def run():
        vosk = _FakeStreamingTranscriber(
            [
                TranscriptEvent(
                    text="provisional", type="transcript.hypothesis", segment_id="v1",
                    capture_start_time=1.0, capture_end_time=2.0,
                ),
                TranscriptEvent(
                    text="vosk final", segment_id="v1", revision=1,
                    capture_start_time=1.0, capture_end_time=2.0,
                ),
            ]
        )
        whisper = _FakeStreamingTranscriber(
            [
                TranscriptEvent(
                    text="whisper correction", type="transcript.interval_final", segment_id="w1",
                    capture_start_time=1.0, capture_end_time=2.0,
                )
            ],
            delay=0.001,
        )
        hybrid = HybridStreamingTranscriber(vosk, whisper)
        await hybrid.start()
        await hybrid.submit_frame(AudioFrame(data=b"\x00\x00", session_id="service"))
        await asyncio.sleep(0.01)
        stream = hybrid.events()
        events = [await anext(stream) for _ in range(3)]
        await hybrid.stop()
        return vosk, whisper, events

    vosk, whisper, events = asyncio.run(run())

    assert len(vosk.frames) == len(whisper.frames) == 1
    assert [event.text for event in events] == ["provisional", "vosk final", "whisper correction"]
    assert [event.revision for event in events] == [0, 1, 2]
    assert events[0].segment_id == events[1].segment_id == events[2].segment_id
    assert events[1].is_fallback is True
    assert events[2].metadata["replaces_engine"] == "vosk"


def test_hybrid_holds_early_whisper_intervals_until_vosk_segment_exists():
    async def run():
        vosk = _FakeStreamingTranscriber([
            TranscriptEvent(
                text="vosk final", segment_id="v1", revision=1,
                capture_start_time=1.0, capture_end_time=5.0,
            ),
        ], delay=0.001)
        whisper = _FakeStreamingTranscriber([
            TranscriptEvent(
                text="first correction", type="transcript.interval_final", segment_id="w1",
                capture_start_time=1.0, capture_end_time=3.0,
            ),
            TranscriptEvent(
                text="second correction", type="transcript.interval_final", segment_id="w2",
                capture_start_time=3.0, capture_end_time=5.0,
            ),
        ])
        hybrid = HybridStreamingTranscriber(vosk, whisper)
        await hybrid.start()
        await hybrid.submit_frame(AudioFrame(data=b"\x00\x00", session_id="service"))
        await asyncio.sleep(0.01)
        stream = hybrid.events()
        events = [await anext(stream) for _ in range(2)]
        await hybrid.stop()
        return events

    events = asyncio.run(run())

    assert [event.text for event in events] == ["vosk final", "first correction second correction"]
    assert [event.revision for event in events] == [1, 3]
    assert events[0].segment_id == events[1].segment_id
