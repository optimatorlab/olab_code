from __future__ import annotations

from typing import Protocol

from olab_voice.stt.base import TranscriptEvent


class TranscriptRecipient(Protocol):
    """Consumes finalized transcripts without coupling olab_voice to app logic."""

    async def handle_transcript(self, event: TranscriptEvent) -> None:
        ...
