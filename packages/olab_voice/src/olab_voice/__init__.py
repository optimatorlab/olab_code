"""Local-first voice capture, STT, TTS, wake phrase, and transport adapters."""

from importlib.metadata import PackageNotFoundError, version

from olab_voice.audio.models import AudioBlob, AudioFrame
from olab_voice.defaults import (
    default_faster_whisper_model,
    default_model_root,
    default_piper_model,
)
from olab_voice.files import audio_format_for_path, synthesize_to_wav, transcribe_file
from olab_voice.playback import (
    AplayPlaybackSink,
    PlaybackUnavailableError,
    ServiceStatus,
    TtsJob,
    TtsPlaybackService,
    TtsResult,
)
from olab_voice.sessions import CommandSession
from olab_voice.stt.base import TranscriptEvent
from olab_voice.tts.base import TtsAudio, TtsRequest

__all__ = [
    "__version__",
    "AplayPlaybackSink",
    "AudioBlob",
    "AudioFrame",
    "CommandSession",
    "PlaybackUnavailableError",
    "ServiceStatus",
    "TranscriptEvent",
    "TtsAudio",
    "TtsJob",
    "TtsPlaybackService",
    "TtsRequest",
    "TtsResult",
    "audio_format_for_path",
    "default_faster_whisper_model",
    "default_model_root",
    "default_piper_model",
    "synthesize_to_wav",
    "transcribe_file",
]

try:
    __version__ = version("olab-voice")
except PackageNotFoundError:
    __version__ = "0.0.0"
