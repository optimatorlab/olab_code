from __future__ import annotations

import asyncio
import time

import pytest

from olab_voice.playback.service import TtsPlaybackService
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


class _FakeSink:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.played: list[TtsAudio] = []

    async def play(self, audio: TtsAudio) -> None:
        self.played.append(audio)
        if self.delay:
            await asyncio.sleep(self.delay)


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


class _FailingSynthesizer:
    async def synthesize(self, request: TtsRequest) -> TtsAudio:
        raise RuntimeError("synth boom")


class _FailingSink:
    async def play(self, audio: TtsAudio) -> None:
        raise RuntimeError("sink boom")


def test_speak_and_wait_completes() -> None:
    service = TtsPlaybackService(_FakeSynthesizer(), _FakeSink(), max_queue_size=4)
    try:
        result = service.speak_and_wait(TtsRequest(text="107 is listening"), timeout=2.0)
    finally:
        service.close(timeout=2.0)

    assert result.outcome == "completed"


def test_disabled_service_does_not_synthesize_or_play() -> None:
    synth = _FakeSynthesizer()
    sink = _FakeSink()
    service = TtsPlaybackService(synth, sink, enabled=False)
    try:
        result = service.speak_and_wait(TtsRequest(text="ignored"), timeout=2.0)
    finally:
        service.close(timeout=2.0)

    assert result.outcome == "disabled"
    assert synth.calls == []
    assert sink.played == []


def test_set_enabled_toggles_behavior() -> None:
    synth = _FakeSynthesizer()
    service = TtsPlaybackService(synth, _FakeSink(), enabled=False)
    try:
        service.set_enabled(True)
        result = service.speak_and_wait(TtsRequest(text="107 is listening"), timeout=2.0)
    finally:
        service.close(timeout=2.0)

    assert result.outcome == "completed"


def test_empty_text_is_rejected_without_synthesizing() -> None:
    synth = _FakeSynthesizer()
    service = TtsPlaybackService(synth, _FakeSink())
    try:
        job = service.speak(TtsRequest(text="   "))
    finally:
        service.close(timeout=2.0)

    assert job.status == "rejected"
    assert synth.calls == []


def test_text_over_max_length_is_rejected() -> None:
    service = TtsPlaybackService(_FakeSynthesizer(), _FakeSink(), max_text_length=5)
    try:
        job = service.speak(TtsRequest(text="way too long for the configured limit"))
    finally:
        service.close(timeout=2.0)

    assert job.status == "rejected"


def test_full_queue_returns_rejected_outcome() -> None:
    synth = _FakeSynthesizer(delay=0.3)
    sink = _FakeSink(delay=0.3)
    service = TtsPlaybackService(synth, sink, max_queue_size=1)
    try:
        active_job = service.speak(TtsRequest(text="active"))
        time.sleep(0.05)  # let the background worker pick up the active job

        job1 = service.speak(TtsRequest(text="pending-1"))
        job2 = service.speak(TtsRequest(text="pending-2 too many"))

        assert job2.status == "rejected"
        assert job2.result is not None
        assert job2.result.error == "playback queue is full"

        assert active_job.wait(timeout=2.0).outcome == "completed"
        assert job1.wait(timeout=2.0).outcome == "completed"
    finally:
        service.close(timeout=2.0)


def test_preempt_cancels_active_and_pending_then_plays_alert() -> None:
    synth = _FakeSynthesizer()
    sink = _FakeSink(delay=1.0)
    service = TtsPlaybackService(synth, sink, max_queue_size=8)
    try:
        active_job = service.speak(TtsRequest(text="long playback"))
        time.sleep(0.05)

        pending_job = service.speak(TtsRequest(text="pending"))
        alert_job = service.speak(TtsRequest(text="alert", preempt=True))

        assert active_job.wait(timeout=2.0).outcome == "preempted"
        assert pending_job.wait(timeout=2.0).outcome == "preempted"
        assert alert_job.wait(timeout=2.0).outcome == "completed"
    finally:
        service.close(timeout=2.0)


