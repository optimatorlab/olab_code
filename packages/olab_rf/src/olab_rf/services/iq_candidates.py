from __future__ import annotations

from olab_rf.decoders.rtl_sdr_iq import IqPeakEstimate
from olab_rf.models import FrequencyCandidate
from olab_rf.models.frequencies import FrequencyMatch
from olab_rf.services.frequency_catalog import FrequencyCatalog


def candidate_from_iq_peak(
    estimate: IqPeakEstimate,
    *,
    catalog: FrequencyCatalog,
    tolerance_hz: int = 2_500,
    baseline_power_db: float | None = None,
    sweeps_seen: int = 1,
    channel_frequencies_hz: list[int] | None = None,
) -> FrequencyCandidate:
    if tolerance_hz <= 0:
        raise ValueError("tolerance_hz must be greater than zero")
    if sweeps_seen <= 0:
        raise ValueError("sweeps_seen must be greater than zero")
    match, matched_frequency_hz, frequency_offset_hz = _match_iq_peak(
        estimate,
        catalog=catalog,
        tolerance_hz=tolerance_hz,
        channel_frequencies_hz=channel_frequencies_hz or [],
    )
    return FrequencyCandidate(
        frequency_hz=estimate.frequency_hz,
        power_db=estimate.power_db,
        baseline_power_db=baseline_power_db,
        margin_db=(
            estimate.power_db - baseline_power_db
            if baseline_power_db is not None
            else None
        ),
        sweeps_seen=sweeps_seen,
        label=match.label,
        modulation=match.modulation,
        range_id=match.range_id,
        channel_id=match.channel_id,
        matched_frequency_hz=matched_frequency_hz,
        frequency_offset_hz=frequency_offset_hz,
        source="iq_peak",
    )


def _match_iq_peak(
    estimate: IqPeakEstimate,
    *,
    catalog: FrequencyCatalog,
    tolerance_hz: int,
    channel_frequencies_hz: list[int],
) -> tuple[FrequencyMatch, int | None, int | None]:
    if channel_frequencies_hz:
        nearest_channel_hz = min(
            channel_frequencies_hz,
            key=lambda frequency_hz: abs(estimate.frequency_hz - frequency_hz),
        )
        offset_hz = estimate.frequency_hz - nearest_channel_hz
        if abs(offset_hz) <= tolerance_hz:
            match = catalog.match_frequency(nearest_channel_hz, tolerance_hz=0)
            return match, nearest_channel_hz, offset_hz
    match = catalog.match_frequency(estimate.frequency_hz, tolerance_hz=tolerance_hz)
    return match, match.channel_frequency_hz, match.offset_hz
