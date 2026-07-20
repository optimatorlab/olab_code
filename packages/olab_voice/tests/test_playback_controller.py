from __future__ import annotations

import asyncio
import time

import pytest

from olab_voice.playback.controller import _PlaybackController
from olab_voice.playback.models import TtsJob, TtsResult
from olab_voice.tts.base import TtsAudio, TtsRequest


class _FakeSynthesizer:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[str] = []

    async def synthesize(self, request: TtsRequest) -> TtsAudio:
        self.calls.append(request.text)
        if self.delay:
            await asyncio.sleep(self.delay)
        return TtsAudio(data=b"RIFF....", format="audio/wav")


class _BlockingSynthesizer:
    """A synthesizer whose `synthesize()` is `async` but blocks its OS thread
    synchronously, like real Piper inference does."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[str] = []

    async def synthesize(self, request: TtsRequest) -> TtsAudio:
        self.calls.append(request.text)
        if self.delay:
            time.sleep(self.delay)
        return TtsAudio(data=b"RIFF....", format="audio/wav")


class _FakeSink:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.played: list[TtsAudio] = []

    async def play(self, audio: TtsAudio) -> None:
        self.played.append(audio)
        if self.delay:
            await asyncio.sleep(self.delay)


async def _wait_for(job: TtsJob, timeout: float = 2.0) -> TtsResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while job.status in ("queued", "running"):
        if loop.time() > deadline:
            raise TimeoutError(f"job {job.id} did not settle in time")
        await asyncio.sleep(0.005)
    assert job.result is not None
    return job.result


def test_queue_processes_in_fifo_order() -> None:
    async def scenario() -> None:
        synth = _FakeSynthesizer()
        sink = _FakeSink()
        controller = _PlaybackController(synth, sink, max_queue_size=8)
        controller.bind(asyncio.get_running_loop())

        jobs = [TtsJob(TtsRequest(text=f"msg-{i}")) for i in range(3)]
        for job in jobs:
            assert await controller.submit(job) is True

        for job in jobs:
            result = await _wait_for(job)
            assert result.outcome == "completed"

        assert synth.calls == ["msg-0", "msg-1", "msg-2"]
        await controller.close()

    asyncio.run(scenario())


def test_full_queue_rejects_new_ordinary_requests() -> None:
    async def scenario() -> None:
        synth = _FakeSynthesizer(delay=0.2)
        sink = _FakeSink()
        controller = _PlaybackController(synth, sink, max_queue_size=2)
        controller.bind(asyncio.get_running_loop())

        active_job = TtsJob(TtsRequest(text="active"))
        assert await controller.submit(active_job) is True
        await asyncio.sleep(0.02)  # let the worker pop it into the active slot

        job1 = TtsJob(TtsRequest(text="p1"))
        job2 = TtsJob(TtsRequest(text="p2"))
        job3 = TtsJob(TtsRequest(text="p3"))
        assert await controller.submit(job1) is True
        assert await controller.submit(job2) is True
        assert await controller.submit(job3) is False

        await controller.close()

    asyncio.run(scenario())


def test_preempt_cancels_active_and_pending_then_plays_alert() -> None:
    async def scenario() -> None:
        synth = _FakeSynthesizer()
        sink = _FakeSink(delay=1.0)
        controller = _PlaybackController(synth, sink, max_queue_size=8)
        controller.bind(asyncio.get_running_loop())

        active_job = TtsJob(TtsRequest(text="long playback"))
        assert await controller.submit(active_job) is True
        await asyncio.sleep(0.02)  # let it become the active, in-progress job

        pending_job = TtsJob(TtsRequest(text="pending"))
        assert await controller.submit(pending_job) is True

        alert_job = TtsJob(TtsRequest(text="alert", preempt=True))
        assert await controller.submit(alert_job) is True

        active_result = await _wait_for(active_job)
        pending_result = await _wait_for(pending_job)
        alert_result = await _wait_for(alert_job)

        assert active_result.outcome == "preempted"
        assert pending_result.outcome == "preempted"
        assert alert_result.outcome == "completed"

        await controller.close()

    asyncio.run(scenario())


def test_preempt_with_nothing_active_just_queues_next() -> None:
    async def scenario() -> None:
        synth = _FakeSynthesizer()
        sink = _FakeSink()
        controller = _PlaybackController(synth, sink, max_queue_size=8)
        controller.bind(asyncio.get_running_loop())

        alert_job = TtsJob(TtsRequest(text="alert", preempt=True))
        assert await controller.submit(alert_job) is True

        result = await _wait_for(alert_job)
        assert result.outcome == "completed"

        await controller.close()

    asyncio.run(scenario())


def test_submit_stays_responsive_during_blocking_synthesis() -> None:
    """A genuinely blocking synthesize() must not stall the controller's event
    loop -- submit()/preempt must still return promptly while it runs."""

    async def scenario() -> None:
        synth = _BlockingSynthesizer(delay=0.35)
        sink = _FakeSink()
        controller = _PlaybackController(synth, sink, max_queue_size=8)
        controller.bind(asyncio.get_running_loop())

        active_job = TtsJob(TtsRequest(text="long blocking synth"))
        assert await controller.submit(active_job) is True
        await asyncio.sleep(0.05)  # let the worker hand synthesis to its executor thread

        loop = asyncio.get_running_loop()
        start = loop.time()
        alert_job = TtsJob(TtsRequest(text="alert", preempt=True))
        accepted = await controller.submit(alert_job)
        elapsed = loop.time() - start

        assert accepted is True
        assert elapsed < 0.15, f"submit() blocked for {elapsed}s during synthesis"

        active_result = await _wait_for(active_job, timeout=2.0)
        alert_result = await _wait_for(alert_job, timeout=2.0)
        assert active_result.outcome == "preempted"
        assert alert_result.outcome == "completed"

        await controller.close()

    asyncio.run(scenario())


def test_close_waits_for_blocking_synthesis_thread_to_finish() -> None:
    """close() must not return while the non-daemon synthesis worker thread is
    still executing a genuinely blocking synthesize() call -- otherwise that
    thread outlives close()'s claim to have stopped everything."""

    async def scenario() -> None:
        synth = _BlockingSynthesizer(delay=0.3)
        sink = _FakeSink()
        controller = _PlaybackController(synth, sink, max_queue_size=8)
        controller.bind(asyncio.get_running_loop())

        job = TtsJob(TtsRequest(text="long blocking synth"))
        assert await controller.submit(job) is True
        await asyncio.sleep(0.02)  # let the worker hand synthesis to its executor thread

        loop = asyncio.get_running_loop()
        start = loop.time()
        await controller.close()
        elapsed = loop.time() - start

        assert elapsed >= 0.25, f"close() returned after only {elapsed}s; the worker thread may still be running"
        with pytest.raises(RuntimeError):
            controller._synthesis_executor.submit(lambda: None)

    asyncio.run(scenario())


def test_close_with_timeout_raises_while_blocking_synthesis_runs() -> None:
    async def scenario() -> None:
        synth = _BlockingSynthesizer(delay=1.0)
        sink = _FakeSink()
        controller = _PlaybackController(synth, sink, max_queue_size=8)
        controller.bind(asyncio.get_running_loop())

        job = TtsJob(TtsRequest(text="very long blocking synth"))
        assert await controller.submit(job) is True
        await asyncio.sleep(0.02)

        with pytest.raises(TimeoutError):
            await controller.close(timeout=0.1)

    asyncio.run(scenario())


def test_close_rejects_further_submissions() -> None:
    async def scenario() -> None:
        synth = _FakeSynthesizer()
        sink = _FakeSink()
        controller = _PlaybackController(synth, sink, max_queue_size=8)
        controller.bind(asyncio.get_running_loop())

        await controller.close()

        job = TtsJob(TtsRequest(text="after close"))
        assert await controller.submit(job) is False

    asyncio.run(scenario())
