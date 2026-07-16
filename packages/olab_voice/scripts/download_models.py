#!/usr/bin/env python3
"""Download local olab_voice model fixtures explicitly.

This script is a setup/bootstrap helper. Runtime olab_voice backends do not
silently download models; they consume the local paths prepared here or supplied
through environment variables.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


FASTER_WHISPER_REPO = "Systran/faster-whisper-base.en"
PIPER_REPO = "rhasspy/piper-voices"
PIPER_MODEL_FILE = "en/en_US/lessac/medium/en_US-lessac-medium.onnx"
PIPER_CONFIG_FILE = f"{PIPER_MODEL_FILE}.json"


def default_model_root() -> Path:
    env_root = os.environ.get("OLAB_VOICE_MODEL_DIR")
    if env_root:
        return Path(env_root).expanduser()
    return Path.cwd() / "models" / "olab_voice"


def download_faster_whisper(root: Path) -> Path:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise SystemExit(
            "huggingface-hub is required for model downloads. "
            "Install with: venv/bin/python -m pip install -e '.[models]'"
        ) from exc

    target = root / "faster-whisper" / "base.en"
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=FASTER_WHISPER_REPO,
        local_dir=target,
    )
    return target


def download_piper(root: Path) -> Path:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit(
            "huggingface-hub is required for model downloads. "
            "Install with: venv/bin/python -m pip install -e '.[models]'"
        ) from exc

    target = root / "piper"
    target.mkdir(parents=True, exist_ok=True)
    hf_hub_download(
        repo_id=PIPER_REPO,
        filename=PIPER_MODEL_FILE,
        local_dir=target,
    )
    hf_hub_download(
        repo_id=PIPER_REPO,
        filename=PIPER_CONFIG_FILE,
        local_dir=target,
    )

    nested_model = target / PIPER_MODEL_FILE
    nested_config = target / PIPER_CONFIG_FILE
    flat_model = target / "en_US-lessac-medium.onnx"
    flat_config = target / "en_US-lessac-medium.onnx.json"
    if nested_model.exists() and not flat_model.exists():
        flat_model.write_bytes(nested_model.read_bytes())
    if nested_config.exists() and not flat_config.exists():
        flat_config.write_bytes(nested_config.read_bytes())
    return flat_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-root",
        type=Path,
        default=default_model_root(),
        help="Directory for local models. Defaults to OLAB_VOICE_MODEL_DIR or ./models/olab_voice.",
    )
    parser.add_argument(
        "--only",
        choices=("all", "faster-whisper", "piper"),
        default="all",
        help="Limit downloads to one backend.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.model_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    faster_whisper_path: Path | None = None
    piper_path: Path | None = None
    if args.only in {"all", "faster-whisper"}:
        faster_whisper_path = download_faster_whisper(root)
    if args.only in {"all", "piper"}:
        piper_path = download_piper(root)

    print("Model setup complete.")
    print(f"export OLAB_VOICE_MODEL_DIR={root}")
    if faster_whisper_path is not None:
        print(f"export OLAB_VOICE_FASTER_WHISPER_MODEL={faster_whisper_path}")
    else:
        print(f"export OLAB_VOICE_FASTER_WHISPER_MODEL={root / 'faster-whisper' / 'base.en'}")
    if piper_path is not None:
        print(f"export OLAB_VOICE_PIPER_MODEL={piper_path}")
    else:
        print(f"export OLAB_VOICE_PIPER_MODEL={root / 'piper' / 'en_US-lessac-medium.onnx'}")


if __name__ == "__main__":
    main()
