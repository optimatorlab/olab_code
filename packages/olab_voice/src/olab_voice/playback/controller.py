"""Backend-neutral playback queue/preemption logic.

`_PlaybackController` owns pending jobs and the active job's asyncio task.
All of its methods run on the service's dedicated event-loop thread (called
via `asyncio.run_coroutine_threadsafe`), so its internal state needs no
locking of its own -- only `TtsJob` (read from other threads) is locked.

`SpeechSynthesizer.synthesize()` is declared `async`, but real backends (e.g.
Piper) do synchronous, non-interruptible inference inside that coroutine. If
we awaited it directly on this controller's event loop, that blocking call
would stall the loop itself -- including new `submit()` calls arriving via
`run_coroutine_threadsafe`, defeating "speak() returns immediately" and
preemption. Instead, every synthesize() call is routed through a dedicated
single-worker thread pool (`_synthesis_executor`): the loop stays responsive
while a job synthesizes, and the single worker serializes synthesis calls so
two jobs never call into the same (not necessarily thread-safe) synthesizer
concurrently -- including a synthesis left running after its job was
preempted, which is simply left to finish and discarded.
"""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from olab_voice.audio.base import AudioPlaybackSink
from olab_voice.playback.models import TtsJob, TtsResult
from olab_voice.tts.base import SpeechSynthesizer, TtsAudio, TtsRequest


class _PlaybackController:
    def __init__(
        self,
        synthesizer: SpeechSynthesizer,
        sink: AudioPlaybackSink,
        *,
        max_queue_size: int,
    ) -> None:
        self._synthesizer = synthesizer
        self._sink = sink
        self._max_queue_size = max_queue_size
        self._pending: deque[TtsJob] = deque()
        self._active_job: TtsJob | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._wakeup: asyncio.Event | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._synthesis_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="olab-voice-tts-synth"
        )
        self._closed = False

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Create loop-bound primitives and start the worker. Call once, on the loop thread."""

        self._wakeup = asyncio.Event()
        self._worker_task = loop.create_task(self._run())

    @property
    def queue_depth(self) -> int:
        return len(self._pending)

    @property
    def active(self) -> bool:
        return self._active_job is not None

    async def submit(self, job: TtsJob) -> bool:
        """Runs on the loop thread. Returns whether the job was accepted."""

        if self._closed:
            return False

        if job.request.preempt:
            self._preempt_active()
            self._reject_pending("preempted")
            self._pending.append(job)
            self._wakeup.set()  # type: ignore[union-attr]
            return True

        if len(self._pending) >= self._max_queue_size:
            return False

        self._pending.append(job)
        self._wakeup.set()  # type: ignore[union-attr]
        return True

    async def close(self, timeout: float | None = None) -> None:
        """Runs on the loop thread. Cancels active work, drains pending jobs, and
        waits for the dedicated synthesis worker thread to actually finish.

        Cancelling the asyncio-level future wrapping a `run_in_executor()` call
        (as `_preempt_active()` does) marks that future cancelled immediately,
        regardless of whether the real OS thread running the blocking
        synthesize() call is still executing -- unlike `Task.cancel()`, a plain
        `asyncio.Future.cancel()` does not wait for the underlying work. That
        makes ordinary preemption responsive, but it would let `close()` return
        while a non-daemon worker thread is still running, which keeps the
        process alive after `close()` claims to have stopped everything. So
        `close()` explicitly waits (up to `timeout`) for the executor to shut
        down, raising `TimeoutError` if the in-flight synthesis call hasn't
        finished by then.
        """

        if self._closed:
            return
        self._closed = True
        self._preempt_active()
        self._reject_pending("preempted")
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass

        loop = asyncio.get_running_loop()
        shutdown_and_join = loop.run_in_executor(None, self._synthesis_executor.shutdown, True)
        try:
            await asyncio.wait_for(shutdown_and_join, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                "a non-interruptible synthesis call did not finish within the close() "
                "timeout; its worker thread is still running and will keep the process "
                "alive until it does"
            ) from exc

    def _preempt_active(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()

    def _reject_pending(self, outcome: str) -> None:
        while self._pending:
            job = self._pending.popleft()
            job._complete(TtsResult(job_id=job.id, request=job.request, outcome=outcome))  # type: ignore[arg-type]

    async def _run(self) -> None:
        assert self._wakeup is not None
        while not self._closed:
            await self._wakeup.wait()
            while self._pending and not self._closed:
                job = self._pending.popleft()
                self._active_job = job
                task = asyncio.ensure_future(self._process(job))
                self._active_task = task
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                self._active_job = None
                self._active_task = None
            self._wakeup.clear()

    async def _process(self, job: TtsJob) -> None:
        job._mark_running()
        request = job.request
        loop = asyncio.get_running_loop()
        try:
            audio = await loop.run_in_executor(
                self._synthesis_executor, self._synthesize_blocking, request
            )
        except asyncio.CancelledError:
            job._complete(TtsResult(job_id=job.id, request=request, outcome="preempted"))
            raise
        except Exception as exc:
            job._complete(
                TtsResult(job_id=job.id, request=request, outcome="synthesis_failed", error=str(exc))
            )
            return

        try:
            await self._sink.play(audio)
        except asyncio.CancelledError:
            job._complete(TtsResult(job_id=job.id, request=request, outcome="preempted"))
            raise
        except Exception as exc:
            job._complete(
                TtsResult(job_id=job.id, request=request, outcome="playback_failed", error=str(exc))
            )
            return

        job._complete(TtsResult(job_id=job.id, request=request, outcome="completed"))

    def _synthesize_blocking(self, request: TtsRequest) -> TtsAudio:
        """Runs on the dedicated synthesis worker thread, not the event loop."""

        return asyncio.run(self._synthesizer.synthesize(request))
