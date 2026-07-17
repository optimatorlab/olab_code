from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from olab_voice.audio.models import AudioBlob
from olab_voice.stt.base import TranscriptEvent
from olab_voice.transports.codec import pack_payload, unpack_payload
from olab_voice.transports.payloads import (
    PayloadStyle,
    audio_blob_from_payload,
    audio_blob_to_payload,
    transcript_event_from_payload,
    transcript_event_to_payload,
    tts_audio_to_payload,
)
from olab_voice.transports.subjects import audio_blob_subject, transcript_subject, tts_audio_subject
from olab_voice.tts.base import TtsAudio


class NatsClient(Protocol):
    async def publish(self, subject: str, payload: bytes) -> None:
        ...

    async def subscribe(self, subject: str, cb: Callable[[Any], Awaitable[None]]) -> Any:
        ...


@dataclass(slots=True)
class NatsVoiceTransport:
    """MessagePack voice transport wrapper over an injected nats-py client."""

    client: NatsClient
    payload_style: PayloadStyle = "generic"

    async def publish_audio_blob(
        self, audio: AudioBlob, subject: str | None = None, style: PayloadStyle | None = None
    ) -> None:
        target = subject or audio_blob_subject(audio.session_id)
        payload = audio_blob_to_payload(audio, style or self.payload_style)
        await self.publish_payload(target, payload)

    async def publish_transcript(
        self, event: TranscriptEvent, subject: str | None = None, style: PayloadStyle | None = None
    ) -> None:
        if event.session_id is None and subject is None:
            raise ValueError("event.session_id is required when subject is not provided")
        target = subject or transcript_subject(event.session_id or "")
        payload = transcript_event_to_payload(event, style or self.payload_style)
        await self.publish_payload(target, payload)

    async def publish_tts_audio(self, audio: TtsAudio, subject: str | None = None) -> None:
        if audio.session_id is None and subject is None:
            raise ValueError("audio.session_id is required when subject is not provided")
        target = subject or tts_audio_subject(audio.session_id or "")
        await self.publish_payload(target, tts_audio_to_payload(audio))

    async def publish_payload(self, subject: str, payload: dict[str, Any]) -> None:
        await self.client.publish(subject, pack_payload(payload))

    async def subscribe_audio_blobs(
        self, subject: str, handler: Callable[[AudioBlob], Awaitable[None]]
    ) -> Any:
        async def callback(message: Any) -> None:
            await handler(audio_blob_from_payload(unpack_payload(message.data)))

        return await self.client.subscribe(subject, cb=callback)

    async def subscribe_transcripts(
        self, subject: str, handler: Callable[[TranscriptEvent], Awaitable[None]]
    ) -> Any:
        async def callback(message: Any) -> None:
            await handler(transcript_event_from_payload(unpack_payload(message.data)))

        return await self.client.subscribe(subject, cb=callback)
