from __future__ import annotations

from io import BytesIO
from pathlib import Path
import wave

import pytest

from olab_voice import cli
from olab_voice.stt.base import TranscriptEvent
from olab_voice.tts.base import TtsAudio


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_ROOT = PROJECT_ROOT / "models" / "olab_voice"
DEFAULT_FASTER_WHISPER_MODEL = DEFAULT_MODEL_ROOT / "faster-whisper" / "base.en"
DEFAULT_PIPER_MODEL = DEFAULT_MODEL_ROOT / "piper" / "en_US-lessac-medium.onnx"


def _silent_wav(path: Path, sample_rate: int = 16000, duration_seconds: float = 0.1) -> None:
    frames = int(sample_rate * duration_seconds)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frames)


def _tiny_wav_bytes() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 160)
    return output.getvalue()


def _fake_transcribe_file(path, **kwargs):
    assert Path(path).suffix == ".wav"
    assert kwargs["model_path"] == Path("/tmp/model")
    return TranscriptEvent(text="hello local voice")


def _fake_synthesize_to_wav(text, output_path, **kwargs):
    assert text == "107 is listening"
    assert kwargs["model_path"] == Path("/tmp/model.onnx")
    audio = TtsAudio(data=_tiny_wav_bytes(), format="audio/wav", sample_rate=16000, channels=1)
    Path(output_path).write_bytes(audio.data)
    return audio


def test_transcribe_cli_prints_plain_text(monkeypatch, tmp_path, capsys):
    audio_path = tmp_path / "input.wav"
    _silent_wav(audio_path)
    monkeypatch.setattr(cli, "transcribe_file", _fake_transcribe_file)

    result = cli.transcribe_main([str(audio_path), "--model", "/tmp/model"])

    assert result == 0
    assert capsys.readouterr().out.strip() == "hello local voice"


def test_transcribe_cli_prints_json(monkeypatch, tmp_path, capsys):
    audio_path = tmp_path / "input.wav"
    _silent_wav(audio_path)
    monkeypatch.setattr(cli, "transcribe_file", _fake_transcribe_file)

    result = cli.transcribe_main([str(audio_path), "--model", "/tmp/model", "--json"])

    assert result == 0
    assert '"text": "hello local voice"' in capsys.readouterr().out


def test_synthesize_cli_writes_wav(monkeypatch, tmp_path, capsys):
    output_path = tmp_path / "out.wav"
    monkeypatch.setattr(cli, "synthesize_to_wav", _fake_synthesize_to_wav)

    result = cli.synthesize_main(["107 is listening", str(output_path), "--model", "/tmp/model.onnx"])

    assert result == 0
    assert output_path.read_bytes().startswith(b"RIFF")
    assert capsys.readouterr().out.strip() == str(output_path)


def test_synthesize_cli_real_model_when_available(tmp_path):
    if not DEFAULT_PIPER_MODEL.exists():
        pytest.skip("Piper model is not configured; run scripts/download_models.py")

    output_path = tmp_path / "out.wav"
    result = cli.synthesize_main(
        ["107 is listening", str(output_path), "--model", str(DEFAULT_PIPER_MODEL)]
    )

    assert result == 0
    assert output_path.read_bytes().startswith(b"RIFF")


def test_transcribe_cli_real_model_when_available(tmp_path, capsys):
    if not DEFAULT_FASTER_WHISPER_MODEL.exists():
        pytest.skip("Faster-Whisper model is not configured; run scripts/download_models.py")

    audio_path = tmp_path / "input.wav"
    _silent_wav(audio_path)
    result = cli.transcribe_main(
        [str(audio_path), "--model", str(DEFAULT_FASTER_WHISPER_MODEL), "--json"]
    )

    assert result == 0
    assert '"type": "transcript.segment_final"' in capsys.readouterr().out
