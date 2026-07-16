"""Text-to-speech backend interfaces and implementations."""

from olab_voice.tts.base import SpeechSynthesizer, TtsAudio, TtsOutputMode, TtsRequest
from olab_voice.tts.piper import PiperSynthesizer, PiperUnavailableError

__all__ = [
    "PiperSynthesizer",
    "PiperUnavailableError",
    "SpeechSynthesizer",
    "TtsAudio",
    "TtsOutputMode",
    "TtsRequest",
]
