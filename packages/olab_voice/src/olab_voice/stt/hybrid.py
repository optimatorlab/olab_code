from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import AsyncIterator

from olab_voice.audio.models import AudioFrame
from olab_voice.stt.base import StreamingTranscriber, TranscriptEvent


@dataclass(slots=True)
class _Segment:
    segment_id: str
    start_time: float | None
    end_time: float | None
    revision: int = 0
    whisper_events: list[TranscriptEvent] = field(default_factory=list)


@dataclass(slots=True)
class HybridStreamingTranscriber:
    """Combines immediate Vosk captions with Faster-Whisper corrections."""

    vosk: StreamingTranscriber
    faster_whisper: StreamingTranscriber
    _incoming: asyncio.Queue[tuple[str, TranscriptEvent] | None] | None = field(
        default=None, init=False, repr=False
    )
    _events: asyncio.Queue[TranscriptEvent | None] | None = field(
        default=None, init=False, repr=False
    )
    _forwarders: list[asyncio.Task[None]] = field(default_factory=list, init=False, repr=False)
    _segments: dict[str, _Segment] = field(default_factory=dict, init=False, repr=False)
    _pending_faster_whisper: list[TranscriptEvent] = field(default_factory=list, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)

    async def start(self) -> None:
        if self._started:
            return
        await self.vosk.start()
        await self.faster_whisper.start()
        self._incoming = asyncio.Queue()
        self._events = asyncio.Queue()
        self._forwarders = [
            asyncio.create_task(self._forward("vosk", self.vosk)),
            asyncio.create_task(self._forward("faster_whisper", self.faster_whisper)),
        ]
        asyncio.create_task(self._route_events())
        self._started = True

    async def submit_frame(self, frame: AudioFrame) -> None:
        if not self._started or self._stopped:
            raise RuntimeError("Hybrid streaming transcriber is not running")
        await self.vosk.submit_frame(frame)
        await self.faster_whisper.submit_frame(frame)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        if self._events is None:
            raise RuntimeError("Hybrid streaming transcriber has not been started")
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def stop(self, *, flush: bool = True) -> None:
        if self._stopped:
            return
        self._stopped = True
        await self.vosk.stop(flush=flush)
        await self.faster_whisper.stop(flush=flush)
        if self._forwarders:
            await asyncio.gather(*self._forwarders)
        if self._incoming is not None:
            await self._incoming.put(None)

    async def _forward(self, engine: str, transcriber: StreamingTranscriber) -> None:
        if self._incoming is None:
            raise RuntimeError("Hybrid incoming queue was not initialized")
        async for event in transcriber.events():
            await self._incoming.put((engine, event))

    async def _route_events(self) -> None:
        if self._incoming is None or self._events is None:
            raise RuntimeError("Hybrid queues were not initialized")
        while (item := await self._incoming.get()) is not None:
            engine, event = item
            if engine == "vosk":
                await self._events.put(self._route_vosk(event))
                for correction in self._flush_pending_faster_whisper():
                    await self._events.put(correction)
            else:
                correction = self._route_faster_whisper(event)
                if correction is not None:
                    await self._events.put(correction)
        await self._events.put(None)

    def _route_vosk(self, event: TranscriptEvent) -> TranscriptEvent:
        source_id = event.segment_id or f"vosk:{len(self._segments) + 1}"
        segment = self._segments.get(source_id)
        if segment is None:
            segment = _Segment(
                segment_id=f"hybrid:{source_id}",
                start_time=event.capture_start_time,
                end_time=event.capture_end_time,
            )
            self._segments[source_id] = segment
        segment.end_time = event.capture_end_time or segment.end_time
        segment.revision = max(segment.revision, event.revision)
        return replace(
            event,
            segment_id=segment.segment_id,
            revision=event.revision,
            engine="vosk",
            is_fallback=event.type != "transcript.hypothesis",
        )

    def _route_faster_whisper(self, event: TranscriptEvent) -> TranscriptEvent | None:
        segment = self._best_overlap(event.capture_start_time, event.capture_end_time)
        if segment is None:
            # Whisper can finish an interval before Vosk has emitted its first
            # non-empty partial. Hold it so a later Vosk segment can claim it.
            self._pending_faster_whisper.append(event)
            return None
        return self._apply_faster_whisper(segment, event)

    def _flush_pending_faster_whisper(self) -> list[TranscriptEvent]:
        corrections: list[TranscriptEvent] = []
        remaining: list[TranscriptEvent] = []
        for event in self._pending_faster_whisper:
            segment = self._best_overlap(event.capture_start_time, event.capture_end_time)
            if segment is None:
                remaining.append(event)
            else:
                correction = self._apply_faster_whisper(segment, event)
                if corrections and corrections[-1].segment_id == correction.segment_id:
                    corrections[-1] = correction
                else:
                    corrections.append(correction)
        self._pending_faster_whisper = remaining
        return corrections

    @staticmethod
    def _apply_faster_whisper(segment: _Segment, event: TranscriptEvent) -> TranscriptEvent:
        segment.whisper_events.append(event)
        revision = max(segment.revision + 1, 2)
        segment.revision = revision
        return replace(
            event,
            text=" ".join(item.text.strip() for item in segment.whisper_events if item.text.strip()),
            segment_id=segment.segment_id,
            revision=revision,
            engine="faster_whisper",
            is_fallback=False,
            metadata={**event.metadata, "replaces_engine": "vosk"},
        )

    def _best_overlap(self, start_time: float | None, end_time: float | None) -> _Segment | None:
        if start_time is None or end_time is None:
            return None
        best: tuple[float, _Segment] | None = None
        for segment in self._segments.values():
            if segment.start_time is None or segment.end_time is None:
                continue
            overlap = max(0.0, min(end_time, segment.end_time) - max(start_time, segment.start_time))
            if best is None or overlap > best[0]:
                best = (overlap, segment)
        return best[1] if best and best[0] > 0 else None
