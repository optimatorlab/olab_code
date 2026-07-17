from __future__ import annotations

from typing import Any


def pack_payload(payload: dict[str, Any]) -> bytes:
    try:
        import msgpack
    except ImportError as exc:
        raise RuntimeError("msgpack is not installed; install olab-voice[nats]") from exc

    return msgpack.packb(payload, use_bin_type=True)


def unpack_payload(data: bytes) -> dict[str, Any]:
    try:
        import msgpack
    except ImportError as exc:
        raise RuntimeError("msgpack is not installed; install olab-voice[nats]") from exc

    payload = msgpack.unpackb(data, raw=False)
    if not isinstance(payload, dict):
        raise ValueError("MessagePack payload must decode to a dict")
    return payload
