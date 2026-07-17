from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FrequencyChannel:
    id: str
    label: str
    frequency_hz: int
    modulation: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "frequency_hz": self.frequency_hz,
            "modulation": self.modulation,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrequencyChannel:
        return cls(
            id=str(payload["id"]),
            label=str(payload["label"]),
            frequency_hz=int(payload["frequency_hz"]),
            modulation=payload.get("modulation"),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class FrequencyCatalogRange:
    id: str
    label: str
    min_freq_hz: int
    max_freq_hz: int
    default_modulation: str | None = None
    default_bin_size_hz: int | None = None
    channels: list[FrequencyChannel] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "min_freq_hz": self.min_freq_hz,
            "max_freq_hz": self.max_freq_hz,
            "default_modulation": self.default_modulation,
            "default_bin_size_hz": self.default_bin_size_hz,
            "channels": [channel.to_dict() for channel in self.channels],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrequencyCatalogRange:
        return cls(
            id=str(payload["id"]),
            label=str(payload["label"]),
            min_freq_hz=int(payload["min_freq_hz"]),
            max_freq_hz=int(payload["max_freq_hz"]),
            default_modulation=payload.get("default_modulation"),
            default_bin_size_hz=(
                int(payload["default_bin_size_hz"])
                if payload.get("default_bin_size_hz") is not None
                else None
            ),
            channels=[
                FrequencyChannel.from_dict(item) for item in payload.get("channels") or []
            ],
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class FrequencyFavorite:
    frequency_hz: int
    modulation: str
    label: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency_hz": self.frequency_hz,
            "modulation": self.modulation,
            "label": self.label,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrequencyFavorite:
        return cls(
            frequency_hz=int(payload["frequency_hz"]),
            modulation=str(payload.get("modulation") or "NFM"),
            label=payload.get("label"),
            created_at=payload.get("created_at"),
        )


@dataclass(frozen=True, slots=True)
class FrequencyMatch:
    frequency_hz: int
    label: str = ""
    modulation: str | None = None
    range_id: str | None = None
    range_label: str | None = None
    channel_id: str | None = None
    channel_label: str | None = None
    channel_frequency_hz: int | None = None
    favorite_label: str | None = None
    offset_hz: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency_hz": self.frequency_hz,
            "label": self.label,
            "modulation": self.modulation,
            "range_id": self.range_id,
            "range_label": self.range_label,
            "channel_id": self.channel_id,
            "channel_label": self.channel_label,
            "channel_frequency_hz": self.channel_frequency_hz,
            "favorite_label": self.favorite_label,
            "offset_hz": self.offset_hz,
        }
