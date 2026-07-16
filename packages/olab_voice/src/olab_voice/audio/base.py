from __future__ import annotations

from typing import AsyncIterator, Protocol

from olab_voice.audio.models import AudioBlob, AudioFrame
from olab_voice.tts.base import TtsAudio


class AudioFrameSource(Protocol):
    """Produces live audio frames from an external audio implementation."""

    async def frames(self) -> AsyncIterator[AudioFrame]:
        ...


class AudioBlobSource(Protocol):
    """Produces complete utterance/file blobs from an external audio implementation."""

    async def read_blob(self) -> AudioBlob:
        ...


class AudioPlaybackSink(Protocol):
    """Consumes synthesized audio without tying olab_voice to a device backend."""

    async def play(self, audio: TtsAudio) -> None:
        ...
