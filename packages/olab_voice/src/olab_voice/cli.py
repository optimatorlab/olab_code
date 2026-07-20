from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Sequence

from olab_voice.defaults import default_faster_whisper_model, default_piper_model
from olab_voice.files import synthesize_to_wav, transcribe_file
from olab_voice.playback import AplayPlaybackSink


def transcribe_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Transcribe a local audio file with Faster-Whisper.")
    parser.add_argument("audio_file", type=Path, help="Path to a local WAV/WebM/Ogg/MP3 audio file.")
    parser.add_argument("--model", type=Path, default=default_faster_whisper_model())
    parser.add_argument("--language", default="en", help="Language code, or empty for auto-detect.")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="Print the full transcript event as JSON.")
    args = parser.parse_args(argv)

    language = args.language or None
    event = transcribe_file(
        args.audio_file,
        model_path=args.model,
        language=language,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
    )

    if args.json:
        print(json.dumps(_jsonable(event.to_dict()), sort_keys=True))
    else:
        print(event.text)
    return 0


def synthesize_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthesize local speech with Piper and write a WAV file.")
    parser.add_argument("text", help="Text to synthesize.")
    parser.add_argument("output_wav", type=Path, help="Output WAV path.")
    parser.add_argument("--model", type=Path, default=default_piper_model())
    parser.add_argument("--speaker-id", type=int)
    parser.add_argument("--length-scale", type=float)
    parser.add_argument("--noise-scale", type=float)
    parser.add_argument("--noise-w", type=float)
    parser.add_argument(
        "--play", action="store_true", help="Play the synthesized audio locally with aplay after writing it."
    )
    parser.add_argument("--device", help="ALSA device name to pass to aplay (only with --play).")
    args = parser.parse_args(argv)

    audio = synthesize_to_wav(
        args.text,
        args.output_wav,
        model_path=args.model,
        speaker_id=args.speaker_id,
        length_scale=args.length_scale,
        noise_scale=args.noise_scale,
        noise_w=args.noise_w,
    )
    print(args.output_wav.expanduser())

    if args.play:
        sink = AplayPlaybackSink(device=args.device)
        asyncio.run(sink.play(audio))
    return 0


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if not isinstance(value, bytes)}


if __name__ == "__main__":
    raise SystemExit(transcribe_main())
