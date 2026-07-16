from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from olab_voice.audio.models import AudioBlob
from olab_voice.stt.base import TranscriptEvent


class FasterWhisperUnavailableError(RuntimeError):
    """Raised when Faster-Whisper is not installed or cannot be initialized."""


@dataclass(slots=True)
class FasterWhisperTranscriber:
    """Batch transcriber backed by a local Faster-Whisper model path.

    The constructor only records configuration. The model is loaded lazily on
    first transcription so importing and constructing this class remains cheap
    in environments where optional backend dependencies are absent.
    """

    model_path: str | Path
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = "en"
    beam_size: int = 5
    _model: Any | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.model_path = Path(self.model_path).expanduser()

    @classmethod
    def from_env(cls, env_var: str = "OLAB_VOICE_FASTER_WHISPER_MODEL", **kwargs: Any) -> "FasterWhisperTranscriber":
        import os

        model_path = os.environ.get(env_var)
        if not model_path:
            raise ValueError(f"{env_var} must point to a local Faster-Whisper model directory")
        return cls(model_path=model_path, **kwargs)

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        if not self.model_path.exists():
            raise FileNotFoundError(f"Faster-Whisper model path does not exist: {self.model_path}")

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise FasterWhisperUnavailableError(
                "faster-whisper is not installed; install olab-voice[stt-faster-whisper]"
            ) from exc

        self._model = WhisperModel(
            str(self.model_path),
            device=self.device,
            compute_type=self.compute_type,
            local_files_only=True,
        )
        return self._model

    async def transcribe(self, audio: AudioBlob) -> TranscriptEvent:
        if not audio.data:
            return TranscriptEvent(
                text="",
                session_id=audio.session_id,
                user_id=audio.user_id,
                asset_id=audio.asset_id,
                confidence=None,
            )

        model = self._load_model()
        source = BytesIO(audio.data)
        source.name = f"audio.{_extension_for_format(audio.format)}"

        segments, _info = model.transcribe(
            source,
            language=self.language,
            beam_size=self.beam_size,
        )
        collected = list(segments)
        text = " ".join(segment.text.strip() for segment in collected if segment.text.strip()).strip()
        start_time = min((segment.start for segment in collected), default=None)
        end_time = max((segment.end for segment in collected), default=None)
        confidence = _average_probability(collected)

        return TranscriptEvent(
            text=text,
            session_id=audio.session_id,
            user_id=audio.user_id,
            asset_id=audio.asset_id,
            confidence=confidence,
            start_time=start_time,
            end_time=end_time,
        )


def _extension_for_format(format_name: str) -> str:
    lowered = format_name.lower()
    if "wav" in lowered:
        return "wav"
    if "webm" in lowered:
        return "webm"
    if "ogg" in lowered or "opus" in lowered:
        return "ogg"
    if "mpeg" in lowered or "mp3" in lowered:
        return "mp3"
    return "bin"


def _average_probability(segments: list[Any]) -> float | None:
    values = [segment.avg_logprob for segment in segments if segment.avg_logprob is not None]
    if not values:
        return None
    return sum(values) / len(values)
