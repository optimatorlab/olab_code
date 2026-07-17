from __future__ import annotations

from typing import Any, Literal

from olab_voice.audio.models import AudioBlob
from olab_voice.stt.base import TranscriptEvent
from olab_voice.tts.base import TtsAudio

PayloadStyle = Literal["generic", "legacy"]


def audio_blob_to_payload(audio: AudioBlob, style: PayloadStyle = "generic") -> dict[str, Any]:
    return _apply_style(audio.to_dict(), style)


def audio_blob_from_payload(payload: dict[str, Any]) -> AudioBlob:
    return AudioBlob.from_dict(_normalize_keys(payload))


def transcript_event_to_payload(
    event: TranscriptEvent, style: PayloadStyle = "generic"
) -> dict[str, Any]:
    return _apply_style(event.to_dict(), style)


def transcript_event_from_payload(payload: dict[str, Any]) -> TranscriptEvent:
    return TranscriptEvent.from_dict(_normalize_keys(payload))


def tts_audio_to_payload(audio: TtsAudio) -> dict[str, Any]:
    return audio.to_dict()


def tts_audio_from_payload(payload: dict[str, Any]) -> TtsAudio:
    return TtsAudio.from_dict(payload)


def _apply_style(payload: dict[str, Any], style: PayloadStyle) -> dict[str, Any]:
    if style == "generic":
        return dict(payload)
    if style == "legacy":
        data = dict(payload)
        if "user_id" in data:
            data["userID"] = data.pop("user_id")
        if "asset_id" in data:
            data["assetID"] = data.pop("asset_id")
        return data
    raise ValueError(f"unknown payload style: {style!r}")


def _normalize_keys(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    if "userID" in data and "user_id" not in data:
        data["user_id"] = data.pop("userID")
    if "assetID" in data and "asset_id" not in data:
        data["asset_id"] = data.pop("assetID")
    return data
