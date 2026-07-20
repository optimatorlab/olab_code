"""Playback job/result/status types shared by the controller and service.

`TtsJob` is the handle returned by `TtsPlaybackService.speak()`. Its status
and result are written from the service's background worker thread and read
from arbitrary caller threads, so all access goes through `_lock`/`_done`
rather than relying on the GIL.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from olab_voice.tts.base import TtsRequest


TtsOutcome = Literal[
    "completed",
    "rejected",
    "disabled",
    "synthesis_failed",
    "playback_failed",
    "preempted",
]

TtsJobStatus = Literal[
    "queued",
    "running",
    "completed",
    "rejected",
    "disabled",
    "synthesis_failed",
    "playback_failed",
    "preempted",
]


@dataclass(slots=True)
class TtsResult:
    """Terminal outcome of a queued/played speech request."""

    job_id: str
    request: TtsRequest
    outcome: TtsOutcome
    error: str | None = None


@dataclass(slots=True)
class ServiceStatus:
    """Synchronous snapshot of a `TtsPlaybackService`."""

    enabled: bool
    closed: bool
    queue_depth: int
    active: bool


class TtsJob:
    """Handle returned by `speak()`; supports synchronous status/wait."""

    def __init__(self, request: TtsRequest, job_id: str | None = None) -> None:
        self.id = job_id or str(uuid4())
        self.request = request
        self._lock = threading.Lock()
        self._status: TtsJobStatus = "queued"
        self._result: TtsResult | None = None
        self._done = threading.Event()

    @property
    def status(self) -> TtsJobStatus:
        with self._lock:
            return self._status

    @property
    def result(self) -> TtsResult | None:
        with self._lock:
            return self._result

    def wait(self, timeout: float | None = None) -> TtsResult:
        if not self._done.wait(timeout):
            raise TimeoutError(f"TTS job {self.id} did not complete within {timeout}s")
        with self._lock:
            assert self._result is not None
            return self._result

    def _mark_running(self) -> None:
        with self._lock:
            if self._done.is_set():
                return
            self._status = "running"

    def _complete(self, result: TtsResult) -> None:
        with self._lock:
            if self._done.is_set():
                return
            self._status = result.outcome
            self._result = result
        self._done.set()
