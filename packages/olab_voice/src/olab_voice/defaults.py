from __future__ import annotations

import os
from pathlib import Path


def default_model_root() -> Path:
    env_root = os.environ.get("OLAB_VOICE_MODEL_DIR")
    if env_root:
        return Path(env_root).expanduser()
    return Path.cwd() / "models" / "olab_voice"


def default_faster_whisper_model() -> Path:
    env_model = os.environ.get("OLAB_VOICE_FASTER_WHISPER_MODEL")
    if env_model:
        return Path(env_model).expanduser()
    return default_model_root() / "faster-whisper" / "base.en"


def default_piper_model() -> Path:
    env_model = os.environ.get("OLAB_VOICE_PIPER_MODEL")
    if env_model:
        return Path(env_model).expanduser()
    return default_model_root() / "piper" / "en_US-lessac-medium.onnx"
