from __future__ import annotations

from io import BytesIO
import wave

import pytest

from olab_voice.audio.models import AudioFrame
from olab_voice.audio.wav import (
    audio_frame_to_wav_bytes,
    load_wav_blob,
    pcm_s16le_to_wav_bytes,
    save_wav_bytes,
    wav_bytes_to_audio_blob,
    wav_duration_seconds,
    wav_info,
)


def test_pcm_s16le_to_wav_bytes_wraps_pcm():
    pcm = b"\x00\x00" * 160

    data = pcm_s16le_to_wav_bytes(pcm, sample_rate=16000, channels=1)

    assert data.startswith(b"RIFF")
    with wave.open(BytesIO(data), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() == 160


def test_pcm_s16le_to_wav_bytes_validates_input():
    with pytest.raises(ValueError, match="divisible by 2"):
        pcm_s16le_to_wav_bytes(b"\x00")
    with pytest.raises(ValueError, match="channels"):
        pcm_s16le_to_wav_bytes(b"\x00\x00", channels=0)
    with pytest.raises(ValueError, match="sample_rate"):
        pcm_s16le_to_wav_bytes(b"\x00\x00", sample_rate=0)


def test_audio_frame_to_wav_bytes():
    frame = AudioFrame(data=b"\x00\x00" * 80, sample_rate=8000, channels=1)

    data = audio_frame_to_wav_bytes(frame)

    assert wav_info(data) == (8000, 1)


def test_audio_frame_to_wav_bytes_rejects_non_pcm():
    frame = AudioFrame(data=b"abc", format="audio/wav")

    with pytest.raises(ValueError, match="pcm_s16le"):
        audio_frame_to_wav_bytes(frame)


def test_wav_bytes_to_audio_blob_populates_metadata():
    data = pcm_s16le_to_wav_bytes(b"\x00\x00" * 160, sample_rate=16000, channels=1)

    blob = wav_bytes_to_audio_blob(data, source="file", session_id="session-1")

    assert blob.format == "audio/wav"
    assert blob.sample_rate == 16000
    assert blob.channels == 1
    assert blob.session_id == "session-1"


def test_save_and_load_wav_blob(tmp_path):
    data = pcm_s16le_to_wav_bytes(b"\x00\x00" * 160, sample_rate=16000, channels=1)
    path = save_wav_bytes(tmp_path / "nested" / "test.wav", data)

    blob = load_wav_blob(path, user_id=7)

    assert path.exists()
    assert blob.data == data
    assert blob.user_id == 7
    assert wav_duration_seconds(blob.data) == 0.01
