from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

pytest.importorskip("msgpack", reason="msgpack is not installed; install olab-voice[nats]")

from olab_voice.audio.models import AudioBlob
from olab_voice.integrations import OfmVoiceAdapter
from olab_voice.sessions import CommandSession
from olab_voice.stt.base import TranscriptEvent
from olab_voice.transports import (
    GCS_AUDIO_COMMAND,
    GCS_AUDIO_RESPONSE,
    GCS_AUDIO_TRANSCRIBED,
    NatsVoiceTransport,
    audio_blob_to_payload,
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


@dataclass(slots=True)
class _FakeTranscriber:
    async def transcribe(self, audio: AudioBlob) -> TranscriptEvent:
        return TranscriptEvent(text="hey 107 take off")


def test_ofm_adapter_subscribes_to_legacy_command_subject():
    client = _FakeNatsClient()
    adapter = _adapter(client)

    subscription = _run(adapter.start())

    assert subscription == GCS_AUDIO_COMMAND
    assert GCS_AUDIO_COMMAND in client.subscriptions


def test_ofm_adapter_handles_legacy_audio_command_and_publishes_legacy_transcript():
    client = _FakeNatsClient()
    adapter = _adapter(client)
    _run(adapter.start())
    audio = AudioBlob(
        data=b"abc",
        format="audio/wav",
        session_id="session-1",
        source="browser",
        user_id=7,
        asset_id=107,
    )
    message = _Message(pack_payload(audio_blob_to_payload(audio, style="legacy")))

    _run(client.subscriptions[GCS_AUDIO_COMMAND](message))

    subject, packed = client.published[0]
    payload = unpack_payload(packed)
    event = transcript_event_from_payload(payload)
    assert subject == GCS_AUDIO_TRANSCRIBED
    assert payload["userID"] == 7
    assert payload["assetID"] == 107
    assert event.text == "hey 107 take off"
    assert event.session_id == "session-1"


def test_ofm_adapter_handle_audio_blob_returns_transcript():
    client = _FakeNatsClient()
    adapter = _adapter(client)
    audio = AudioBlob(data=b"abc", format="audio/wav", session_id="session-1")

    event = _run(adapter.handle_audio_blob(audio))

    assert event.text == "hey 107 take off"
    assert client.published[0][0] == GCS_AUDIO_TRANSCRIBED


def test_ofm_adapter_can_publish_response_audio():
    client = _FakeNatsClient()
    adapter = _adapter(client)
    audio = TtsAudio(data=b"RIFF", format="audio/wav", session_id="session-1")

    _run(adapter.publish_response_audio(audio))

    subject, packed = client.published[0]
    assert subject == GCS_AUDIO_RESPONSE
    assert unpack_payload(packed)["data"] == b"RIFF"


def _adapter(client: _FakeNatsClient) -> OfmVoiceAdapter:
    return OfmVoiceAdapter(
        session=CommandSession(transcriber=_FakeTranscriber()),
        transport=NatsVoiceTransport(client),
    )


def _run(awaitable):
    return asyncio.run(awaitable)
