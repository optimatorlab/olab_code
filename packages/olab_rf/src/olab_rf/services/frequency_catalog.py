from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

import yaml

from olab_rf.models.frequencies import (
    FrequencyCatalogRange,
    FrequencyFavorite,
    FrequencyMatch,
)


@dataclass(slots=True)
class FrequencyCatalog:
    ranges: list[FrequencyCatalogRange] = field(default_factory=list)
    favorites: list[FrequencyFavorite] = field(default_factory=list)

    @classmethod
    def default(cls) -> FrequencyCatalog:
        payload = yaml.safe_load(
            files("olab_rf.catalog")
            .joinpath("default_frequency_catalog.yaml")
            .read_text(encoding="utf-8")
        ) or {}
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrequencyCatalog:
        catalog_payload = payload.get("frequency_catalog") or payload
        return cls(
            ranges=[
                FrequencyCatalogRange.from_dict(item)
                for item in catalog_payload.get("ranges") or []
            ],
            favorites=[
                FrequencyFavorite.from_dict(item)
                for item in catalog_payload.get("favorites") or []
            ],
        )

    @classmethod
    def merged(
        cls,
        *,
        base: FrequencyCatalog | None = None,
        override_payload: dict[str, Any] | None = None,
    ) -> FrequencyCatalog:
        base = base or cls.default()
        if not override_payload:
            return base
        override = cls.from_dict(override_payload)
        ranges_by_id = {item.id: item for item in base.ranges}
        for item in override.ranges:
            ranges_by_id[item.id] = item
        favorites_by_frequency = {
            item.frequency_hz: item for item in base.favorites
        }
        for item in override.favorites:
            favorites_by_frequency[item.frequency_hz] = item
        return cls(
            ranges=list(ranges_by_id.values()),
            favorites=list(favorites_by_frequency.values()),
        )

    def with_favorites(self, favorites: list[dict[str, object]]) -> FrequencyCatalog:
        merged = {
            favorite.frequency_hz: favorite for favorite in self.favorites
        }
        for payload in favorites:
            if payload.get("frequency_hz") is None:
                continue
            favorite = FrequencyFavorite.from_dict(payload)
            merged[favorite.frequency_hz] = favorite
        return FrequencyCatalog(ranges=list(self.ranges), favorites=list(merged.values()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranges": [item.to_dict() for item in self.ranges],
            "favorites": [item.to_dict() for item in self.favorites],
        }

    def range_by_id(self, range_id: str) -> FrequencyCatalogRange | None:
        for item in self.ranges:
            if item.id == range_id:
                return item
        return None

    def match_frequency(
        self,
        frequency_hz: int,
        *,
        tolerance_hz: int = 2_500,
    ) -> FrequencyMatch:
        favorite = self._favorite_for_frequency(frequency_hz, tolerance_hz=tolerance_hz)
        channel_match = self._channel_for_frequency(frequency_hz, tolerance_hz=tolerance_hz)
        range_match = self._range_for_frequency(frequency_hz)
        channel, offset_hz = channel_match if channel_match else (None, None)
        label = ""
        modulation = None
        if favorite and favorite.label:
            label = favorite.label
            modulation = favorite.modulation
        elif channel:
            label = channel.label
            modulation = channel.modulation
        elif range_match:
            label = range_match.label
            modulation = range_match.default_modulation
        return FrequencyMatch(
            frequency_hz=frequency_hz,
            label=label,
            modulation=modulation,
            range_id=range_match.id if range_match else None,
            range_label=range_match.label if range_match else None,
            channel_id=channel.id if channel else None,
            channel_label=channel.label if channel else None,
            channel_frequency_hz=channel.frequency_hz if channel else None,
            favorite_label=favorite.label if favorite else None,
            offset_hz=offset_hz,
        )

    def _favorite_for_frequency(
        self,
        frequency_hz: int,
        *,
        tolerance_hz: int,
    ) -> FrequencyFavorite | None:
        for favorite in self.favorites:
            if abs(favorite.frequency_hz - frequency_hz) <= tolerance_hz:
                return favorite
        return None

    def _channel_for_frequency(
        self,
        frequency_hz: int,
        *,
        tolerance_hz: int,
    ):
        best = None
        for frequency_range in self.ranges:
            for channel in frequency_range.channels:
                offset_hz = frequency_hz - channel.frequency_hz
                if abs(offset_hz) <= tolerance_hz:
                    if best is None or abs(offset_hz) < abs(best[1]):
                        best = (channel, offset_hz)
        return best

    def _range_for_frequency(self, frequency_hz: int) -> FrequencyCatalogRange | None:
        for frequency_range in self.ranges:
            if frequency_range.min_freq_hz <= frequency_hz <= frequency_range.max_freq_hz:
                return frequency_range
        return None