def test_speak_does_not_block_on_genuinely_blocking_synthesis() -> None:
    synth = _BlockingSynthesizer(delay=0.4)
    service = TtsPlaybackService(synth, _FakeSink(), max_queue_size=4)
    try:
        start = time.monotonic()
        job = service.speak(TtsRequest(text="107 is listening"))
        elapsed = time.monotonic() - start

        assert elapsed < 0.2, f"speak() blocked for {elapsed}s waiting on synthesis"
        assert job.wait(timeout=2.0).outcome == "completed"
    finally:
        service.close(timeout=2.0)


def test_preempt_stays_responsive_during_blocking_synthesis() -> None:
    synth = _BlockingSynthesizer(delay=0.4)
    service = TtsPlaybackService(synth, _FakeSink(), max_queue_size=8)
    try:
        active_job = service.speak(TtsRequest(text="active"))
        time.sleep(0.05)  # let the worker hand synthesis to its executor thread

        start = time.monotonic()
        alert_job = service.speak(TtsRequest(text="alert", preempt=True))
        elapsed = time.monotonic() - start

        assert elapsed < 0.2, f"preempting speak() blocked for {elapsed}s"
        assert active_job.wait(timeout=2.0).outcome == "preempted"
        assert alert_job.wait(timeout=2.0).outcome == "completed"
    finally:
        service.close(timeout=2.0)


def test_non_bool_preempt_is_rejected_without_synthesizing() -> None:
    synth = _FakeSynthesizer()
    service = TtsPlaybackService(synth, _FakeSink())
    try:
        job = service.speak(TtsRequest(text="107 is listening", preempt="false"))  # type: ignore[arg-type]
    finally:
        service.close(timeout=2.0)

    assert job.status == "rejected"
    assert job.result is not None
    assert job.result.error == "preempt must be a bool"
    assert synth.calls == []


def test_close_waits_for_blocking_synthesis_thread_to_finish() -> None:
    """close() must not return while the non-daemon synthesis worker thread is
    still executing a genuinely blocking synthesize() call."""

    synth = _BlockingSynthesizer(delay=0.3)
    service = TtsPlaybackService(synth, _FakeSink())
    job = service.speak(TtsRequest(text="107 is listening"))
    time.sleep(0.05)  # let the worker hand synthesis to its executor thread

    start = time.monotonic()
    service.close()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.25, f"close() returned after only {elapsed}s; the worker thread may still be running"
    assert job.wait(timeout=2.0).outcome == "preempted"


def test_close_with_short_timeout_raises_during_blocking_synthesis() -> None:
    synth = _BlockingSynthesizer(delay=1.0)
    service = TtsPlaybackService(synth, _FakeSink())
    service.speak(TtsRequest(text="107 is listening"))
    time.sleep(0.05)

    with pytest.raises(TimeoutError):
        service.close(timeout=0.1)


def test_synthesis_failure_outcome() -> None:
    service = TtsPlaybackService(_FailingSynthesizer(), _FakeSink())
    try:
        result = service.speak_and_wait(TtsRequest(text="107 is listening"), timeout=2.0)
    finally:
        service.close(timeout=2.0)

    assert result.outcome == "synthesis_failed"
    assert result.error == "synth boom"


def test_playback_failure_outcome() -> None:
    service = TtsPlaybackService(_FakeSynthesizer(), _FailingSink())
    try:
        result = service.speak_and_wait(TtsRequest(text="107 is listening"), timeout=2.0)
    finally:
        service.close(timeout=2.0)

    assert result.outcome == "playback_failed"
    assert result.error == "sink boom"


def test_status_reports_enabled_and_closed_state() -> None:
    service = TtsPlaybackService(_FakeSynthesizer(), _FakeSink())

    status_before = service.status
    assert status_before.enabled is True
    assert status_before.closed is False

    service.speak_and_wait(TtsRequest(text="107 is listening"), timeout=2.0)
    service.close(timeout=2.0)

    status_after = service.status
    assert status_after.closed is True

    rejected_job = service.speak(TtsRequest(text="after close"))
    assert rejected_job.status == "rejected"


def test_job_wait_times_out_when_not_yet_complete() -> None:
    service = TtsPlaybackService(_FakeSynthesizer(delay=1.0), _FakeSink())
    try:
        job = service.speak(TtsRequest(text="slow"))
        try:
            job.wait(timeout=0.05)
            raise AssertionError("expected TimeoutError")
        except TimeoutError:
            pass
    finally:
        service.close(timeout=2.0)
