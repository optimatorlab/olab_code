from __future__ import annotations

import subprocess
import select
import os
import signal
from dataclasses import dataclass
from typing import IO
from pathlib import Path


@dataclass(slots=True)
class DecoderProcess:
    command: list[str]
    shell: bool = False
    binary_stdout: bool = False
    cwd: str | Path | None = None
    process: subprocess.Popen[str] | subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        command: list[str] | str = self.command
        if self.shell:
            command = " ".join(self.command)
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not self.binary_stdout,
            errors=None if self.binary_stdout else "replace",
            shell=self.shell,
            start_new_session=self.shell,
            cwd=self.cwd,
        )

    def stop(self, timeout_s: float = 3.0) -> None:
        if not self.process or self.process.poll() is not None:
            return
        if self.shell:
            os.killpg(self.process.pid, signal.SIGTERM)
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            if self.shell:
                os.killpg(self.process.pid, signal.SIGKILL)
            else:
                self.process.kill()
            self.process.wait(timeout=timeout_s)

    def is_running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    @property
    def stdout(self) -> IO[str] | None:
        return self.process.stdout if self.process else None

    @property
    def stderr(self) -> IO[str] | None:
        return self.process.stderr if self.process else None

    def read_stdout_lines(self, limit: int = 100) -> list[str]:
        return self._read_lines(self.stdout, limit=limit)

    def read_stderr_lines(self, limit: int = 100) -> list[str]:
        return self._read_lines(self.stderr, limit=limit)

    def read_stdout_bytes(self, limit: int = 65_536) -> bytes:
        stream = self.stdout
        if stream is None:
            return b""
        ready, _, _ = select.select([stream], [], [], 0)
        if not ready:
            return b""
        return os.read(stream.fileno(), limit)

    def _read_lines(self, stream: IO[str] | None, limit: int = 100) -> list[str]:
        if stream is None:
            return []
        lines: list[str] = []
        while len(lines) < limit:
            ready, _, _ = select.select([stream], [], [], 0)
            if not ready:
                break
            line = stream.readline()
            if not line:
                break
            if isinstance(line, bytes):
                lines.append(line.decode(errors="replace").rstrip("\r\n"))
            else:
                lines.append(line.rstrip("\r\n"))
        return lines
