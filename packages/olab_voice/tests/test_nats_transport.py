from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

pytest.importorskip("msgpack", reason="msgpack is not installed; install olab-voice[nats]")

from olab_voice.audio.models import AudioBlob
from olab_voice.stt.base import TranscriptEvent
from olab_voice.transports import (
    NatsVoiceTransport,
    audio_blob_from_payload,
    pack_payload,
    transcript_event_from_payload,
    unpack_payload,
)
from olab_voice.tts.base import TtsAudio


@dataclass(slots=True)
class _Message:
    data: bytes


@dataclass(slots=True)
class _FakeNatsClient:
    published: list[tuple[str, bytes]] = field(default_factory=list)
    subscriptions: dict[str, Any] = field(default_factory=dict)

    async def publish(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, payload))

    async def subscribe(self, subject: str, cb):
        self.subscriptions[subject] = cb
        return subject


def test_pack_unpack_payload_preserves_bytes():
    payload = {"data": b"abc", "format": "audio/wav"}

    packed = pack_payload(payload)

    assert isinstance(packed, bytes)
    assert unpack_payload(packed) == payload


def test_unpack_payload_rejects_non_dict():
    packed = pack_payload(["not", "a", "dict"])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="dict"):
        unpack_payload(packed)


def test_publish_audio_blob_uses_session_subject_and_msgpack_payload():
    client = _FakeNatsClient()
    transport = NatsVoiceTransport(client)
    audio = AudioBlob(data=b"abc", format="audio/wav", session_id="session-1")

    _run(transport.publish_audio_blob(audio))

    subject, packed = client.published[0]
    assert subject == "voice.sessions.session-1.audio.blob"
    assert audio_blob_from_payload(unpack_payload(packed)) == audio


def test_publish_transcript_can_use_legacy_payload_style_and_subject_override():
    client = _FakeNatsClient()
    transport = NatsVoiceTransport(client)
    event = TranscriptEvent(text="take off", session_id="session-1", user_id=7, asset_id=107)

    _run(transport.publish_transcript(event, subject="gcs.audio.transcribed", style="legacy"))

    subject, packed = client.published[0]
    payload = unpack_payload(packed)
    assert subject == "gcs.audio.transcribed"
    assert payload["userID"] == 7
    assert payload["assetID"] == 107
    assert transcript_event_from_payload(payload) == event


def test_publish_transcript_requires_session_or_subject():
    transport = NatsVoiceTransport(_FakeNatsClient())

    with pytest.raises(ValueError, match="session_id"):
        _run(transport.publish_transcript(TranscriptEvent(text="take off")))


def test_publish_tts_audio_uses_session_subject():
    client = _FakeNatsClient()
    transport = NatsVoiceTransport(client)
    audio = TtsAudio(data=b"RIFF", format="audio/wav", session_id="session-1")

    _run(transport.publish_tts_audio(audio))

    subject, packed = client.published[0]
    assert subject == "voice.sessions.session-1.tts.audio"
    assert unpack_payload(packed)["data"] == b"RIFF"


def test_subscribe_audio_blobs_decodes_message_for_handler():
    client = _FakeNatsClient()
    transport = NatsVoiceTransport(client)
    seen: list[AudioBlob] = []

    async def handler(audio: AudioBlob) -> None:
        seen.append(audio)

    _run(transport.subscribe_audio_blobs("voice.sessions.session-1.audio.blob", handler))
    message = _Message(pack_payload(AudioBlob(data=b"abc", format="audio/wav").to_dict()))
    _run(client.subscriptions["voice.sessions.session-1.audio.blob"](message))

    assert seen == [AudioBlob.from_dict(message_payload(message))]


def test_subscribe_transcripts_decodes_message_for_handler():
    client = _FakeNatsClient()
    transport = NatsVoiceTransport(client)
    seen: list[TranscriptEvent] = []

    async def handler(event: TranscriptEvent) -> None:
        seen.append(event)

    _run(transport.subscribe_transcripts("voice.sessions.session-1.transcript.segment_final", handler))
    event = TranscriptEvent(text="take off", session_id="session-1")
    message = _Message(pack_payload(event.to_dict()))
    _run(client.subscriptions["voice.sessions.session-1.transcript.segment_final"](message))

    assert seen == [event]


def message_payload(message: _Message):
    return unpack_payload(message.data)


def _run(awaitable):
    return asyncio.run(awaitable)
