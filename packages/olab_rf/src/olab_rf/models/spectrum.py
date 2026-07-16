from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from olab_rf.models.tracks import dt_to_iso, utc_now


@dataclass(frozen=True, slots=True)
class FrequencyRange:
    start_hz: int
    stop_hz: int
    bin_hz: int

    def to_dict(self) -> dict[str, object]:
        return {
            "start_hz": self.start_hz,
            "stop_hz": self.stop_hz,
            "bin_hz": self.bin_hz,
        }


@dataclass(slots=True)
class SpectrumBin:
    center_hz: int
    power_db: float

    def to_dict(self) -> dict[str, object]:
        return {"center_hz": self.center_hz, "power_db": self.power_db}


@dataclass(slots=True)
class SpectrumPeak:
    center_hz: int
    power_db: float

    def to_dict(self) -> dict[str, object]:
        return {"center_hz": self.center_hz, "power_db": self.power_db}


@dataclass(slots=True)
class SpectrumSnapshot:
    bins: list[SpectrumBin] = field(default_factory=list)
    peaks: list[SpectrumPeak] = field(default_factory=list)
    captured_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "bins": [item.to_dict() for item in self.bins],
            "peaks": [item.to_dict() for item in self.peaks],
            "captured_at": dt_to_iso(self.captured_at),
        }


@dataclass(slots=True)
class SpectrumEvent:
    center_hz: int
    power_db: float
    noise_floor_db: float
    threshold_db: float
    preset_id: str
    captured_at: datetime = field(default_factory=utc_now)

    @property
    def margin_db(self) -> float:
        return self.power_db - self.noise_floor_db

    def to_dict(self) -> dict[str, object]:
        return {
            "center_hz": self.center_hz,
            "power_db": self.power_db,
            "noise_floor_db": self.noise_floor_db,
            "threshold_db": self.threshold_db,
            "margin_db": self.margin_db,
            "preset_id": self.preset_id,
            "captured_at": dt_to_iso(self.captured_at),
        }
