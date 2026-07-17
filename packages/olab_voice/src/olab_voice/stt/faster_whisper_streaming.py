from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from math import log10, sqrt
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import numpy as np

from olab_voice.audio.models import AudioFrame
from olab_voice.stt.base import TranscriptEvent


class FasterWhisperStreamingUnavailableError(RuntimeError):
    """Raised when Faster-Whisper or its configured local model is unavailable."""


class StreamingBackpressureError(RuntimeError):
    """Raised when a live-audio producer outruns the bounded worker queue."""


@dataclass(slots=True)
class FasterWhisperStreamingTranscriber:
    """Bounded, local Faster-Whisper transcription for ordered PCM frames."""

    model_path: str | Path
    sample_rate: int = 16000
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = "en"
    beam_size: int = 3
    target_interval_seconds: float = 4.0
    endpoint_silence_seconds: float = 0.8
    silence_threshold_db: float = -55.0
    vad_filter: bool = True
    max_queued_frames: int = 128
    _model: Any | None = field(default=None, init=False, repr=False)
    _frame_queue: asyncio.Queue[AudioFrame | None] | None = field(
        default=None, init=False, repr=False
    )
    _event_queue: asyncio.Queue[TranscriptEvent | None] | None = field(
        default=None, init=False, repr=False
    )
    _worker: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)
    _flush_on_stop: bool = field(default=True, init=False, repr=False)
    _worker_error: Exception | None = field(default=None, init=False, repr=False)
    _segment_counter: int = field(default=0, init=False, repr=False)
    _frames: list[AudioFrame] = field(default_factory=list, init=False, repr=False)
    _segment_start_time: float | None = field(default=None, init=False, repr=False)
    _segment_end_time: float | None = field(default=None, init=False, repr=False)
    _last_speech_end_time: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.model_path = Path(self.model_path).expanduser()
        if self.target_interval_seconds <= 0:
            raise ValueError("target_interval_seconds must be positive")
        if self.endpoint_silence_seconds <= 0:
            raise ValueError("endpoint_silence_seconds must be positive")
        if self.max_queued_frames < 1:
            raise ValueError("max_queued_frames must be at least 1")

    async def start(self) -> None:
        if self._started:
            return
        if not self.model_path.is_dir():
            raise FileNotFoundError(
                f"Faster-Whisper model directory does not exist: {self.model_path}"
            )

        await asyncio.to_thread(self._initialize)
        self._frame_queue = asyncio.Queue(maxsize=self.max_queued_frames)
        self._event_queue = asyncio.Queue()
        self._started = True
        self._worker = asyncio.create_task(self._run_worker())

    async def submit_frame(self, frame: AudioFrame) -> None:
        if not self._started or self._stopped:
            raise RuntimeError("Faster-Whisper streaming transcriber is not running")
        self._validate_frame(frame)
        if self._frame_queue is None:
            raise RuntimeError("Faster-Whisper streaming transcriber has not been started")
        try:
            self._frame_queue.put_nowait(frame)
        except asyncio.QueueFull as exc:
            raise StreamingBackpressureError(
                "Faster-Whisper frame queue is full; capture must slow down or drop frames"
            ) from exc

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        if self._event_queue is None:
            raise RuntimeError("Faster-Whisper streaming transcriber has not been started")
        while True:
            event = await self._event_queue.get()
            if event is None:
                if self._worker_error is not None:
                    raise RuntimeError("Faster-Whisper streaming worker failed") from self._worker_error
                return
            yield event

    async def stop(self, *, flush: bool = True) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._flush_on_stop = flush
        if self._frame_queue is not None:
            if not flush:
                self._reset_segment()
                while not self._frame_queue.empty():
                    self._frame_queue.get_nowait()
            await self._frame_queue.put(None)
        if self._worker is not None:
            await self._worker
        if self._event_queue is not None:
            await self._event_queue.put(None)
        self._model = None
        if self._worker_error is not None:
            raise RuntimeError("Faster-Whisper streaming worker failed") from self._worker_error

    def _initialize(self) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise FasterWhisperStreamingUnavailableError(
                "faster-whisper is not installed; install olab-voice[stt-faster-whisper]"
            ) from exc
        try:
            self._model = WhisperModel(
                str(self.model_path),
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=True,
            )
        except Exception as exc:
            raise FasterWhisperStreamingUnavailableError(
                f"failed to initialize Faster-Whisper model at {self.model_path}: {exc}"
            ) from exc

    async def _run_worker(self) -> None:
        try:
            if self._frame_queue is None:
                raise RuntimeError("Faster-Whisper frame queue was not initialized")
            while (frame := await self._frame_queue.get()) is not None:
                await self._consume_frame(frame)
            if self._flush_on_stop and self._frames:
                await self._emit_segment("transcript.segment_final")
        except Exception as exc:
            self._worker_error = exc

    async def _consume_frame(self, frame: AudioFrame) -> None:
        frame_end_time = frame.timestamp + len(frame.data) / (2 * frame.sample_rate)
        if not self._frames:
            self._segment_start_time = frame.timestamp
        self._frames.append(frame)
        self._segment_end_time = frame_end_time

        if self._is_speech(frame):
            self._last_speech_end_time = frame_end_time

        if self._last_speech_end_time is not None:
            silence_seconds = frame_end_time - self._last_speech_end_time
            if silence_seconds >= self.endpoint_silence_seconds:
                await self._emit_segment("transcript.segment_final")
                return

        if self._segment_duration() >= self.target_interval_seconds:
            await self._emit_segment("transcript.interval_final")

    async def _emit_segment(
        self, event_type: Literal["transcript.segment_final", "transcript.interval_final"]
    ) -> None:
        if not self._frames:
            return
        frames = self._frames
        segment_start_time = self._segment_start_time
        segment_end_time = self._segment_end_time
        has_speech = self._last_speech_end_time is not None
        self._reset_segment()

        if not has_speech:
            return
        pcm = b"".join(frame.data for frame in frames)
        text, confidence = await asyncio.to_thread(self._transcribe, pcm)
        if not text:
            return
        self._segment_counter += 1
        event = TranscriptEvent(
            text=text,
            type=event_type,
            segment_id=f"{frames[0].session_id}:{self._segment_counter}",
            revision=1,
            engine="faster_whisper",
            confidence=confidence,
            capture_start_time=segment_start_time,
            capture_end_time=segment_end_time,
        )
        if self._event_queue is None:
            raise RuntimeError("Faster-Whisper event queue was not initialized")
        await self._event_queue.put(event)

    def _transcribe(self, pcm: bytes) -> tuple[str, float | None]:
        if self._model is None:
            raise RuntimeError("Faster-Whisper streaming transcriber has not been initialized")
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            samples,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
        )
        collected = list(segments)
        text = " ".join(segment.text.strip() for segment in collected if segment.text.strip()).strip()
        probabilities = [segment.avg_logprob for segment in collected if segment.avg_logprob is not None]
        confidence = sum(probabilities) / len(probabilities) if probabilities else None
        return text, confidence

    def _is_speech(self, frame: AudioFrame) -> bool:
        samples = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        if not len(samples):
            return False
        rms = sqrt(float(np.mean(samples**2)))
        db = 20 * log10(rms + 1e-10)
        return db >= self.silence_threshold_db

    def _segment_duration(self) -> float:
        if self._segment_start_time is None or self._segment_end_time is None:
            return 0.0
        return self._segment_end_time - self._segment_start_time

    def _reset_segment(self) -> None:
        self._frames = []
        self._segment_start_time = None
        self._segment_end_time = None
        self._last_speech_end_time = None

    def _validate_frame(self, frame: AudioFrame) -> None:
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
