from __future__ import annotations

import os
import time

from olab_rf.decoders.process import DecoderProcess


def test_process_reader_replaces_invalid_utf8(tmp_path):
    script = tmp_path / "invalid-stderr"
    script.write_bytes(b"#!/bin/sh\nprintf '\\231bad\\n' >&2\nsleep 30\n")
    os.chmod(script, 0o755)
    process = DecoderProcess(command=[str(script)])
    process.start()

    try:
        lines = []
        for _ in range(10):
            lines = process.read_stderr_lines()
            if lines:
                break
            time.sleep(0.05)
        assert lines
        assert "bad" in lines[0]
    finally:
        process.stop()


def test_shell_process_starts_and_stops():
    process = DecoderProcess(command=["sleep 30"], shell=True)
    process.start()

    assert process.is_running() is True
    process.stop()
    assert process.is_running() is False
