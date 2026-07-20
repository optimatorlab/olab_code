"""Linux speaker playback backend using the `aplay` executable.

`AplayPlaybackSink` never builds a shell command: it always invokes `aplay`
via `asyncio.create_subprocess_exec` with an explicit argument list and pipes
WAV bytes through stdin, so request text, model paths, and device names can
never be interpreted by a shell.
"""

from __future__ import annotations

import asyncio
import shutil

from olab_voice.tts.base import TtsAudio


class PlaybackUnavailableError(RuntimeError):
    """Raised when the configured playback executable is not on PATH."""


class AplayPlaybackSink:
    """Plays WAV audio on Linux by piping it into `aplay`'s stdin."""

    def __init__(self, device: str | None = None, executable: str = "aplay") -> None:
        self.device = device
        self.executable = executable

    async def play(self, audio: TtsAudio) -> None:
        if audio.format != "audio/wav":
            raise ValueError(f"AplayPlaybackSink only supports audio/wav, got {audio.format!r}")
        if shutil.which(self.executable) is None:
            raise PlaybackUnavailableError(f"{self.executable!r} executable not found on PATH")

        args = [self.executable, "-q", "-t", "wav"]
        if self.device:
            args += ["-D", self.device]
        args.append("-")

        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await process.communicate(audio.data)
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise

        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip() if stderr else ""
            raise RuntimeError(
                f"{self.executable} exited with code {process.returncode}: {message}"
            )
