from __future__ import annotations

import asyncio
from pathlib import Path

from olab_voice.audio.models import AudioBlob
from olab_voice.defaults import default_faster_whisper_model, default_piper_model
from olab_voice.stt.base import TranscriptEvent
from olab_voice.stt.faster_whisper import FasterWhisperTranscriber
from olab_voice.tts.base import TtsAudio, TtsRequest
from olab_voice.tts.piper import PiperSynthesizer


def transcribe_file(
    path: str | Path,
    model_path: str | Path | None = None,
    *,
    language: str | None = "en",
    device: str = "cpu",
    compute_type: str = "int8",
    beam_size: int = 5,
) -> TranscriptEvent:
    audio_path = Path(path).expanduser()
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file does not exist: {audio_path}")

    transcriber = FasterWhisperTranscriber(
        model_path=model_path or default_faster_whisper_model(),
        device=device,
        compute_type=compute_type,
        language=language,
        beam_size=beam_size,
    )
    blob = AudioBlob(
        data=audio_path.read_bytes(),
        format=audio_format_for_path(audio_path),
        source="file",
    )
    return asyncio.run(transcriber.transcribe(blob))


def synthesize_to_wav(
    text: str,
    output_path: str | Path,
    model_path: str | Path | None = None,
    *,
    speaker_id: int | None = None,
    length_scale: float | None = None,
    noise_scale: float | None = None,
    noise_w: float | None = None,
) -> TtsAudio:
    wav_path = Path(output_path).expanduser()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    synthesizer = PiperSynthesizer(
        model_path=model_path or default_piper_model(),
        speaker_id=speaker_id,
        length_scale=length_scale,
        noise_scale=noise_scale,
        noise_w=noise_w,
    )
    audio = asyncio.run(synthesizer.synthesize(TtsRequest(text=text)))
    wav_path.write_bytes(audio.data)
    return audio


def audio_format_for_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".webm":
        return "audio/webm"
    if suffix == ".ogg":
        return "audio/ogg"
    if suffix == ".opus":
        return "audio/ogg;codecs=opus"
    if suffix == ".mp3":
        return "audio/mpeg"
    return "application/octet-stream"
