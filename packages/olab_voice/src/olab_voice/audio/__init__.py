"""Audio data models and source/sink adapter interfaces."""

from olab_voice.audio.base import AudioBlobSource, AudioFrameSource, AudioPlaybackSink
from olab_voice.audio.models import AudioBlob, AudioFrame, AudioSource
from olab_voice.audio.wav import (
    audio_frame_to_wav_bytes,
    load_wav_blob,
    pcm_s16le_to_wav_bytes,
    save_wav_bytes,
    wav_bytes_to_audio_blob,
    wav_duration_seconds,
    wav_info,
)

__all__ = [
    "AudioBlob",
    "AudioBlobSource",
    "AudioFrame",
    "AudioFrameSource",
    "AudioPlaybackSink",
    "AudioSource",
    "audio_frame_to_wav_bytes",
    "load_wav_blob",
    "pcm_s16le_to_wav_bytes",
    "save_wav_bytes",
    "wav_bytes_to_audio_blob",
    "wav_duration_seconds",
    "wav_info",
]
