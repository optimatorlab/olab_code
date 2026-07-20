"""Synchronous, thread-owning TTS playback service.

`TtsPlaybackService` is the public entry point for local synthesis+playback.
It owns a dedicated background thread running a private asyncio event loop
so callers never need `await` or an event loop of their own; `speak()` only
blocks long enough to validate and enqueue a request, and hands back a
`TtsJob` the caller can poll or wait on synchronously.
"""

from __future__ import annotations

import asyncio
import threading

from olab_voice.audio.base import AudioPlaybackSink
from olab_voice.playback.controller import _PlaybackController
from olab_voice.playback.models import ServiceStatus, TtsJob, TtsResult
from olab_voice.tts.base import SpeechSynthesizer, TtsRequest


DEFAULT_MAX_TEXT_LENGTH = 500
DEFAULT_MAX_QUEUE_SIZE = 8


class TtsPlaybackService:
    """Owns one `SpeechSynthesizer` and one playback sink for local speech."""

    def __init__(
        self,
        synthesizer: SpeechSynthesizer,
        sink: AudioPlaybackSink,
        *,
        max_text_length: int = DEFAULT_MAX_TEXT_LENGTH,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        enabled: bool = True,
    ) -> None:
        if max_text_length < 1:
            raise ValueError("max_text_length must be at least 1")
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be at least 1")

        self._max_text_length = max_text_length
        self._max_queue_size = max_queue_size
        self._controller = _PlaybackController(synthesizer, sink, max_queue_size=max_queue_size)

        self._enabled_lock = threading.Lock()
        self._enabled = enabled

        self._start_lock = threading.Lock()
        self._started = False
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background loop/thread. Idempotent; `speak()` calls this lazily."""

        with self._start_lock:
            if self._started or self._closed:
                return
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop, args=(loop,), name="olab-voice-tts-playback", daemon=True
            )
            thread.start()

            ready = threading.Event()

            def _bind() -> None:
                self._controller.bind(loop)
                ready.set()

            loop.call_soon_threadsafe(_bind)
            ready.wait()

            self._loop = loop
            self._thread = thread
            self._started = True

    def _run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()
        loop.close()

    def set_enabled(self, enabled: bool) -> None:
        with self._enabled_lock:
            self._enabled = enabled

    @property
    def enabled(self) -> bool:
        with self._enabled_lock:
            return self._enabled

    @property
    def status(self) -> ServiceStatus:
        queue_depth = self._controller.queue_depth if self._started else 0
        active = self._controller.active if self._started else False
        return ServiceStatus(
            enabled=self.enabled,
            closed=self._closed,
            queue_depth=queue_depth,
            active=active,
        )

    def speak(self, request: TtsRequest) -> TtsJob:
        """Validate and queue `request`, returning immediately. Never blocks on synthesis/playback."""

        job = TtsJob(request)

        if self._closed:
            job._complete(TtsResult(job_id=job.id, request=request, outcome="rejected", error="service is closed"))
            return job

        if not isinstance(request.preempt, bool):
            job._complete(
                TtsResult(job_id=job.id, request=request, outcome="rejected", error="preempt must be a bool")
            )
            return job

        if not self.enabled:
            job._complete(TtsResult(job_id=job.id, request=request, outcome="disabled"))
            return job

        if not request.text.strip():
            job._complete(
                TtsResult(job_id=job.id, request=request, outcome="rejected", error="text must not be empty")
            )
            return job

        if len(request.text) > self._max_text_length:
            job._complete(
                TtsResult(
                    job_id=job.id,
                    request=request,
                    outcome="rejected",
                    error=f"text exceeds max_text_length ({self._max_text_length})",
                )
            )
            return job

        self.start()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self._controller.submit(job), self._loop)
        accepted = future.result()
        if not accepted:
            job._complete(
                TtsResult(job_id=job.id, request=request, outcome="rejected", error="playback queue is full")
            )
        return job

    def speak_and_wait(self, request: TtsRequest, timeout: float | None = None) -> TtsResult:
        """Blocking convenience wrapper around `speak()` + `job.wait()`."""

        job = self.speak(request)
        return job.wait(timeout=timeout)

    def close(self, timeout: float | None = None) -> None:
        """Cancel active/pending work and stop the background thread. Idempotent.

        Waits (up to `timeout`) for any in-flight, non-interruptible synthesis
        call to actually finish, since its worker thread is not daemonic and
        would otherwise keep the process alive after `close()` returns. If
        `timeout` elapses first, raises `TimeoutError` -- the service is still
        marked closed (no new work is accepted) and the event loop thread is
        still stopped, but the synthesis worker thread may still be running.
        """

        if self._closed:
            return
        self._closed = True

        if not self._started or self._loop is None:
            return

        future = asyncio.run_coroutine_threadsafe(self._controller.close(timeout=timeout), self._loop)
        try:
            future.result()
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=timeout)
