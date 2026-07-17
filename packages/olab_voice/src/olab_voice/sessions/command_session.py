from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence
from uuid import uuid4

from olab_voice.audio.models import AudioBlob
from olab_voice.recipients.base import TranscriptRecipient
from olab_voice.stt.base import BatchTranscriber, TranscriptEvent


@dataclass(slots=True)
class CommandSession:
    """Push-to-talk command session for complete utterance blobs.

    This class is transport-neutral. It accepts a complete audio blob, delegates
    STT to a BatchTranscriber, and optionally forwards the resulting final
    transcript to application-level recipients.
    """

    transcriber: BatchTranscriber
    recipients: Sequence[TranscriptRecipient] = field(default_factory=tuple)
    session_id: str = field(default_factory=lambda: str(uuid4()))

    async def handle_audio_blob(self, audio: AudioBlob) -> TranscriptEvent:
        audio = self._with_session_id(audio)
        event = await self.transcriber.transcribe(audio)
        event = self._normalize_event(event, audio)
        for recipient in self.recipients:
            await recipient.handle_transcript(event)
        return event

    def _with_session_id(self, audio: AudioBlob) -> AudioBlob:
        if audio.session_id:
            return audio
        data = audio.to_dict()
        data["session_id"] = self.session_id
        return AudioBlob.from_dict(data)

    def _normalize_event(self, event: TranscriptEvent, audio: AudioBlob) -> TranscriptEvent:
        changed = False
        data = event.to_dict()
        if event.session_id is None:
            data["session_id"] = audio.session_id or self.session_id
            changed = True
        if event.user_id == 0 and audio.user_id != 0:
            data["user_id"] = audio.user_id
            changed = True
        if event.asset_id == 0 and audio.asset_id != 0:
            data["asset_id"] = audio.asset_id
            changed = True
        if changed:
            return TranscriptEvent.from_dict(data)
        return event
