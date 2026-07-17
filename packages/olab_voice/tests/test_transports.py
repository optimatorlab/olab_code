from __future__ import annotations

import importlib.util

import pytest

from olab_voice.audio.models import AudioBlob
from olab_voice.stt.base import TranscriptEvent
from olab_voice.transports import (
    GCS_AUDIO_COMMAND,
    GCS_AUDIO_RESPONSE,
    GCS_AUDIO_TRANSCRIBED,
    VOICE_AUDIO_BLOB,
    VOICE_TRANSCRIPT_SEGMENT_FINAL,
    audio_blob_from_payload,
    audio_blob_subject,
    audio_blob_to_payload,
    transcript_event_from_payload,
    transcript_event_to_payload,
    transcript_subject,
    tts_audio_from_payload,
    tts_audio_subject,
    tts_audio_to_payload,
    voice_session_subject,
)
from olab_voice.tts.base import TtsAudio


def test_legacy_subject_constants():
    assert GCS_AUDIO_COMMAND == "gcs.audio.command"
    assert GCS_AUDIO_TRANSCRIBED == "gcs.audio.transcribed"
    assert GCS_AUDIO_RESPONSE == "gcs.audio.response"


def test_voice_session_subject_helpers():
    assert voice_session_subject("session-1", VOICE_AUDIO_BLOB) == (
        "voice.sessions.session-1.audio.blob"
    )
    assert audio_blob_subject("session-1") == "voice.sessions.session-1.audio.blob"
    assert transcript_subject("session-1") == "voice.sessions.session-1.transcript.segment_final"
    assert tts_audio_subject("session-1") == "voice.sessions.session-1.tts.audio"


def test_voice_session_subject_rejects_wildcards_and_bad_session_tokens():
    with pytest.raises(ValueError, match="session_id"):
        voice_session_subject("bad.session", VOICE_AUDIO_BLOB)
    with pytest.raises(ValueError, match="wildcards"):
        voice_session_subject("session-1", "transcript.*")


def test_audio_blob_payload_round_trip_generic_and_legacy():
    audio = AudioBlob(
        data=b"abc",
        format="audio/wav",
        session_id="session-1",
        source="browser",
        user_id=7,
        asset_id=107,
        sample_rate=16000,
        channels=1,
    )

    generic = audio_blob_to_payload(audio)
    legacy = audio_blob_to_payload(audio, style="legacy")

    assert generic["user_id"] == 7
    assert legacy["userID"] == 7
    assert legacy["assetID"] == 107
    assert "user_id" not in legacy
    assert audio_blob_from_payload(generic) == audio
    assert audio_blob_from_payload(legacy) == audio


def test_transcript_payload_round_trip_generic_and_legacy():
    event = TranscriptEvent(
        text="hey 107 take off",
        session_id="session-1",
        user_id=7,
        asset_id=107,
        confidence=0.8,
    )

    generic = transcript_event_to_payload(event)
    legacy = transcript_event_to_payload(event, style="legacy")

    assert generic["type"] == VOICE_TRANSCRIPT_SEGMENT_FINAL
    assert legacy["userID"] == 7
    assert legacy["assetID"] == 107
    assert transcript_event_from_payload(generic) == event
    assert transcript_event_from_payload(legacy) == event


def test_tts_audio_payload_round_trip():
    audio = TtsAudio(data=b"RIFF", format="audio/wav", sample_rate=22050, channels=1)

    payload = tts_audio_to_payload(audio)

    assert payload["data"] == b"RIFF"
    assert tts_audio_from_payload(payload) == audio


def test_payloads_are_msgpack_ready_when_msgpack_is_installed():
    if importlib.util.find_spec("msgpack") is None:
        pytest.skip("msgpack is not installed")

    import msgpack

    audio = AudioBlob(data=b"abc", format="audio/wav", session_id="session-1")
    packed = msgpack.packb(audio_blob_to_payload(audio), use_bin_type=True)
    unpacked = msgpack.unpackb(packed, raw=False)

    assert audio_blob_from_payload(unpacked) == audio
