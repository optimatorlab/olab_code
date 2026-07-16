from __future__ import annotations

import asyncio
import importlib.util
import os
from io import BytesIO
from pathlib import Path
import wave

import pytest

from olab_voice.audio.models import AudioBlob
from olab_voice.stt.faster_whisper import FasterWhisperTranscriber
from olab_voice.tts.base import TtsRequest
from olab_voice.tts.piper import PiperSynthesizer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_ROOT = PROJECT_ROOT / "models" / "olab_voice"
DEFAULT_FASTER_WHISPER_MODEL = DEFAULT_MODEL_ROOT / "faster-whisper" / "base.en"
DEFAULT_PIPER_MODEL = DEFAULT_MODEL_ROOT / "piper" / "en_US-lessac-medium.onnx"


def _model_path_from_env(name: str, default: Path) -> Path | None:
    value = os.environ.get(name)
    path = Path(value).expanduser() if value else default
    if not path.exists():
        return None
    return path


def _silent_wav_bytes(sample_rate: int = 16000, duration_seconds: float = 0.1) -> bytes:
    frames = int(sample_rate * duration_seconds)
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


def test_faster_whisper_constructs_without_loading_model():
    transcriber = FasterWhisperTranscriber(model_path="/definitely/local/model")

    assert transcriber.model_path == Path("/definitely/local/model")


def test_faster_whisper_empty_audio_does_not_require_backend():
    transcriber = FasterWhisperTranscriber(model_path="/definitely/local/model")
    blob = AudioBlob(data=b"", format="audio/wav", session_id="session-1")

    event = asyncio.run(transcriber.transcribe(blob))

    assert event.text == ""
    assert event.session_id == "session-1"


def test_faster_whisper_transcribes_local_fixture_when_configured():
    model_path = _model_path_from_env(
        "OLAB_VOICE_FASTER_WHISPER_MODEL", DEFAULT_FASTER_WHISPER_MODEL
    )
    if model_path is None:
        pytest.skip("Faster-Whisper model is not configured; run scripts/download_models.py")
    if importlib.util.find_spec("faster_whisper") is None:
        pytest.skip("faster-whisper is not installed")

    transcriber = FasterWhisperTranscriber(model_path=model_path)
    blob = AudioBlob(data=_silent_wav_bytes(), format="audio/wav", session_id="session-1")

    event = asyncio.run(transcriber.transcribe(blob))

    assert event.session_id == "session-1"
    assert event.type == "transcript.segment_final"


def test_piper_constructs_without_loading_voice():
    synthesizer = PiperSynthesizer(model_path="/definitely/local/model.onnx")

    assert synthesizer.model_path == Path("/definitely/local/model.onnx")




class _FakePiperChunk:
    sample_rate = 16000
    sample_width = 2
    sample_channels = 1
    audio_int16_bytes = b"\x00\x00" * 160


class _FakePiperVoice:
    def synthesize(self, text, syn_config=None):
        assert text == "107 is listening"
        assert syn_config is not None
        return [_FakePiperChunk()]


def test_piper_synthesizer_writes_wav_from_chunks(monkeypatch):
    synthesizer = PiperSynthesizer(model_path="/definitely/local/model.onnx")
    synthesizer._voice = _FakePiperVoice()
    # Stub out the real Piper-backed synthesis config (PiperSynthesizer is a
    # slots dataclass, so this must patch the class, not the instance) so
    # this test exercises PiperSynthesizer.synthesize()'s own orchestration
    # without requiring piper-tts to be installed (an opt-in extra).
    monkeypatch.setattr(PiperSynthesizer, "_synthesis_config", lambda self: object())

    audio = asyncio.run(
        synthesizer.synthesize(TtsRequest(text="107 is listening", session_id="session-1"))
    )

    assert audio.format == "audio/wav"
    assert audio.data.startswith(b"RIFF")
    assert audio.sample_rate == 16000
    assert audio.channels == 1
    assert audio.session_id == "session-1"


def test_piper_synthesizes_wav_when_configured():
    model_path = _model_path_from_env("OLAB_VOICE_PIPER_MODEL", DEFAULT_PIPER_MODEL)
    if model_path is None:
        pytest.skip("Piper model is not configured; run scripts/download_models.py")
    if importlib.util.find_spec("piper") is None:
        pytest.skip("piper-tts is not installed")

    synthesizer = PiperSynthesizer(model_path=model_path)
    audio = asyncio.run(synthesizer.synthesize(TtsRequest(text="107 is listening")))

    assert audio.format == "audio/wav"
    assert audio.data.startswith(b"RIFF")
    assert audio.channels == 1
