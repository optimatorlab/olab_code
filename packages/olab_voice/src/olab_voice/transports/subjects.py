from __future__ import annotations


GCS_AUDIO_COMMAND = "gcs.audio.command"
GCS_AUDIO_TRANSCRIBED = "gcs.audio.transcribed"
GCS_AUDIO_RESPONSE = "gcs.audio.response"

VOICE_AUDIO_FRAME = "audio.frame"
VOICE_AUDIO_BLOB = "audio.blob"
VOICE_TRANSCRIPT_HYPOTHESIS = "transcript.hypothesis"
VOICE_TRANSCRIPT_SEGMENT_FINAL = "transcript.segment_final"
VOICE_TRANSCRIPT_INTERVAL_FINAL = "transcript.interval_final"
VOICE_TTS_REQUEST = "tts.request"
VOICE_TTS_AUDIO = "tts.audio"
VOICE_WAKE_DETECTED = "wake.detected"
VOICE_COMMAND_WINDOW_STARTED = "command.window_started"
VOICE_COMMAND_WINDOW_CLOSED = "command.window_closed"
VOICE_RECIPIENT_REQUEST = "recipient.request"
VOICE_RECIPIENT_RESPONSE = "recipient.response"
VOICE_STATUS = "status"
VOICE_CONTROL = "control"


def voice_session_subject(session_id: str, event: str) -> str:
    _validate_subject_token("session_id", session_id)
    _validate_event(event)
    return f"voice.sessions.{session_id}.{event}"


def audio_blob_subject(session_id: str) -> str:
    return voice_session_subject(session_id, VOICE_AUDIO_BLOB)


def transcript_subject(session_id: str, transcript_type: str = VOICE_TRANSCRIPT_SEGMENT_FINAL) -> str:
    return voice_session_subject(session_id, transcript_type)


def tts_audio_subject(session_id: str) -> str:
    return voice_session_subject(session_id, VOICE_TTS_AUDIO)


def _validate_subject_token(name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{name} must not be empty")
    if "." in value or "*" in value or ">" in value:
        raise ValueError(f"{name} must be a single NATS subject token")


def _validate_event(event: str) -> None:
    if not event:
        raise ValueError("event must not be empty")
    if "*" in event or ">" in event:
        raise ValueError("event must not contain NATS wildcards")
