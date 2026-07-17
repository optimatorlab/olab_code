from __future__ import annotations

from io import BytesIO
from pathlib import Path
import wave

from olab_voice.audio.models import AudioBlob, AudioFrame


def pcm_s16le_to_wav_bytes(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Wrap signed 16-bit little-endian PCM bytes in a WAV container."""

    if channels < 1:
        raise ValueError("channels must be at least 1")
    if sample_rate < 1:
        raise ValueError("sample_rate must be positive")
    if len(pcm) % 2 != 0:
        raise ValueError("pcm_s16le data length must be divisible by 2")

    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def wav_bytes_to_audio_blob(data: bytes, source: str = "file", **metadata) -> AudioBlob:
    """Create an AudioBlob from WAV bytes and populate rate/channel metadata."""

    sample_rate, channels = wav_info(data)
    return AudioBlob(
        data=data,
        format="audio/wav",
        source=source,
        sample_rate=sample_rate,
        channels=channels,
        **metadata,
    )


def audio_frame_to_wav_bytes(frame: AudioFrame) -> bytes:
    if frame.format != "pcm_s16le":
        raise ValueError(f"expected pcm_s16le frame data, got {frame.format!r}")
    return pcm_s16le_to_wav_bytes(frame.data, sample_rate=frame.sample_rate, channels=frame.channels)


def save_wav_bytes(path: str | Path, data: bytes) -> Path:
    wav_path = Path(path).expanduser()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(data)
    return wav_path


def load_wav_blob(path: str | Path, **metadata) -> AudioBlob:
    wav_path = Path(path).expanduser()
    if not wav_path.exists():
        raise FileNotFoundError(f"WAV file does not exist: {wav_path}")
    return wav_bytes_to_audio_blob(wav_path.read_bytes(), source="file", **metadata)


def wav_info(data: bytes) -> tuple[int, int]:
    with wave.open(BytesIO(data), "rb") as wav_file:
        return wav_file.getframerate(), wav_file.getnchannels()


def wav_duration_seconds(data: bytes) -> float:
    with wave.open(BytesIO(data), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        sample_rate = wav_file.getframerate()
    return frame_count / sample_rate
