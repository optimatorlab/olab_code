from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import wave

from olab_voice import defaults, files
from olab_voice.stt.base import TranscriptEvent
from olab_voice.tts.base import TtsAudio


def _silent_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 160)


def _tiny_wav_bytes() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 160)
    return output.getvalue()


@dataclass(slots=True)
class _FakeTranscriber:
    model_path: Path
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = "en"
    beam_size: int = 5

    async def transcribe(self, audio):
        assert audio.source == "file"
        assert audio.format == "audio/wav"
        assert audio.data.startswith(b"RIFF")
        return TranscriptEvent(text="hello local voice", session_id=audio.session_id)


@dataclass(slots=True)
class _FakeSynthesizer:
    model_path: Path
    speaker_id: int | None = None
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None

    async def synthesize(self, request):
        assert request.text == "107 is listening"
        return TtsAudio(data=_tiny_wav_bytes(), format="audio/wav", sample_rate=16000, channels=1)


def test_default_model_paths_use_environment(monkeypatch):
    monkeypatch.setenv("OLAB_VOICE_MODEL_DIR", "/tmp/ub-models")
    monkeypatch.setenv("OLAB_VOICE_FASTER_WHISPER_MODEL", "/tmp/fw")
    monkeypatch.setenv("OLAB_VOICE_PIPER_MODEL", "/tmp/piper.onnx")

    assert defaults.default_model_root() == Path("/tmp/ub-models")
    assert defaults.default_faster_whisper_model() == Path("/tmp/fw")
    assert defaults.default_piper_model() == Path("/tmp/piper.onnx")


def test_audio_format_for_path():
    assert files.audio_format_for_path("input.wav") == "audio/wav"
    assert files.audio_format_for_path("input.webm") == "audio/webm"
    assert files.audio_format_for_path("input.ogg") == "audio/ogg"
    assert files.audio_format_for_path("input.opus") == "audio/ogg;codecs=opus"
    assert files.audio_format_for_path("input.mp3") == "audio/mpeg"
    assert files.audio_format_for_path("input.bin") == "application/octet-stream"


def test_transcribe_file_uses_backend(monkeypatch, tmp_path):
    audio_path = tmp_path / "input.wav"
    _silent_wav(audio_path)
    monkeypatch.setattr(files, "FasterWhisperTranscriber", _FakeTranscriber)

    event = files.transcribe_file(audio_path, model_path=Path("/tmp/model"))

    assert event.text == "hello local voice"


def test_synthesize_to_wav_uses_backend(monkeypatch, tmp_path):
    output_path = tmp_path / "out.wav"
    monkeypatch.setattr(files, "PiperSynthesizer", _FakeSynthesizer)

    audio = files.synthesize_to_wav(
        "107 is listening", output_path, model_path=Path("/tmp/model.onnx")
    )

    assert audio.format == "audio/wav"
    assert output_path.read_bytes().startswith(b"RIFF")
