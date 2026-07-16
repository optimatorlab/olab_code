from __future__ import annotations

from olab_rf.decoders.process import DecoderProcess


class SdrTrunkBackend:
    """GUI launcher only; SDRTrunk retains ownership of decoding and playlists."""

    def __init__(self, *, launcher_path: str, working_directory: str | None = None):
        self.command = [launcher_path]
        self.process = DecoderProcess(command=self.command, cwd=working_directory)

    def start(self) -> None:
        self.process.start()

    def stop(self) -> None:
        self.process.stop()

    def is_running(self) -> bool:
        return self.process.is_running()

    def stderr_lines(self) -> list[str]:
        return self.process.read_stderr_lines()
