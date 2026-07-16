from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, AsyncIterator

from olab_voice.audio.models import AudioFrame
from olab_voice.stt.base import TranscriptEvent


class VoskUnavailableError(RuntimeError):
    """Raised when Vosk is unavailable or its configured model cannot load."""


@dataclass(slots=True)
class VoskStreamingTranscriber:
    """Streaming Vosk recognizer for ordered signed-16-bit PCM audio frames."""

    model_path: str | Path
    sample_rate: int = 16000
    emit_words: bool = True
    _model: Any | None = field(default=None, init=False, repr=False)
    _recognizer: Any | None = field(default=None, init=False, repr=False)
    _event_queue: asyncio.Queue[TranscriptEvent | None] | None = field(
        default=None, init=False, repr=False
    )
    _started: bool = field(default=False, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)
    _segment_counter: int = field(default=0, init=False, repr=False)
    _segment_id: str | None = field(default=None, init=False, repr=False)
    _segment_start_time: float | None = field(default=None, init=False, repr=False)
    _segment_end_time: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.model_path = Path(self.model_path).expanduser()

    async def start(self) -> None:
        if self._started:
            return
        if not self.model_path.is_dir():
            raise FileNotFoundError(f"Vosk model directory does not exist: {self.model_path}")

        self._event_queue = asyncio.Queue()
        await asyncio.to_thread(self._initialize)
        self._started = True

    async def submit_frame(self, frame: AudioFrame) -> None:
        if not self._started or self._stopped:
            raise RuntimeError("Vosk streaming transcriber is not running")
        if frame.format != "pcm_s16le":
            raise ValueError(f"expected pcm_s16le audio frames, got {frame.format!r}")
        if frame.sample_rate != self.sample_rate:
            raise ValueError(
                f"expected {self.sample_rate} Hz audio frames, got {frame.sample_rate} Hz"
            )
        if frame.channels != 1:
            raise ValueError(f"expected mono audio frames, got {frame.channels} channels")
        if len(frame.data) % 2:
            raise ValueError("pcm_s16le frame data length must be divisible by 2")

        event = await asyncio.to_thread(self._process_frame, frame)
        if event is not None:
            await self._queue_event(event)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        if self._event_queue is None:
            raise RuntimeError("Vosk streaming transcriber has not been started")

        while True:
            event = await self._event_queue.get()
            if event is None:
                return
            yield event

    async def stop(self, *, flush: bool = True) -> None:
        if self._stopped:
            return
        self._stopped = True

        if flush and self._started:
            event = await asyncio.to_thread(self._flush)
            if event is not None:
                await self._queue_event(event)

        if self._event_queue is not None:
            await self._event_queue.put(None)
        self._recognizer = None
        self._model = None

    def _initialize(self) -> None:
        try:
            from vosk import KaldiRecognizer, Model
        except ImportError as exc:
            raise VoskUnavailableError(
                "vosk is not installed; install olab-voice[stt-vosk]"
            ) from exc

        try:
            self._model = Model(str(self.model_path))
            self._recognizer = KaldiRecognizer(self._model, self.sample_rate)
            if self.emit_words:
                self._recognizer.SetWords(True)
        except Exception as exc:
            raise VoskUnavailableError(
                f"failed to initialize Vosk model at {self.model_path}: {exc}"
            ) from exc

    def _process_frame(self, frame: AudioFrame) -> TranscriptEvent | None:
        if self._recognizer is None:
            raise RuntimeError("Vosk streaming transcriber has not been initialized")

        frame_end_time = frame.timestamp + len(frame.data) / (2 * frame.sample_rate)
        if self._segment_id is None:
            self._start_segment(frame, frame_end_time)
        else:
            self._segment_end_time = frame_end_time

        if self._recognizer.AcceptWaveform(frame.data):
            result = json.loads(self._recognizer.Result())
            event = self._event_from_result(
                result.get("text", ""),
                event_type="transcript.segment_final",
                revision=1,
            )
            self._reset_segment()
            return event

        result = json.loads(self._recognizer.PartialResult())
        return self._event_from_result(
            result.get("partial", ""),
            event_type="transcript.hypothesis",
            revision=0,
        )

    def _flush(self) -> TranscriptEvent | None:
        if self._recognizer is None or self._segment_id is None:
            return None
        result = json.loads(self._recognizer.FinalResult())
        event = self._event_from_result(
            result.get("text", ""),
            event_type="transcript.segment_final",
            revision=1,
        )
        self._reset_segment()
        return event

    def _start_segment(self, frame: AudioFrame, frame_end_time: float) -> None:
        self._segment_counter += 1
        self._segment_id = f"{frame.session_id}:{self._segment_counter}"
        self._segment_start_time = frame.timestamp
        self._segment_end_time = frame_end_time

    def _event_from_result(
        self,
        text: str,
        *,
        event_type: str,
        revision: int,
    ) -> TranscriptEvent | None:
        if not text.strip() or self._segment_id is None:
            return None
        return TranscriptEvent(
            text=text.strip(),
            type=event_type,  # type: ignore[arg-type]
            segment_id=self._segment_id,
            revision=revision,
            engine="vosk",
            capture_start_time=self._segment_start_time,
            capture_end_time=self._segment_end_time,
        )

    async def _queue_event(self, event: TranscriptEvent) -> None:
        if self._event_queue is None:
            raise RuntimeError("Vosk streaming transcriber has not been started")
        await self._event_queue.put(event)

    def _reset_segment(self) -> None:
        self._segment_id = None
        self._segment_start_time = None
        self._segment_end_time = None
