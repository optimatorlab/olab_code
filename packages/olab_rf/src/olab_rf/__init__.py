from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from olab_rf.models import (
    FrequencyBaseline,
    DigitalListenStatus,
    FrequencyCandidate,
    FrequencyCatalogRange,
    FrequencyChannel,
    FrequencyFavorite,
    FrequencyMatch,
    FrequencyRange,
    FrequencyScanBackend,
    FrequencyScanRequest,
    FrequencyScanStatus,
    Observation,
    PcmAudioFrame,
    RadioSession,
    RadioVoiceSegment,
    ReceiverConfig,
    RecordingRequest,
    RecordingStatus,
    SensorStatus,
    SpectrumBin,
    SpectrumEvent,
    SpectrumPeak,
    SpectrumSnapshot,
    Track,
    VoiceCaptureEvent,
    VoiceSegmentStatus,
)
from olab_rf.history import get_history
from olab_rf.services.frequency_catalog import FrequencyCatalog
from olab_rf.services.range_scanner import FrequencyRangeScanPlan, build_frequency_range_scan_plan
from olab_rf.services.session_manager import SessionManager

try:
    __version__ = version("olab-rf")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "__version__",
    "FrequencyBaseline",
    "DigitalListenStatus",
    "FrequencyCandidate",
    "FrequencyCatalog",
    "FrequencyCatalogRange",
    "FrequencyChannel",
    "FrequencyFavorite",
    "FrequencyMatch",
    "FrequencyRange",
    "FrequencyRangeScanPlan",
    "FrequencyScanBackend",
    "FrequencyScanRequest",
    "FrequencyScanStatus",
    "build_frequency_range_scan_plan",
    "get_history",
    "Observation",
    "PcmAudioFrame",
    "RadioSession",
    "RadioVoiceSegment",
    "ReceiverConfig",
    "RecordingRequest",
    "RecordingStatus",
    "SensorStatus",
    "SessionManager",
    "SpectrumBin",
    "SpectrumEvent",
    "SpectrumPeak",
    "SpectrumSnapshot",
    "Track",
    "VoiceCaptureEvent",
    "VoiceSegmentStatus",
]
