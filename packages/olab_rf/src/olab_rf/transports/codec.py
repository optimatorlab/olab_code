from __future__ import annotations

from typing import Any


def pack_payload(payload: dict[str, Any]) -> bytes:
    try:
        import msgpack
    except ImportError as exc:  # pragma: no cover - exercised when optional extra is absent.
        raise RuntimeError("Install olab_rf with the nats extra to enable MessagePack") from exc
    return msgpack.packb(payload, use_bin_type=True)


def unpack_payload(data: bytes) -> dict[str, Any]:
    try:
        import msgpack
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install olab_rf with the nats extra to enable MessagePack") from exc
    return msgpack.unpackb(data, raw=False)
