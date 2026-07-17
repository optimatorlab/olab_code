from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from olab_rf.models import (
    Observation,
    RadioVoiceSegment,
    SensorStatus,
    Track,
    VoiceCaptureEvent,
    VoiceSegmentStatus,
)
from olab_rf.transports.codec import pack_payload, unpack_payload
from olab_rf.transports.payloads import (
    observation_to_payload,
    status_to_payload,
    track_from_payload,
    track_to_payload,
)
from olab_rf.transports.subjects import (
    RF_OBSERVATION,
    RF_STATUS,
    RF_TRACK_UPDATED,
    RF_VOICE_AUDIO,
    RF_VOICE_EVENT,
    RF_VOICE_SEGMENT,
    RF_VOICE_STATUS,
)


class NatsClient(Protocol):
    async def publish(self, subject: str, payload: bytes) -> None:
        ...

    async def subscribe(self, subject: str, cb: Callable[[Any], Awaitable[None]]) -> Any:
        ...


@dataclass(slots=True)
class NatsRfTransport:
    client: NatsClient

    async def publish_track(self, track: Track, subject: str = RF_TRACK_UPDATED) -> None:
        await self.client.publish(subject, pack_payload(track_to_payload(track)))

    async def publish_observation(
        self,
        observation: Observation,
        subject: str = RF_OBSERVATION,
    ) -> None:
        await self.client.publish(subject, pack_payload(observation_to_payload(observation)))

    async def publish_status(self, status: SensorStatus, subject: str = RF_STATUS) -> None:
        await self.client.publish(subject, pack_payload(status_to_payload(status)))

    async def publish_voice_status(
        self,
        status: VoiceSegmentStatus,
        subject: str = RF_VOICE_STATUS,
    ) -> None:
        await self.client.publish(subject, pack_payload(status.to_dict()))

    async def publish_voice_event(
        self,
        event: VoiceCaptureEvent,
        subject: str = RF_VOICE_EVENT,
    ) -> None:
        await self.client.publish(subject, pack_payload(event.to_dict()))

    async def publish_voice_segment(
        self,
        segment: RadioVoiceSegment,
        subject: str = RF_VOICE_SEGMENT,
    ) -> None:
        """Publish segment metadata; use ``publish_voice_audio`` for WAV bytes."""
        await self.client.publish(subject, pack_payload(segment.to_dict()))

    async def publish_voice_audio(
        self,
        segment: RadioVoiceSegment,
        subject: str = RF_VOICE_AUDIO,
    ) -> None:
        """Publish the ``olab_voice.AudioBlob``-compatible WAV payload."""
        await self.client.publish(subject, pack_payload(segment.to_audio_blob_payload()))

    async def subscribe_tracks(self, handler: Callable[[Track], Awaitable[None]]) -> Any:
        async def callback(message: Any) -> None:
            await handler(track_from_payload(unpack_payload(message.data)))

        return await self.client.subscribe(RF_TRACK_UPDATED, cb=callback)
