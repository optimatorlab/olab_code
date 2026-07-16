from __future__ import annotations

import pytest

pytest.importorskip("nats", reason="nats-py is not installed; install olab-voice[nats]")

from olab_voice import ofm_smoke


def test_ofm_adapter_main_passes_parsed_args_to_runner(monkeypatch):
    seen = {}

    async def fake_run(args):
        seen["nats"] = args.nats
        seen["language"] = args.language
        seen["beam_size"] = args.beam_size

    monkeypatch.setattr(ofm_smoke, "_run", fake_run)

    result = ofm_smoke.ofm_adapter_main(
        ["--nats", "nats://example:4222", "--language", "", "--beam-size", "2"]
    )

    assert result == 0
    assert seen == {"nats": "nats://example:4222", "language": "", "beam_size": 2}


def test_ofm_publish_audio_main_passes_parsed_args_to_runner(monkeypatch, tmp_path):
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"RIFF")
    seen = {}

    async def fake_publish(args):
        seen["audio_file"] = args.audio_file
        seen["nats"] = args.nats
        seen["user_id"] = args.user_id
        seen["asset_id"] = args.asset_id
        seen["timeout"] = args.timeout

        class Event:
            text = "hello from nats"

        return Event()

    monkeypatch.setattr(ofm_smoke, "_publish_audio_and_wait", fake_publish)

    result = ofm_smoke.ofm_publish_audio_main(
        [
            str(audio_path),
            "--nats",
            "nats://example:4222",
            "--user-id",
            "7",
            "--asset-id",
            "107",
            "--timeout",
            "3",
        ]
    )

    assert result == 0
    assert seen == {
        "audio_file": audio_path,
        "nats": "nats://example:4222",
        "user_id": 7,
        "asset_id": 107,
        "timeout": 3.0,
    }
