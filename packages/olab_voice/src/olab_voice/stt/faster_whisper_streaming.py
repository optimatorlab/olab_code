from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from math import isfinite, log10, sqrt
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import numpy as np

from olab_voice.audio.models import AudioFrame
from olab_voice.stt.base import TranscriptEvent


class FasterWhisperStreamingUnavailableError(RuntimeError):
    """Raised when Faster-Whisper or its configured local model is unavailable."""


class StreamingBackpressureError(RuntimeError):
    """Raised when a live-audio producer outruns the bounded worker queue."""


# Full trailing run of these characters (including the Unicode ellipsis, which
# Faster-Whisper emits as a single codepoint rather than three periods) is
# stripped from every internal segment except the last when joining one
# _transcribe() call's segments -- see _transcribe() below.
_INTERNAL_SEGMENT_TRAILING_PUNCTUATION = ".!?…"


def _leading_token_is_guarded(text: str) -> bool:
    """True if decapitalizing ``text``'s leading letter would damage it.

    Guards two token classes that a blanket decapitalize-the-leading-letter
    rule would otherwise corrupt: the first-person pronoun ("I", "I'll",
    "I'm", ...) and multi-character acronyms ("AIS,", "GMRS", ...).
    ``str.isupper()`` ignores uncased characters such as a trailing comma,
    so no extra punctuation-stripping is needed for the acronym check.
    """
    token = text.split(" ", 1)[0]
    if token == "I" or token.startswith("I'"):
        return True
    return len(token) > 1 and token.isupper()


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
    initial_prompt_context_chars: int = 200
    context_reset_silence_multiplier: float = 4.0
    """Scale factor on ``endpoint_silence_seconds`` (not an absolute number of
    seconds): the carried ``initial_prompt`` is discarded once the real gap
    since speech was last heard exceeds ``endpoint_silence_seconds *
    context_reset_silence_multiplier``. A value below ``1.0`` makes that
    threshold shorter than ``endpoint_silence_seconds`` itself, so every
    ``transcript.segment_final`` resets context — recovering the previous
    (pre-#63-fix) never-carry-past-segment_final behavior as an explicit
    opt-in escape hatch for a consumer who disagrees with the "always carry"
    default."""
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
    _segment_first_speech_time: float | None = field(default=None, init=False, repr=False)
    _last_speech_heard_time: float | None = field(default=None, init=False, repr=False)
    """Cross-segment bookkeeping: the timestamp speech was last heard,
    updated only in ``_emit_segment`` and deliberately **not** cleared by
    ``_reset_segment()`` — it has to survive segment boundaries to measure
    the real elapsed gap for the long-silence context reset. Do not fold
    this into ``_reset_segment()``'s per-segment cleanup."""
    _next_initial_prompt: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.model_path = Path(self.model_path).expanduser()
        if not (self.target_interval_seconds > 0 and isfinite(self.target_interval_seconds)):
            raise ValueError("target_interval_seconds must be a positive, finite number")
        if not (
            self.endpoint_silence_seconds > 0 and isfinite(self.endpoint_silence_seconds)
        ):
            raise ValueError("endpoint_silence_seconds must be a positive, finite number")
        if not (
            self.context_reset_silence_multiplier > 0
            and isfinite(self.context_reset_silence_multiplier)
        ):
            raise ValueError("context_reset_silence_multiplier must be a positive, finite number")
        if self.max_queued_frames < 1:
            raise ValueError("max_queued_frames must be at least 1")
        if self.initial_prompt_context_chars < 1:
            raise ValueError("initial_prompt_context_chars must be at least 1")

    def update_tuning(
        self,
        *,
        target_interval_seconds: float | None = None,
        endpoint_silence_seconds: float | None = None,
        context_reset_silence_multiplier: float | None = None,
    ) -> None:
        """Live-update chunking-tuning knobs without reconstructing the
        transcriber. Any argument left as ``None`` is unchanged. Validates
        every provided value before assigning any of them, so a bad value
        raises ``ValueError`` and leaves all existing tuning fields
        unmodified (no partial application). Not thread-safe beyond the
        normal single-asyncio-loop assumptions the rest of this class makes;
        ``_consume_frame`` reads these fields fresh every frame, so an
        update takes effect on the very next frame."""
        if target_interval_seconds is not None and not (
            target_interval_seconds > 0 and isfinite(target_interval_seconds)
        ):
            raise ValueError("target_interval_seconds must be a positive, finite number")
        if endpoint_silence_seconds is not None and not (
            endpoint_silence_seconds > 0 and isfinite(endpoint_silence_seconds)
        ):
            raise ValueError("endpoint_silence_seconds must be a positive, finite number")
        if context_reset_silence_multiplier is not None and not (
            context_reset_silence_multiplier > 0 and isfinite(context_reset_silence_multiplier)
        ):
            raise ValueError("context_reset_silence_multiplier must be a positive, finite number")

        if target_interval_seconds is not None:
            self.target_interval_seconds = target_interval_seconds
        if endpoint_silence_seconds is not None:
            self.endpoint_silence_seconds = endpoint_silence_seconds
        if context_reset_silence_multiplier is not None:
            self.context_reset_silence_multiplier = context_reset_silence_multiplier

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
            if self._last_speech_end_time is None:
                self._segment_first_speech_time = frame.timestamp
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
        segment_first_speech_time = self._segment_first_speech_time
        segment_last_speech_time = self._last_speech_end_time
        self._reset_segment()

        if not has_speech:
            return

        # Cross-segment context continuity (olab_code#63): by default we
        # always carry the previous segment's trailing text forward as
        # `initial_prompt`, regardless of whether that segment ended via
        # transcript.interval_final or transcript.segment_final — a real
        # pause is usually just a natural breath at typical
        # endpoint_silence_seconds, and re-decoding the next chunk with no
        # context is what causes the stuttered/duplicated first word. Only a
        # genuinely long silence (relative to endpoint_silence_seconds)
        # discards the carried context, since at that point it's more likely
        # to be a stale, unrelated topic than useful continuity.
        initial_prompt = self._next_initial_prompt
        previous_speech_heard_time = self._last_speech_heard_time
        if previous_speech_heard_time is not None:
            gap = segment_first_speech_time - previous_speech_heard_time
            reset_threshold = self.endpoint_silence_seconds * self.context_reset_silence_multiplier
            if gap > reset_threshold:
                initial_prompt = None
                # Discard the stored context outright, not just for this one
                # call: if this segment's own transcription comes back empty
                # (a cough/click right after the long pause), leaving
                # `_next_initial_prompt` in place would let the *next*
                # segment inherit stale, pre-gap context.
                self._next_initial_prompt = None

        pcm = b"".join(frame.data for frame in frames)
        text, confidence = await asyncio.to_thread(self._transcribe, pcm, initial_prompt)

        # Roll `_last_speech_heard_time` forward on every segment that had
        # speech, whether or not it transcribed to non-empty text, so the
        # next segment's gap is always measured from the true most-recent
        # speech rather than going stale across an empty-text segment.
        self._last_speech_heard_time = segment_last_speech_time

        if not text:
            return
        # An empty transcription must not clear the carried context — only a
        # non-empty result replaces it with its own trailing text. This
        # keeps continuity intact across a breath/click/cough that clears
        # the dB gate but produces no words.
        self._next_initial_prompt = text[-self.initial_prompt_context_chars :]
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

    def _transcribe(self, pcm: bytes, initial_prompt: str | None) -> tuple[str, float | None]:
        if self._model is None:
            raise RuntimeError("Faster-Whisper streaming transcriber has not been initialized")
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            samples,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            initial_prompt=initial_prompt,
        )
        collected = list(segments)
        # By the time _transcribe() is called, the caller (_emit_segment) has
        # already decided this whole buffer is one continuous utterance --
        # via endpoint_silence_seconds or the target_interval_seconds
        # backstop. So any re-segmentation Faster-Whisper's own internal VAD
        # does *within* this one call is never a real sentence boundary from
        # the caller's perspective, and the per-internal-segment
        # capitalization/terminal punctuation the decoder assigns at each
        # split is spurious. Strip it out before joining -- but never for the
        # last segment (the real end of this call's utterance, whose own
        # trailing punctuation/capitalization is legitimate).
        raw_texts = [segment.text.strip() for segment in collected if segment.text.strip()]
        last_index = len(raw_texts) - 1
        pieces: list[str] = []
        for index, raw_text in enumerate(raw_texts):
            piece = raw_text
            if index != last_index:
                # Full trailing run, not just one char -- a single-char strip
                # does not fully resolve real examples like "deferred
                # until..": stripping only the last char still leaves
                # "deferred until.", the same artifact class. Re-.strip()
                # afterward: rstrip() alone can leave interior whitespace
                # behind (e.g. "hello . . ." -> "hello . .").
                piece = piece.rstrip(_INTERNAL_SEGMENT_TRAILING_PUNCTUATION).strip()
                if not piece:
                    # A segment that was only punctuation (Faster-Whisper
                    # emits bare "..." segments for near-silence under
                    # vad_filter=True) contributes nothing -- drop it rather
                    # than leaving a stray double space in the join.
                    continue
            # Decapitalize every segment except the first surviving one (the
            # true start of this call's joined text -- not necessarily
            # raw_texts[0], if that segment was itself all punctuation and
            # got dropped above). Guarded so this doesn't damage "I"/"I'll"
            # or multi-char acronyms like "AIS," that a blanket decap would
            # otherwise corrupt.
            if pieces and piece[0].isalpha() and not _leading_token_is_guarded(piece):
                piece = piece[0].lower() + piece[1:]
            pieces.append(piece)
        text = " ".join(pieces).strip()
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
        self._segment_first_speech_time = None
        # NOTE: _last_speech_heard_time is deliberately NOT cleared here — it
        # is cross-segment bookkeeping for the long-silence context reset in
        # _emit_segment and must survive across segment boundaries.

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
