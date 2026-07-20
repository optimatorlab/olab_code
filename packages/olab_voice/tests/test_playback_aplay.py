from __future__ import annotations

import asyncio

import pytest

from olab_voice.playback.aplay import AplayPlaybackSink, PlaybackUnavailableError
from olab_voice.tts.base import TtsAudio


class _FakeProcess:
    def __init__(self, args, *, returncode: int = 0, stderr: bytes = b"", hang: bool = False) -> None:
        self.args = args
        self.returncode = returncode
        self._stderr = stderr
        self._hang = hang
        self.received_stdin: bytes | None = None
        self.killed = False
        self.waited = False

    async def communicate(self, input: bytes | None = None):
        self.received_stdin = input
        if self._hang:
            await asyncio.Event().wait()
        return b"", self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        self.waited = True


def _patch_which_found(monkeypatch) -> None:
    monkeypatch.setattr("olab_voice.playback.aplay.shutil.which", lambda executable: f"/usr/bin/{executable}")


def test_play_invokes_aplay_without_shell_and_pipes_wav_stdin(monkeypatch) -> None:
    _patch_which_found(monkeypatch)
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        process = _FakeProcess(args)
        captured["process"] = process
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    sink = AplayPlaybackSink(device="hw:1,0")
    audio = TtsAudio(data=b"RIFFsomewavbytes", format="audio/wav")

    asyncio.run(sink.play(audio))

    process = captured["process"]
    assert process.args == ("aplay", "-q", "-t", "wav", "-D", "hw:1,0", "-")
    assert process.received_stdin == b"RIFFsomewavbytes"
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE


def test_play_without_device_omits_device_args(monkeypatch) -> None:
    _patch_which_found(monkeypatch)
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        process = _FakeProcess(args)
        captured["process"] = process
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    sink = AplayPlaybackSink()
    asyncio.run(sink.play(TtsAudio(data=b"RIFF", format="audio/wav")))

    assert captured["process"].args == ("aplay", "-q", "-t", "wav", "-")


def test_play_raises_on_nonzero_exit(monkeypatch) -> None:
    _patch_which_found(monkeypatch)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProcess(args, returncode=1, stderr=b"device or resource busy")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    sink = AplayPlaybackSink()
    with pytest.raises(RuntimeError, match="device or resource busy"):
        asyncio.run(sink.play(TtsAudio(data=b"RIFF", format="audio/wav")))


def test_play_raises_when_executable_missing(monkeypatch) -> None:
    monkeypatch.setattr("olab_voice.playback.aplay.shutil.which", lambda executable: None)

    sink = AplayPlaybackSink()
    with pytest.raises(PlaybackUnavailableError):
        asyncio.run(sink.play(TtsAudio(data=b"RIFF", format="audio/wav")))


def test_play_kills_process_on_cancellation(monkeypatch) -> None:
    _patch_which_found(monkeypatch)
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        process = _FakeProcess(args, hang=True)
        captured["process"] = process
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    async def scenario() -> None:
        sink = AplayPlaybackSink()
        task = asyncio.ensure_future(sink.play(TtsAudio(data=b"RIFF", format="audio/wav")))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert captured["process"].killed is True
    assert captured["process"].waited is True


def test_play_rejects_non_wav_audio(monkeypatch) -> None:
    _patch_which_found(monkeypatch)
    sink = AplayPlaybackSink()

    with pytest.raises(ValueError):
        asyncio.run(sink.play(TtsAudio(data=b"raw", format="audio/pcm")))
