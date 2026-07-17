from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Sequence

import nats

from olab_voice.audio.models import AudioBlob
from olab_voice.defaults import default_faster_whisper_model
from olab_voice.files import audio_format_for_path
from olab_voice.integrations import OfmVoiceAdapter
from olab_voice.sessions import CommandSession
from olab_voice.stt.faster_whisper import FasterWhisperTranscriber
from olab_voice.transports import (
    GCS_AUDIO_COMMAND,
    GCS_AUDIO_TRANSCRIBED,
    NatsVoiceTransport,
    audio_blob_to_payload,
    pack_payload,
    transcript_event_from_payload,
    unpack_payload,
)


def ofm_adapter_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run olab_voice as an OFM gcs.audio.* NATS adapter.")
    parser.add_argument("--nats", default="nats://127.0.0.1:4222")
    parser.add_argument("--model", type=Path, default=default_faster_whisper_model())
    parser.add_argument("--language", default="en")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    args = parser.parse_args(argv)
    asyncio.run(_run(args))
    return 0


def ofm_publish_audio_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish a local audio file to OFM gcs.audio.command and wait for transcription."
    )
    parser.add_argument("audio_file", type=Path)
    parser.add_argument("--nats", default="nats://127.0.0.1:4222")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--asset-id", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    event = asyncio.run(_publish_audio_and_wait(args))
    print(event.text)
    return 0


async def _publish_audio_and_wait(args: argparse.Namespace):
    audio_path = args.audio_file.expanduser()
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file does not exist: {audio_path}")

    nc = await nats.connect(args.nats)
    future = asyncio.get_running_loop().create_future()

    async def on_transcript(message):
        event = transcript_event_from_payload(unpack_payload(message.data))
        if event.user_id == args.user_id and not future.done():
            future.set_result(event)

    await nc.subscribe(GCS_AUDIO_TRANSCRIBED, cb=on_transcript)
    audio = AudioBlob(
        data=audio_path.read_bytes(),
        format=audio_format_for_path(audio_path),
        source="file",
        user_id=args.user_id,
        asset_id=args.asset_id,
    )
    await nc.publish(GCS_AUDIO_COMMAND, pack_payload(audio_blob_to_payload(audio, style="legacy")))
    try:
        return await asyncio.wait_for(future, timeout=args.timeout)
    finally:
        await nc.drain()


async def _run(args: argparse.Namespace) -> None:
    nc = await nats.connect(args.nats)
    transcriber = FasterWhisperTranscriber(
        model_path=args.model,
        language=args.language or None,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
    )
    adapter = OfmVoiceAdapter(
        session=CommandSession(transcriber=transcriber),
        transport=NatsVoiceTransport(nc),
    )
    await adapter.start()
    print(f"olab_voice OFM adapter listening on {args.nats} subject gcs.audio.command", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await nc.drain()


if __name__ == "__main__":
    raise SystemExit(ofm_adapter_main())
