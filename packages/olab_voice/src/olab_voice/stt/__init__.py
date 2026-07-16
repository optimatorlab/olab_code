"""Speech-to-text backend interfaces and implementations."""

from olab_voice.stt.base import BatchTranscriber, StreamingTranscriber, TranscriptEvent, TranscriptEventType
from olab_voice.stt.faster_whisper import FasterWhisperTranscriber, FasterWhisperUnavailableError
from olab_voice.stt.faster_whisper_streaming import (
    FasterWhisperStreamingTranscriber,
    FasterWhisperStreamingUnavailableError,
    StreamingBackpressureError,
)
from olab_voice.stt.hybrid import HybridStreamingTranscriber
from olab_voice.stt.vosk import VoskStreamingTranscriber, VoskUnavailableError

__all__ = [
    "BatchTranscriber",
    "FasterWhisperTranscriber",
    "FasterWhisperUnavailableError",
    "FasterWhisperStreamingTranscriber",
    "FasterWhisperStreamingUnavailableError",
    "HybridStreamingTranscriber",
    "StreamingTranscriber",
    "StreamingBackpressureError",
    "TranscriptEvent",
    "TranscriptEventType",
    "VoskStreamingTranscriber",
    "VoskUnavailableError",
]
