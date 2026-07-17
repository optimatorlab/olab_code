from __future__ import annotations

from dataclasses import dataclass

from olab_rf.services.frequency_catalog import FrequencyCatalog


@dataclass(frozen=True, slots=True)
class FrequencyRangeScanPlan:
    """Resolved frequency range scan parameters.

    The plan is backend-neutral: callers can pass it to
    ``SessionManager.start_frequency_scan`` with either ``rtl_power`` or a
    channelized backend such as ``rtl_sdr_iq``.
    """

    min_freq_hz: int
    max_freq_hz: int
    channel_frequencies_hz: list[int]
    channel_width_hz: int
    range_id: str | None = None

    @property
    def min_hz(self) -> int:
        """Compatibility alias for older IQ range example callers."""
        return self.min_freq_hz

    @property
    def max_hz(self) -> int:
        """Compatibility alias for older IQ range example callers."""
        return self.max_freq_hz


def build_frequency_range_scan_plan(
    *,
    catalog: FrequencyCatalog,
    range_id: str | None = "frs_gmrs",
    min_freq_hz: int | None = None,
    max_freq_hz: int | None = None,
    step_hz: int | None = None,
    channel_width_hz: int | None = None,
    channel_frequencies_hz: list[int] | None = None,
) -> FrequencyRangeScanPlan:
    """Resolve catalog or arbitrary range inputs into scan-ready frequencies."""
    _validate_optional_positive(step_hz, name="step_hz")
    _validate_optional_positive(channel_width_hz, name="channel_width_hz")

    if channel_frequencies_hz:
        channels = sorted(set(channel_frequencies_hz))
        _validate_positive_frequencies(channels, name="channel_frequencies_hz")
        width_hz = _positive_width(_first_not_none(channel_width_hz, step_hz, 12_500))
        return FrequencyRangeScanPlan(
            min_freq_hz=min(channels) - width_hz,
            max_freq_hz=max(channels) + width_hz,
            channel_frequencies_hz=channels,
            channel_width_hz=width_hz,
            range_id=range_id,
        )

    if (min_freq_hz is None) != (max_freq_hz is None):
        raise ValueError("min_freq_hz and max_freq_hz must be provided together")
    if min_freq_hz is not None and max_freq_hz is not None:
        return _build_grid_plan(
            min_freq_hz=min_freq_hz,
            max_freq_hz=max_freq_hz,
            step_hz=step_hz,
            channel_width_hz=channel_width_hz,
            range_id=range_id,
        )

    if not range_id:
        raise ValueError("range_id is required when min_freq_hz and max_freq_hz are omitted")
    frequency_range = catalog.range_by_id(range_id)
    if frequency_range is None:
        raise ValueError(f"range id not found: {range_id}")

    width_hz = _positive_width(
        _first_not_none(channel_width_hz, frequency_range.default_bin_size_hz, step_hz, 12_500)
    )
    channels = [channel.frequency_hz for channel in frequency_range.channels]
    if not channels:
        grid_step_hz = _positive_width(_first_not_none(step_hz, width_hz), name="step_hz")
        channels = list(
            range(frequency_range.min_freq_hz, frequency_range.max_freq_hz + 1, grid_step_hz)
        )
    return FrequencyRangeScanPlan(
        min_freq_hz=frequency_range.min_freq_hz,
        max_freq_hz=frequency_range.max_freq_hz,
        channel_frequencies_hz=channels,
        channel_width_hz=width_hz,
        range_id=range_id,
    )


def _build_grid_plan(
    *,
    min_freq_hz: int,
    max_freq_hz: int,
    step_hz: int | None,
    channel_width_hz: int | None,
    range_id: str | None,
) -> FrequencyRangeScanPlan:
    if min_freq_hz <= 0:
        raise ValueError("min_freq_hz must be greater than zero")
    if max_freq_hz <= min_freq_hz:
        raise ValueError("max_freq_hz must be greater than min_freq_hz")
    width_hz = _positive_width(_first_not_none(channel_width_hz, step_hz, 12_500))
    grid_step_hz = _positive_width(_first_not_none(step_hz, width_hz), name="step_hz")
    return FrequencyRangeScanPlan(
        min_freq_hz=min_freq_hz,
        max_freq_hz=max_freq_hz,
        channel_frequencies_hz=list(range(min_freq_hz, max_freq_hz + 1, grid_step_hz)),
        channel_width_hz=width_hz,
        range_id=range_id,
    )


def _positive_width(value: int, *, name: str = "channel_width_hz") -> int:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _first_not_none(*values: int | None) -> int:
    for value in values:
        if value is not None:
            return value
    raise ValueError("at least one value is required")


def _validate_optional_positive(value: int | None, *, name: str) -> None:
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _validate_positive_frequencies(frequencies_hz: list[int], *, name: str) -> None:
    if any(frequency_hz <= 0 for frequency_hz in frequencies_hz):
        raise ValueError(f"{name} values must be greater than zero")
