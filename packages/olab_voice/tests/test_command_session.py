from __future__ import annotations

from dataclasses import dataclass, field

from olab_voice.audio.models import AudioBlob
from olab_voice.sessions import CommandSession
from olab_voice.stt.base import TranscriptEvent


@dataclass(slots=True)
class _FakeTranscriber:
    text: str = "hey 107 take off"
    events: list[AudioBlob] = field(default_factory=list)

    async def transcribe(self, audio: AudioBlob) -> TranscriptEvent:
        self.events.append(audio)
        return TranscriptEvent(text=self.text)


@dataclass(slots=True)
class _MetadataTranscriber:
    async def transcribe(self, audio: AudioBlob) -> TranscriptEvent:
        return TranscriptEvent(
            text="already tagged",
            session_id="event-session",
            user_id=99,
            asset_id=1099,
        )


@dataclass(slots=True)
class _Recipient:
    events: list[TranscriptEvent] = field(default_factory=list)

    async def handle_transcript(self, event: TranscriptEvent) -> None:
        self.events.append(event)


def test_command_session_transcribes_blob_and_returns_event():
    transcriber = _FakeTranscriber()
    session = CommandSession(transcriber=transcriber, session_id="session-1")
    audio = AudioBlob(data=b"abc", format="audio/wav", session_id="audio-session")

    event = _run(session.handle_audio_blob(audio))

    assert event.text == "hey 107 take off"
    assert event.session_id == "audio-session"
    assert transcriber.events == [audio]


def test_command_session_propagates_audio_metadata_to_event():
    session = CommandSession(transcriber=_FakeTranscriber())
    audio = AudioBlob(
        data=b"abc",
        format="audio/wav",
        session_id="session-1",
        user_id=7,
        asset_id=107,
    )

    event = _run(session.handle_audio_blob(audio))

    assert event.session_id == "session-1"
    assert event.user_id == 7
    assert event.asset_id == 107


def test_command_session_preserves_transcriber_metadata():
    session = CommandSession(transcriber=_MetadataTranscriber())
    audio = AudioBlob(
        data=b"abc",
        format="audio/wav",
        session_id="audio-session",
        user_id=7,
        asset_id=107,
    )

    event = _run(session.handle_audio_blob(audio))

    assert event.session_id == "event-session"
    assert event.user_id == 99
    assert event.asset_id == 1099


def test_command_session_forwards_event_to_recipients():
    recipient_a = _Recipient()
    recipient_b = _Recipient()
    session = CommandSession(
        transcriber=_FakeTranscriber(),
        recipients=(recipient_a, recipient_b),
    )
    audio = AudioBlob(data=b"abc", format="audio/wav", session_id="session-1")

    event = _run(session.handle_audio_blob(audio))

    assert recipient_a.events == [event]
    assert recipient_b.events == [event]


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
