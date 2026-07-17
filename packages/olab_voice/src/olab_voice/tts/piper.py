from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
import wave

from olab_voice.tts.base import TtsAudio, TtsRequest


class PiperUnavailableError(RuntimeError):
    """Raised when Piper is not installed or cannot be initialized."""


@dataclass(slots=True)
class PiperSynthesizer:
    """Piper-backed synthesizer that returns WAV bytes.

    The backend requires an explicit local ``.onnx`` model path. It never
    downloads voices at runtime.
    """

    model_path: str | Path
    config_path: str | Path | None = None
    speaker_id: int | None = None
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None
    _voice: Any | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.model_path = Path(self.model_path).expanduser()
        if self.config_path is not None:
            self.config_path = Path(self.config_path).expanduser()

    @classmethod
    def from_env(cls, env_var: str = "OLAB_VOICE_PIPER_MODEL", **kwargs: Any) -> "PiperSynthesizer":
        import os

        model_path = os.environ.get(env_var)
        if not model_path:
            raise ValueError(f"{env_var} must point to a local Piper .onnx model")
        config_path = os.environ.get(f"{env_var}_CONFIG")
        if config_path and "config_path" not in kwargs:
            kwargs["config_path"] = config_path
        return cls(model_path=model_path, **kwargs)

    def _load_voice(self) -> Any:
        if self._voice is not None:
            return self._voice

        if not self.model_path.exists():
            raise FileNotFoundError(f"Piper model path does not exist: {self.model_path}")
        if self.config_path is not None and not self.config_path.exists():
            raise FileNotFoundError(f"Piper config path does not exist: {self.config_path}")

        try:
            from piper.voice import PiperVoice
        except ImportError as exc:
            raise PiperUnavailableError("piper-tts is not installed; install olab-voice[tts-piper]") from exc

        if self.config_path is None:
            self._voice = PiperVoice.load(str(self.model_path))
        else:
            self._voice = PiperVoice.load(str(self.model_path), config_path=str(self.config_path))
        return self._voice

    async def synthesize(self, request: TtsRequest) -> TtsAudio:
        if request.format != "wav":
            raise ValueError(f"PiperSynthesizer only supports wav output, got {request.format!r}")
        if not request.text.strip():
            raise ValueError("TTS request text must not be empty")

        voice = self._load_voice()
        syn_config = self._synthesis_config()
        chunks = list(voice.synthesize(request.text, syn_config=syn_config))
        if not chunks:
            raise RuntimeError("Piper produced no audio chunks")

        first_chunk = chunks[0]
        sample_rate = first_chunk.sample_rate
        sample_width = first_chunk.sample_width
        channels = first_chunk.sample_channels

        output = BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            for chunk in chunks:
                if (
                    chunk.sample_rate != sample_rate
                    or chunk.sample_width != sample_width
                    or chunk.sample_channels != channels
                ):
                    raise RuntimeError("Piper returned chunks with inconsistent audio formats")
                wav_file.writeframes(chunk.audio_int16_bytes)

        return TtsAudio(
            data=output.getvalue(),
            format="audio/wav",
            sample_rate=sample_rate,
            channels=channels,
            session_id=request.session_id,
        )

    def _synthesis_config(self) -> Any:
        try:
            from piper.config import SynthesisConfig
        except ImportError as exc:
            raise PiperUnavailableError("piper-tts is not installed; install olab-voice[tts-piper]") from exc

        return SynthesisConfig(
            speaker_id=self.speaker_id,
            length_scale=self.length_scale,
            noise_scale=self.noise_scale,
            noise_w_scale=self.noise_w,
        )
