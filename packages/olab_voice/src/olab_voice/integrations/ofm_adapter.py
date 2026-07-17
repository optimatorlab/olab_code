from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from olab_voice.audio.models import AudioBlob
from olab_voice.sessions import CommandSession
from olab_voice.stt.base import TranscriptEvent
from olab_voice.transports.codec import unpack_payload
from olab_voice.transports.nats import NatsVoiceTransport
from olab_voice.transports.payloads import audio_blob_from_payload
from olab_voice.transports.subjects import (
    GCS_AUDIO_COMMAND,
    GCS_AUDIO_RESPONSE,
    GCS_AUDIO_TRANSCRIBED,
)
from olab_voice.tts.base import TtsAudio


class NatsSubscriber(Protocol):
    async def subscribe(self, subject: str, cb: Callable[[Any], Awaitable[None]]) -> Any:
        ...


@dataclass(slots=True)
class OfmVoiceAdapter:
    """OFM legacy `gcs.audio.*` bridge around a transport-neutral command session."""

    session: CommandSession
    transport: NatsVoiceTransport
    command_subject: str = GCS_AUDIO_COMMAND
    transcript_subject: str = GCS_AUDIO_TRANSCRIBED
    response_subject: str = GCS_AUDIO_RESPONSE

    async def start(self) -> Any:
        return await self.transport.client.subscribe(self.command_subject, cb=self._handle_command_message)

    async def handle_audio_blob(self, audio: AudioBlob) -> TranscriptEvent:
        event = await self.session.handle_audio_blob(audio)
        await self.publish_transcript(event)
        return event

    async def publish_transcript(self, event: TranscriptEvent) -> None:
        await self.transport.publish_transcript(
            event,
            subject=self.transcript_subject,
            style="legacy",
        )

    async def publish_response_audio(self, audio: TtsAudio) -> None:
        await self.transport.publish_tts_audio(audio, subject=self.response_subject)

    async def _handle_command_message(self, message: Any) -> None:
        audio = audio_blob_from_payload(unpack_payload(message.data))
        await self.handle_audio_blob(audio)
