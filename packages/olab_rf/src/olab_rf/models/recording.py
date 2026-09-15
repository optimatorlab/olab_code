from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from olab_rf.models.tracks import dt_from_iso, dt_to_iso, utc_now


RecordingKind = Literal["normalized", "decoder_stdout", "demod_audio", "iq"]
RecordingStatusValue = Literal["created", "running", "stopped", "error"]
RECORDING_KINDS = {"normalized", "decoder_stdout", "demod_audio", "iq"}
RECORDING_STATUS_VALUES = {"created", "running", "stopped", "error"}


@dataclass(frozen=True, slots=True)
class RecordingRequest:
    """Requested recording contract.

    ``kind="iq"`` is implemented (see ``SessionManager.start_recording``):
    it captures raw ``cu8`` IQ samples to a SigMF ``.sigmf-meta``/
    ``.sigmf-data`` pair derived from ``path`` (see
    ``olab_rf.decoders.sigmf.sigmf_paths``). It requires ``frequency_hz`` and
    ``sample_rate_hz``; ``gain_db``/``device_index`` are optional.
    ``rotate_seconds``/``max_bytes`` are accepted and round-trip normally but
    are not implemented for ``kind="iq"`` — ``start_recording()`` raises
    ``NotImplementedError`` if either is set. ``format`` is accepted but
    ignored for ``kind="iq"`` (the on-disk format is always the SigMF pair).
    Other ``kind`` values remain unimplemented placeholders.
    """

    kind: RecordingKind
    path: str
    format: str | None = None
    include_metadata: bool = True
    rotate_seconds: int | None = None
    max_bytes: int | None = None
    frequency_hz: int | None = None
    sample_rate_hz: int | None = None
    gain_db: float | None = None
    device_index: int = 0

    def __post_init__(self) -> None:
        if self.kind not in RECORDING_KINDS:
            raise ValueError(f"unknown recording kind: {self.kind}")
        if not self.path:
            raise ValueError("path is required")
        if self.rotate_seconds is not None and self.rotate_seconds <= 0:
            raise ValueError("rotate_seconds must be greater than zero")
        if self.max_bytes is not None and self.max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")
        if self.device_index < 0:
            raise ValueError("device_index must be non-negative")
        if self.kind == "iq":
            if self.frequency_hz is None or self.frequency_hz <= 0:
                raise ValueError("frequency_hz must be greater than zero for kind='iq'")
            if self.sample_rate_hz is None or self.sample_rate_hz <= 0:
                raise ValueError("sample_rate_hz must be greater than zero for kind='iq'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "format": self.format,
            "include_metadata": self.include_metadata,
            "rotate_seconds": self.rotate_seconds,
            "max_bytes": self.max_bytes,
            "frequency_hz": self.frequency_hz,
            "sample_rate_hz": self.sample_rate_hz,
            "gain_db": self.gain_db,
            "device_index": self.device_index,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RecordingRequest:
        return cls(
            kind=payload["kind"],
            path=str(payload["path"]),
            format=payload.get("format"),
            include_metadata=bool(payload.get("include_metadata", True)),
            rotate_seconds=(
                int(payload["rotate_seconds"])
                if payload.get("rotate_seconds") is not None
                else None
            ),
            max_bytes=(
                int(payload["max_bytes"]) if payload.get("max_bytes") is not None else None
            ),
            frequency_hz=(
                int(payload["frequency_hz"]) if payload.get("frequency_hz") is not None else None
            ),
            sample_rate_hz=(
                int(payload["sample_rate_hz"])
                if payload.get("sample_rate_hz") is not None
                else None
            ),
            gain_db=(
                float(payload["gain_db"]) if payload.get("gain_db") is not None else None
            ),
            device_index=int(payload.get("device_index", 0)),
        )


@dataclass(frozen=True, slots=True)
class RecordingStatus:
    """Current recording lifecycle state."""

    request: RecordingRequest
    recording_id: str = field(default_factory=lambda: f"recording-{uuid4()}")
    status: RecordingStatusValue = "created"
    started_at: datetime = field(default_factory=utc_now)
    stopped_at: datetime | None = None
    bytes_written: int | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in RECORDING_STATUS_VALUES:
            raise ValueError(f"unknown recording status: {self.status}")
        if self.bytes_written is not None and self.bytes_written < 0:
            raise ValueError("bytes_written must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording_id": self.recording_id,
            "request": self.request.to_dict(),
            "kind": self.request.kind,
            "path": self.request.path,
            "status": self.status,
            "started_at": dt_to_iso(self.started_at),
            "stopped_at": dt_to_iso(self.stopped_at),
            "bytes_written": self.bytes_written,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RecordingStatus:
        return cls(
            recording_id=str(payload.get("recording_id") or f"recording-{uuid4()}"),
            request=RecordingRequest.from_dict(payload["request"]),
            status=payload.get("status", "created"),
            started_at=_coerce_dt(payload.get("started_at")) or utc_now(),
            stopped_at=_coerce_dt(payload.get("stopped_at")),
            bytes_written=(
                int(payload["bytes_written"])
                if payload.get("bytes_written") is not None
                else None
            ),
            error=payload.get("error"),
        )


def _coerce_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return dt_from_iso(value)
    return None
