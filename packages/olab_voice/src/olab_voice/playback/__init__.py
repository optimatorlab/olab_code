"""Local speaker playback: queued/preemptive delivery and a synchronous service API."""

from olab_voice.playback.aplay import AplayPlaybackSink, PlaybackUnavailableError
from olab_voice.playback.models import ServiceStatus, TtsJob, TtsJobStatus, TtsOutcome, TtsResult
from olab_voice.playback.service import (
    DEFAULT_MAX_QUEUE_SIZE,
    DEFAULT_MAX_TEXT_LENGTH,
    TtsPlaybackService,
)

__all__ = [
    "AplayPlaybackSink",
    "DEFAULT_MAX_QUEUE_SIZE",
    "DEFAULT_MAX_TEXT_LENGTH",
    "PlaybackUnavailableError",
    "ServiceStatus",
    "TtsJob",
    "TtsJobStatus",
    "TtsOutcome",
    "TtsPlaybackService",
    "TtsResult",
]
