from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from olab_rf.models.tracks import dt_from_iso, dt_to_iso, utc_now


FrequencyScanBackend = Literal["rtl_power", "rtl_sdr_iq"]
FrequencyScanStatusValue = Literal["created", "running", "complete", "stopped", "error"]


@dataclass(frozen=True, slots=True)
class FrequencyScanRequest:
    """Parameters for a bounded frequency discovery scan.

    Frequencies are expressed in Hz. The scan is intended to be started with
    ``SessionManager.start_frequency_scan`` or
    ``SessionManager.capture_frequency_baseline`` and then advanced with
    ``SessionManager.poll``.
    """

    min_freq_hz: int
    max_freq_hz: int
    bin_size_hz: int
    duration_sec: float
    channel_frequencies_hz: list[int] = field(default_factory=list)
    channel_width_hz: int | None = None
    backend: FrequencyScanBackend = "rtl_power"
    gain_db: float | None = None
    sample_rate_hz: int | None = None
    resume_previous: bool = False

    def __post_init__(self) -> None:
        if self.min_freq_hz <= 0:
            raise ValueError("min_freq_hz must be greater than zero")
        if self.max_freq_hz <= self.min_freq_hz:
            raise ValueError("max_freq_hz must be greater than min_freq_hz")
        if self.bin_size_hz <= 0:
            raise ValueError("bin_size_hz must be greater than zero")
        if self.duration_sec <= 0:
            raise ValueError("duration_sec must be greater than zero")
        if self.channel_width_hz is not None and self.channel_width_hz <= 0:
            raise ValueError("channel_width_hz must be greater than zero")
        if self.backend not in {"rtl_power", "rtl_sdr_iq"}:
            raise ValueError(f"unknown frequency scan backend: {self.backend}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_freq_hz": self.min_freq_hz,
            "max_freq_hz": self.max_freq_hz,
            "bin_size_hz": self.bin_size_hz,
            "duration_sec": self.duration_sec,
            "channel_frequencies_hz": list(self.channel_frequencies_hz),
            "channel_width_hz": self.channel_width_hz,
            "backend": self.backend,
            "gain_db": self.gain_db,
            "sample_rate_hz": self.sample_rate_hz,
            "resume_previous": self.resume_previous,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrequencyScanRequest:
        return cls(
            min_freq_hz=int(payload["min_freq_hz"]),
            max_freq_hz=int(payload["max_freq_hz"]),
            bin_size_hz=int(payload["bin_size_hz"]),
            duration_sec=float(payload["duration_sec"]),
            channel_frequencies_hz=[
                int(item) for item in payload.get("channel_frequencies_hz") or []
            ],
            channel_width_hz=(
                int(payload["channel_width_hz"])
                if payload.get("channel_width_hz") is not None
                else None
            ),
            backend=payload.get("backend", "rtl_power"),
            gain_db=(
                float(payload["gain_db"]) if payload.get("gain_db") is not None else None
            ),
            sample_rate_hz=(
                int(payload["sample_rate_hz"])
                if payload.get("sample_rate_hz") is not None
                else None
            ),
            resume_previous=bool(payload.get("resume_previous", False)),
        )


@dataclass(frozen=True, slots=True)
class FrequencyCandidate:
    """Ranked signal candidate from a frequency discovery scan.

    ``frequency_hz`` is the observed frequency reported by the backend. For the
    current quick ``rtl_power`` backend this is a bin center, not necessarily an
    exact carrier frequency. When the observed bin matches a catalog channel,
    ``matched_frequency_hz`` contains that known channel frequency and
    ``frequency_offset_hz`` is observed minus matched.
    """

    frequency_hz: int
    power_db: float
    baseline_power_db: float | None = None
    margin_db: float | None = None
    sweeps_seen: int = 1
    label: str = ""
    modulation: str | None = None
    range_id: str | None = None
    channel_id: str | None = None
    matched_frequency_hz: int | None = None
    frequency_offset_hz: int | None = None
    source: str = "bin"

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency_hz": self.frequency_hz,
            "observed_frequency_hz": self.frequency_hz,
            "power_db": self.power_db,
            "baseline_power_db": self.baseline_power_db,
            "margin_db": self.margin_db,
            "sweeps_seen": self.sweeps_seen,
            "label": self.label,
            "modulation": self.modulation,
            "range_id": self.range_id,
            "channel_id": self.channel_id,
            "matched_frequency_hz": self.matched_frequency_hz,
            "frequency_offset_hz": self.frequency_offset_hz,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrequencyCandidate:
        return cls(
            frequency_hz=int(payload["frequency_hz"]),
            power_db=float(payload["power_db"]),
            baseline_power_db=(
                float(payload["baseline_power_db"])
                if payload.get("baseline_power_db") is not None
                else None
            ),
            margin_db=(
                float(payload["margin_db"]) if payload.get("margin_db") is not None else None
            ),
            sweeps_seen=int(payload.get("sweeps_seen", 1)),
            label=str(payload.get("label") or ""),
            modulation=payload.get("modulation"),
            range_id=payload.get("range_id"),
            channel_id=payload.get("channel_id"),
            matched_frequency_hz=(
                int(payload["matched_frequency_hz"])
                if payload.get("matched_frequency_hz") is not None
                else None
            ),
            frequency_offset_hz=(
                int(payload["frequency_offset_hz"])
                if payload.get("frequency_offset_hz") is not None
                else None
            ),
            source=str(payload.get("source") or "bin"),
        )


@dataclass(frozen=True, slots=True)
class FrequencyBaseline:
    baseline_id: str
    request: FrequencyScanRequest
    powers_by_frequency_hz: dict[int, float]
    captured_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "request": self.request.to_dict(),
            "powers_by_frequency_hz": {
                str(frequency_hz): power_db
                for frequency_hz, power_db in self.powers_by_frequency_hz.items()
            },
            "captured_at": dt_to_iso(self.captured_at),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrequencyBaseline:
        return cls(
            baseline_id=str(payload["baseline_id"]),
            request=FrequencyScanRequest.from_dict(payload["request"]),
            powers_by_frequency_hz={
                int(frequency_hz): float(power_db)
                for frequency_hz, power_db in (
                    payload.get("powers_by_frequency_hz") or {}
                ).items()
            },
            captured_at=_coerce_dt(payload.get("captured_at")) or utc_now(),
        )


@dataclass(frozen=True, slots=True)
class FrequencyScanStatus:
    scan_id: str
    request: FrequencyScanRequest
    status: FrequencyScanStatusValue = "created"
    session_id: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    stopped_at: datetime | None = None
    elapsed_sec: float = 0.0
    progress: float = 0.0
    sweeps_completed: int = 0
    candidates: list[FrequencyCandidate] = field(default_factory=list)
    baseline_id: str | None = None
    error: str | None = None

    @property
    def best_candidate(self) -> FrequencyCandidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def matched_candidates(self) -> list[FrequencyCandidate]:
        """Return catalog-matched candidates ranked by margin or power."""
        return sorted(
            (
                candidate
                for candidate in self.candidates
                if candidate.matched_frequency_hz is not None
            ),
            key=lambda candidate: (
                candidate.margin_db
                if candidate.margin_db is not None
                else candidate.power_db
            ),
            reverse=True,
        )

    @property
    def best_matched_candidate(self) -> FrequencyCandidate | None:
        return self.matched_candidates[0] if self.matched_candidates else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "request": self.request.to_dict(),
            "status": self.status,
            "session_id": self.session_id,
            "started_at": dt_to_iso(self.started_at),
            "stopped_at": dt_to_iso(self.stopped_at),
            "elapsed_sec": self.elapsed_sec,
            "progress": self.progress,
            "sweeps_completed": self.sweeps_completed,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "matched_candidates": [
                candidate.to_dict() for candidate in self.matched_candidates
            ],
            "best_candidate": (
                self.best_candidate.to_dict() if self.best_candidate else None
            ),
            "best_matched_candidate": (
                self.best_matched_candidate.to_dict()
                if self.best_matched_candidate
                else None
            ),
            "baseline_id": self.baseline_id,
            "error": self.error,
        }

    @classmethod
    def created(
        cls,
        *,
        request: FrequencyScanRequest,
        session_id: str | None = None,
        baseline_id: str | None = None,
    ) -> FrequencyScanStatus:
        return cls(
            scan_id=f"scan-{uuid4()}",
            request=request,
            status="created",
            session_id=session_id,
            baseline_id=baseline_id,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrequencyScanStatus:
        return cls(
            scan_id=str(payload["scan_id"]),
            request=FrequencyScanRequest.from_dict(payload["request"]),
            status=payload.get("status", "created"),
            session_id=payload.get("session_id"),
            started_at=_coerce_dt(payload.get("started_at")) or utc_now(),
            stopped_at=_coerce_dt(payload.get("stopped_at")),
            elapsed_sec=float(payload.get("elapsed_sec", 0.0)),
            progress=float(payload.get("progress", 0.0)),
            sweeps_completed=int(payload.get("sweeps_completed", 0)),
            candidates=[
                FrequencyCandidate.from_dict(item)
                for item in payload.get("candidates") or []
            ],
            baseline_id=payload.get("baseline_id"),
            error=payload.get("error"),
        )


def _coerce_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return dt_from_iso(value)
    return None
