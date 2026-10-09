from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
import os
import shlex
from statistics import median
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread, current_thread
import time
from typing import Any, Literal
from uuid import uuid4

from olab_rf.config import OlabRfConfig
from olab_rf.decoders.process import DecoderProcess
from olab_rf.decoders.base import DecodedMessage
from olab_rf.decoders.replay import ReplayDecoder
from olab_rf.decoders.readsb import parse_readsb_aircraft_file, readsb_command
from olab_rf.decoders.rtl_fm import rtl_fm_audio_rate_hz, rtl_fm_command
from olab_rf.decoders.rtl_power import parse_rtl_power_line, rtl_power_command
from olab_rf.decoders.rtl_sdr_iq import estimate_iq_peak
from olab_rf.decoders.rtl_ais import parse_ais_nmea_line, rtl_ais_command
from olab_rf.decoders.sigmf import read_sigmf_iq, sigmf_paths, truncate_to_iq_pairs, write_sigmf_meta
from olab_rf.history import SqliteHistory
from olab_rf.models import (
    FrequencyCatalogRange,
    FrequencyChannel,
    ReceiverConfig,
    RecordingRequest,
    RecordingStatus,
    SensorStatus,
)
from olab_rf.models.digital import DigitalListenStatus
from olab_rf.decoders.sdrtrunk import SdrTrunkBackend
from olab_rf.models.voice import RadioVoiceSegment, VoiceCaptureEvent, VoiceSegmentStatus
from olab_rf.models.scanning import (
    FrequencyScanBackend,
    FrequencyBaseline,
    FrequencyCandidate,
    FrequencyScanRequest,
    FrequencyScanStatus,
    PriorityScanStatus,
)
from olab_rf.models.sessions import RadioSession
from olab_rf.models.spectrum import (
    FrequencyRange,
    SpectrumEvent,
    SpectrumSnapshot,
)
from olab_rf.receivers.rtlsdr_iq import capture_iq_samples_with_rtl_sdr, rtl_sdr_iq_command
from olab_rf.services.iq_candidates import candidate_from_iq_peak
from olab_rf.services.range_scanner import build_frequency_range_scan_plan
from olab_rf.services.track_store import TrackStore
from olab_rf.services.frequency_catalog import FrequencyCatalog
from olab_rf.services.voice_segments import (
    DETECTOR_MODES,
    MIN_SPECTRUM_BINS,
    AudioConditioner,
    PcmAudioBackend,
    RadioVoiceSegmenter,
    RtlFmAudioBackend,
    spectrum_bin_limit,
)
from olab_rf.models.tracks import dt_to_iso, utc_now

_DEFAULT_REPLAY_SECONDS = 5.0


class _UnsetType:
    """Sentinel distinguishing "caller left this unset" from any real value.

    ``start_priority_scan``/``priority_scan``'s ``deemphasis_us`` parameter
    needs this: it has a per-channel-modulation default (see
    ``_resolve_priority_scan_channels``), so a plain literal default (e.g.
    ``75.0``) would make "caller didn't pass it" indistinguishable from
    "caller explicitly passed the same value the default happens to be" --
    the AM per-modulation override could then never apply to a caller who
    passes ``deemphasis_us=75.0`` on purpose.
    """

    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _UnsetType()


def _priority_scan_rtl_fm_mode(modulation: str) -> str:
    """Normalize a modulation string to "am"/"wbfm"/"fm".

    Mirrors ``decoders.rtl_fm._rtl_fm_mode`` exactly (that function is
    private, so it can't be imported here) -- deliberately kept as its own
    small copy rather than deriving AM/wbfm from
    ``rtl_fm_audio_rate_hz()``'s *return value* (12_000/32_000), which
    couples a modulation decision to an audio-rate constant that could
    change for an unrelated reason in the future without any test failing.
    """
    normalized = modulation.strip().lower()
    if normalized in {"am", "airband", "aviation"}:
        return "am"
    if normalized in {"wfm", "widefm", "broadcast_fm"}:
        return "wbfm"
    return "fm"


@dataclass(slots=True)
class _PriorityScanChannel:
    """One resolved, fully-configured channel in a priority scan's rotation."""

    channel_id: str
    channel_label: str
    range_id: str
    range_label: str
    frequency_hz: int
    modulation: str
    sample_rate_hz: int
    deemphasis_us: float | None
    detector_mode: str
    hf_ratio_threshold: float


@dataclass(slots=True)
class _PriorityScanState:
    """Mutable per-scan state, separate from the ``PriorityScanStatus`` snapshot.

    Reused across every channel visit for the lifetime of one
    ``start_priority_scan`` call; discarded entirely by ``stop()``.
    """

    scan_id: str
    session_id: str
    channels: list[_PriorityScanChannel]
    dwell_ms: int
    max_lock_ms: int
    max_segment_sec: float
    hang_time_ms: int
    frame_ms: int
    dc_block: bool
    normalize: bool
    normalize_target_dbfs: float
    gain_db: float | None
    index: int = 0
    mode: str = "scanning"  # "scanning" | "locked"
    dwell_frame_count: int = 0
    lock_frame_count: int = 0
    cycle_count: int = 0
    # Scan-lifetime run totals, carried forward via carry_counters() at every
    # visit teardown so a fresh per-visit segmenter doesn't reset them to 0.
    completed: int = 0
    dropped: int = 0
    capped_closes: int = 0
    started_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class SessionManager:
    """Own one receiver and one active receive workflow.

    Create one ``SessionManager`` per physical receiver. Starting a new
    receiver workflow stops the previous one because an RTL-SDR device cannot
    be shared by concurrent decoder processes.
    """

    receiver: ReceiverConfig = field(default_factory=lambda: ReceiverConfig(id="rtlsdr-1"))
    track_store: TrackStore = field(default_factory=TrackStore)
    history: SqliteHistory | None = None
    config: OlabRfConfig | None = None
    frequency_catalog: FrequencyCatalog = field(default_factory=FrequencyCatalog.default)
    session: RadioSession | None = None
    status: SensorStatus = field(default_factory=lambda: SensorStatus(sensor_id="rtlsdr-1"))
    _replay_messages: Iterator[DecodedMessage] | None = None
    _message_count: int = 0
    _process: DecoderProcess | None = None
    _digital_backend: SdrTrunkBackend | None = None
    _digital_status: DigitalListenStatus | None = None
    _readsb_json_dir: Path | None = None
    _readsb_temp_dir: TemporaryDirectory[str] | None = None
    _spectrum: SpectrumSnapshot = field(default_factory=SpectrumSnapshot)
    _spectrum_history: list[SpectrumSnapshot] = field(default_factory=list)
    _spectrum_events: list[SpectrumEvent] = field(default_factory=list)
    _spectrum_preset_id: str = "custom"
    _spectrum_threshold_db: float = 12.0
    _watch_frequency_hz: int | None = None
    _watch_modulation: str = "nfm"
    _demod_path: str = "rtl_fm"
    _frequency_scan: FrequencyScanStatus | None = None
    _frequency_scan_baseline: FrequencyBaseline | None = None
    _frequency_scan_started_monotonic: float | None = None
    _frequency_scan_powers: dict[int, list[float]] = field(default_factory=dict)
    _frequency_scan_is_baseline: bool = False
    _last_spectrum_kwargs: dict[str, object] | None = None
    _previous_request: tuple[str, dict[str, object]] | None = None
    _poll_lock: Lock = field(default_factory=Lock)
    _recording: RecordingStatus | None = None
    _recording_process: DecoderProcess | None = None
    _voice_backend: PcmAudioBackend | None = None
    _voice_segmenter: RadioVoiceSegmenter | None = None
    _voice_segments: list[RadioVoiceSegment] = field(default_factory=list)
    _voice_poll_stop: Event | None = None
    _voice_poll_thread: Thread | None = None
    _voice_events: list[VoiceCaptureEvent] = field(default_factory=list)
    _voice_event_callback: Callable[[VoiceCaptureEvent], None] | None = None
    _voice_segment_callback: Callable[[RadioVoiceSegment], None] | None = None
    _voice_params: dict[str, object] = field(default_factory=dict)
    _priority_scan_state: _PriorityScanState | None = None
    spectrum_history_limit: int = 60
    spectrum_event_limit: int = 100

    @classmethod
    def from_config(
        cls,
        config: OlabRfConfig,
        *,
        history: SqliteHistory | None = None,
        receiver_index: int = 0,
    ) -> SessionManager:
        """Create a manager from ``OlabRfConfig`` without exposing decoder paths."""
        receiver = (
            config.receivers[receiver_index]
            if config.receivers
            else ReceiverConfig(id=f"receiver-{receiver_index}")
        )
        return cls(
            receiver=receiver,
            frequency_catalog=FrequencyCatalog.merged(override_payload=config.frequency_catalog),
            history=history,
            config=config,
        )

    def start_replay(self, steps: int = 12) -> RadioSession:
        """Start synthetic replay messages for tests and demos."""
        self.stop()
        self.track_store.clear()
        session = RadioSession(
            session_id=f"session-{uuid4()}",
            mode="replay",
            receiver_id=self.receiver.id,
            status="running",
            decoder="replay",
        )
        self.session = session
        self.status = SensorStatus(
            sensor_id=self.receiver.id,
            mode="replay",
            process_running=True,
            tool_found=True,
        )
        decoder = ReplayDecoder(sensor_id=self.receiver.id, session_id=session.session_id, steps=steps)
        self._replay_messages = decoder.messages()
        self._message_count = 0
        self.advance_replay()
        return session

    def start_adsb(
        self,
        path: str | None = None,
        write_json_dir: str | Path | None = None,
    ) -> RadioSession:
        """Start a ``readsb`` ADS-B subprocess and read its JSON output."""
        path = path or self._decoder_path("readsb", "readsb")
        temp_dir: TemporaryDirectory[str] | None = None
        if write_json_dir is None:
            temp_dir = TemporaryDirectory(prefix="olab-rf-readsb-")
            json_dir = Path(temp_dir.name)
        else:
            json_dir = Path(write_json_dir)
        json_dir.mkdir(parents=True, exist_ok=True)
        command = readsb_command(
            path=path,
            device_serial=self.receiver.serial,
            write_json_dir=json_dir,
        )
        try:
            session = self._start_process_mode(mode="adsb", decoder="readsb", command=command)
        except Exception:
            if temp_dir is not None:
                temp_dir.cleanup()
            raise
        self._readsb_json_dir = json_dir
        self._readsb_temp_dir = temp_dir
        return session

    def start_digital_listen(self, *, system_id: str) -> RadioSession:
        """Launch the operator-selected SDRTrunk GUI playlist (receive-only)."""
        systems = (self.config.digital_system_catalog.get("systems", []) if self.config else [])
        system = next((item for item in systems if item.get("id") == system_id), None)
        if not system:
            raise RuntimeError(f"unknown digital system: {system_id}")
        if system.get("backend") != "sdrtrunk" or system.get("mode") != "profile":
            raise RuntimeError("only sdrtrunk profile systems are supported")
        settings = self.config.sdrtrunk if self.config else None
        launcher = settings.launcher_path if settings else None
        profile = system.get("sdrtrunk_profile_path") or (settings.profile_path if settings else None)
        jmbe = settings.jmbe_path if settings else None
        status = DigitalListenStatus(system_id=system_id)
        if not launcher or not Path(launcher).is_file() or not os.access(launcher, os.X_OK):
            status.state, status.error = "error", "SDRTrunk launcher is missing"
        elif not profile or not Path(profile).is_file():
            status.state, status.error = "error", "SDRTrunk profile is missing"
        elif not jmbe or not Path(jmbe).exists():
            status.state, status.error = "error", "JMBE path is missing"
        if status.error:
            self._digital_status = status
            raise RuntimeError(status.error)
        self.stop()
        command = [launcher]
        session = RadioSession(session_id=f"session-{uuid4()}", mode="digital_listen", receiver_id=self.receiver.id, status="starting", decoder="sdrtrunk", command=command)
        self.session = session
        self.status = SensorStatus(sensor_id=self.receiver.id, mode="digital_listen", tool_found=True)
        self._digital_backend = SdrTrunkBackend(launcher_path=launcher, working_directory=settings.working_directory)
        self._digital_backend.start()
        session.status = "running"
        self.status.process_running = self._digital_backend.is_running()
        self._digital_status = DigitalListenStatus(session_id=session.session_id, system_id=system_id, state="running", process_running=self.status.process_running, tool_found=True, profile_found=True, jmbe_available=True, command=command)
        return session

    def current_digital_listen_status(self) -> DigitalListenStatus | None:
        return self._digital_status

    def start_ais(self, path: str | None = None) -> RadioSession:
        """Start an ``rtl_ais`` subprocess for AIS NMEA messages."""
        path = path or self._decoder_path("rtl_ais", "rtl_ais")
        command = rtl_ais_command(path=path, device_index=0, ppm=self.receiver.ppm)
        return self._start_process_mode(mode="ais", decoder="rtl_ais", command=command)

    def start_spectrum(
        self,
        *,
        path: str | None = None,
        preset_id: str = "noaa_weather",
        start_hz: int | None = None,
        stop_hz: int | None = None,
        bin_hz: int | None = None,
        interval_s: int = 2,
        gain_db: float | None = None,
        sample_rate_hz: int | None = None,
        threshold_db: float = 12.0,
        demod_path: str | None = None,
    ) -> RadioSession:
        """Start a live spectrum monitor using ``rtl_power``.

        Use this for ongoing sweep/waterfall/event monitoring. Use
        ``start_frequency_scan`` for bounded candidate-frequency discovery.
        """
        path = path or self._decoder_path("rtl_power", "rtl_power")
        demod_path = demod_path or self._decoder_path("rtl_fm", "rtl_fm")
        self._last_spectrum_kwargs = {
            "path": path,
            "preset_id": preset_id,
            "start_hz": start_hz,
            "stop_hz": stop_hz,
            "bin_hz": bin_hz,
            "interval_s": interval_s,
            "gain_db": gain_db,
            "sample_rate_hz": sample_rate_hz,
            "threshold_db": threshold_db,
            "demod_path": demod_path,
        }
        catalog_range = self.frequency_catalog.range_by_id(preset_id)
        if catalog_range is None and preset_id != "custom":
            raise RuntimeError(f"unknown frequency catalog range: {preset_id}")
        default_bin_hz = (
            catalog_range.default_bin_size_hz if catalog_range else None
        ) or bin_hz or 100_000
        ranges = [
            FrequencyRange(
                start_hz=catalog_range.min_freq_hz,
                stop_hz=catalog_range.max_freq_hz,
                bin_hz=default_bin_hz,
            )
        ] if catalog_range else [
            FrequencyRange(start_hz=88_000_000, stop_hz=108_000_000, bin_hz=default_bin_hz)
        ]
        if start_hz and stop_hz:
            ranges = [
                FrequencyRange(
                    start_hz=start_hz,
                    stop_hz=stop_hz,
                    bin_hz=bin_hz or default_bin_hz,
                )
            ]
        command = rtl_power_command(
            path=path,
            ranges=ranges,
            device_index=0,
            ppm=self.receiver.ppm,
            gain_db=gain_db,
            sample_rate_hz=sample_rate_hz,
            interval_s=interval_s,
        )
        self._clear_spectrum()
        self._spectrum_preset_id = preset_id if catalog_range or preset_id == "custom" else "custom"
        self._spectrum_threshold_db = threshold_db
        self._watch_modulation = (
            catalog_range.default_modulation if catalog_range else None
        ) or "custom"
        self._demod_path = demod_path
        return self._start_process_mode(mode="spectrum", decoder="rtl_power", command=command)

    def start_frequency_scan(
        self,
        *,
        min_freq_hz: int,
        max_freq_hz: int,
        bin_size_hz: int,
        duration_sec: float,
        channel_frequencies_hz: list[int] | None = None,
        channel_width_hz: int | None = None,
        backend: str = "rtl_power",
        path: str | None = None,
        gain_db: float | None = None,
        sample_rate_hz: int | None = None,
        baseline: FrequencyBaseline | None = None,
        resume_previous: bool = False,
    ) -> FrequencyScanStatus:
        """Start a bounded, non-blocking frequency discovery scan.

        Call ``poll()`` until the returned scan status becomes ``complete`` or
        ``error``. If ``baseline`` is omitted, the most recent baseline captured
        by this manager is reused when available.
        """
        if backend == "iq_replay":
            raise ValueError("iq_replay is not supported here; use start_iq_replay_scan()")
        path = path or self._frequency_scan_backend_path(backend)
        request = FrequencyScanRequest(
            min_freq_hz=min_freq_hz,
            max_freq_hz=max_freq_hz,
            bin_size_hz=bin_size_hz,
            duration_sec=duration_sec,
            channel_frequencies_hz=list(channel_frequencies_hz or []),
            channel_width_hz=channel_width_hz,
            backend=backend,
            gain_db=gain_db,
            sample_rate_hz=sample_rate_hz,
            resume_previous=resume_previous,
        )
        return self._start_frequency_scan(
            request=request,
            path=path,
            baseline=baseline,
            is_baseline=False,
        )

    def start_range_scan(
        self,
        *,
        range_id: str | None = "frs_gmrs",
        min_freq_hz: int | None = None,
        max_freq_hz: int | None = None,
        step_hz: int | None = None,
        channel_frequencies_hz: list[int] | None = None,
        channel_width_hz: int | None = None,
        backend: FrequencyScanBackend = "rtl_power",
        path: str | None = None,
        duration_sec: float = 20.0,
        gain_db: float | None = None,
        sample_rate_hz: int | None = None,
        baseline: FrequencyBaseline | None = None,
        resume_previous: bool = False,
    ) -> FrequencyScanStatus:
        """Start a range scan from catalog or arbitrary frequency inputs.

        Catalog ranges use their known channels when available. Ranges without
        channel definitions, or explicit min/max inputs, are converted to a
        grid using ``step_hz`` or ``channel_width_hz``.
        """
        if backend == "iq_replay":
            raise ValueError("iq_replay is not supported here; use start_iq_replay_scan()")
        plan = build_frequency_range_scan_plan(
            catalog=self.frequency_catalog,
            range_id=range_id,
            min_freq_hz=min_freq_hz,
            max_freq_hz=max_freq_hz,
            step_hz=step_hz,
            channel_width_hz=channel_width_hz,
            channel_frequencies_hz=channel_frequencies_hz,
        )
        return self.start_frequency_scan(
            path=path or self._frequency_scan_backend_path(backend),
            backend=backend,
            min_freq_hz=plan.min_freq_hz,
            max_freq_hz=plan.max_freq_hz,
            bin_size_hz=plan.channel_width_hz,
            duration_sec=duration_sec,
            channel_frequencies_hz=plan.channel_frequencies_hz,
            channel_width_hz=plan.channel_width_hz,
            gain_db=gain_db,
            sample_rate_hz=sample_rate_hz,
            baseline=baseline,
            resume_previous=resume_previous,
        )

    def find_active_channels(
        self,
        *,
        range_id: str,
        backend: FrequencyScanBackend = "rtl_power",
        path: str | None = None,
        duration_sec: float = 10.0,
        channel_width_hz: int | None = None,
        gain_db: float | None = None,
        sample_rate_hz: int | None = None,
        baseline: FrequencyBaseline | None = None,
        resume_previous: bool = False,
    ) -> FrequencyScanStatus:
        """Scan known catalog channels in a range and return scan status."""
        if backend == "iq_replay":
            raise ValueError("iq_replay is not supported here; use start_iq_replay_scan()")
        frequency_range = self.frequency_catalog.range_by_id(range_id)
        if frequency_range is None:
            raise ValueError(f"range id not found: {range_id}")
        if not frequency_range.channels:
            raise ValueError(f"range has no catalog channels: {range_id}")
        return self.start_range_scan(
            range_id=range_id,
            backend=backend,
            path=path,
            duration_sec=duration_sec,
            channel_width_hz=channel_width_hz,
            gain_db=gain_db,
            sample_rate_hz=sample_rate_hz,
            baseline=baseline,
            resume_previous=resume_previous,
        )

    def capture_frequency_baseline(
        self,
        *,
        min_freq_hz: int,
        max_freq_hz: int,
        bin_size_hz: int,
        duration_sec: float,
        channel_frequencies_hz: list[int] | None = None,
        channel_width_hz: int | None = None,
        backend: str = "rtl_power",
        path: str | None = None,
        gain_db: float | None = None,
        sample_rate_hz: int | None = None,
    ) -> FrequencyScanStatus:
        """Start a bounded baseline scan for later differential comparison."""
        if backend == "iq_replay":
            raise ValueError("iq_replay is not supported here; use start_iq_replay_scan()")
        path = path or self._frequency_scan_backend_path(backend)
        request = FrequencyScanRequest(
            min_freq_hz=min_freq_hz,
            max_freq_hz=max_freq_hz,
            bin_size_hz=bin_size_hz,
            duration_sec=duration_sec,
            channel_frequencies_hz=list(channel_frequencies_hz or []),
            channel_width_hz=channel_width_hz,
            backend=backend,
            gain_db=gain_db,
            sample_rate_hz=sample_rate_hz,
        )
        return self._start_frequency_scan(
            request=request,
            path=path,
            baseline=None,
            is_baseline=True,
        )

    def capture_range_baseline(
        self,
        *,
        range_id: str | None = "frs_gmrs",
        min_freq_hz: int | None = None,
        max_freq_hz: int | None = None,
        step_hz: int | None = None,
        channel_frequencies_hz: list[int] | None = None,
        channel_width_hz: int | None = None,
        backend: FrequencyScanBackend = "rtl_power",
        path: str | None = None,
        duration_sec: float = 10.0,
        gain_db: float | None = None,
        sample_rate_hz: int | None = None,
    ) -> FrequencyScanStatus:
        """Start a range baseline from catalog or arbitrary frequency inputs."""
        if backend == "iq_replay":
            raise ValueError("iq_replay is not supported here; use start_iq_replay_scan()")
        plan = build_frequency_range_scan_plan(
            catalog=self.frequency_catalog,
            range_id=range_id,
            min_freq_hz=min_freq_hz,
            max_freq_hz=max_freq_hz,
            step_hz=step_hz,
            channel_width_hz=channel_width_hz,
            channel_frequencies_hz=channel_frequencies_hz,
        )
        return self.capture_frequency_baseline(
            path=path or self._frequency_scan_backend_path(backend),
            backend=backend,
            min_freq_hz=plan.min_freq_hz,
            max_freq_hz=plan.max_freq_hz,
            bin_size_hz=plan.channel_width_hz,
            duration_sec=duration_sec,
            channel_frequencies_hz=plan.channel_frequencies_hz,
            channel_width_hz=plan.channel_width_hz,
            gain_db=gain_db,
            sample_rate_hz=sample_rate_hz,
        )

    def start_iq_replay_scan(
        self,
        *,
        replay_path: str,
        channel_width_hz: int | None = None,
        replay_max_samples: int | None = None,
    ) -> FrequencyScanStatus:
        """Replay a previously recorded SigMF IQ file through the live IQ path.

        Unlike every other scan entry point this reads a file and touches no
        device: it does not call ``stop()`` (so it does not conflict with a
        live recording or any other mode) and does not create a
        ``RadioSession`` or mutate ``self.session``/``self.status``. It does
        still respect the single ``self._frequency_scan`` slot every scan
        backend shares — call this while another scan is ``"running"`` and it
        raises, the same "one scan at a time" invariant ``stop()`` enforces
        for the other backends.
        """
        request = FrequencyScanRequest(
            backend="iq_replay",
            replay_path=replay_path,
            channel_width_hz=channel_width_hz,
            replay_max_samples=replay_max_samples,
        )
        return self._start_frequency_scan(
            request=request,
            path="",
            baseline=None,
            is_baseline=False,
        )

    def _start_frequency_scan(
        self,
        *,
        request: FrequencyScanRequest,
        path: str,
        baseline: FrequencyBaseline | None,
        is_baseline: bool,
    ) -> FrequencyScanStatus:
        if request.backend == "iq_replay":
            return self._run_iq_replay_scan(request=request)
        if request.backend == "rtl_sdr_iq":
            return self._run_iq_frequency_scan(
                request=request,
                path=path,
                baseline=baseline,
                is_baseline=is_baseline,
            )
        if request.backend != "rtl_power":
            raise RuntimeError(f"frequency scan backend is not implemented: {request.backend}")
        if request.resume_previous and self.session and self.session.mode == "spectrum":
            if self._last_spectrum_kwargs:
                self._previous_request = ("spectrum", dict(self._last_spectrum_kwargs))
        ranges = [
            FrequencyRange(
                start_hz=request.min_freq_hz,
                stop_hz=request.max_freq_hz,
                bin_hz=request.bin_size_hz,
            )
        ]
        command = rtl_power_command(
            path=path,
            ranges=ranges,
            device_index=0,
            ppm=self.receiver.ppm,
            gain_db=request.gain_db,
            sample_rate_hz=request.sample_rate_hz,
            interval_s=max(1, min(2, int(request.duration_sec) or 1)),
        )
        self.stop(clear_previous=False)
        session = RadioSession(
            session_id=f"session-{uuid4()}",
            mode="frequency_baseline" if is_baseline else "frequency_scan",
            receiver_id=self.receiver.id,
            status="starting",
            decoder="rtl_power",
            command=command,
        )
        self.session = session
        self.status = SensorStatus(sensor_id=self.receiver.id, mode=session.mode, tool_found=False)
        self._message_count = 0
        self._process = DecoderProcess(command=command)
        scan = FrequencyScanStatus.created(
            request=request,
            session_id=session.session_id,
            baseline_id=baseline.baseline_id if baseline else None,
        )
        self._frequency_scan = self._replace_scan(scan, status="running")
        if is_baseline or baseline is not None:
            self._frequency_scan_baseline = baseline
        self._frequency_scan_started_monotonic = time.monotonic()
        self._frequency_scan_powers = {}
        self._frequency_scan_is_baseline = is_baseline
        try:
            self._process.start()
        except FileNotFoundError as exc:
            session.status = "error"
            self.status.error = f"{command[0]} not found"
            self._frequency_scan = self._replace_scan(
                self._frequency_scan,
                status="error",
                error=self.status.error,
            )
            raise RuntimeError(self.status.error) from exc
        session.status = "running"
        self.status.process_running = self._process.is_running()
        self.status.tool_found = True
        return self._frequency_scan

    def _run_iq_frequency_scan(
        self,
        *,
        request: FrequencyScanRequest,
        path: str,
        baseline: FrequencyBaseline | None,
        is_baseline: bool,
    ) -> FrequencyScanStatus:
        if not request.channel_frequencies_hz:
            raise RuntimeError("rtl_sdr_iq scans require channel_frequencies_hz")
        if request.resume_previous and self.session and self.session.mode == "spectrum":
            if self._last_spectrum_kwargs:
                self._previous_request = ("spectrum", dict(self._last_spectrum_kwargs))
        self.stop(clear_previous=False)
        session = RadioSession(
            session_id=f"session-{uuid4()}",
            mode="frequency_baseline" if is_baseline else "frequency_scan",
            receiver_id=self.receiver.id,
            status="running",
            decoder="rtl_sdr_iq",
            command=[],
        )
        self.session = session
        self.status = SensorStatus(
            sensor_id=self.receiver.id,
            mode=session.mode,
            process_running=True,
            tool_found=True,
        )
        scan = FrequencyScanStatus.created(
            request=request,
            session_id=session.session_id,
            baseline_id=baseline.baseline_id if baseline else None,
        )
        self._frequency_scan = self._replace_scan(scan, status="running")
        self._frequency_scan_baseline = baseline if not is_baseline else None
        self._frequency_scan_started_monotonic = time.monotonic()
        self._frequency_scan_powers = {}
        self._frequency_scan_is_baseline = is_baseline
        capture_path = "rtl_sdr" if path == "rtl_power" else path
        sample_rate_hz = request.sample_rate_hz or 240_000
        sample_count = max(1024, int(sample_rate_hz * request.duration_sec))
        tolerance_hz = request.channel_width_hz or max(2_500, request.bin_size_hz // 2)
        catalog = self._catalog_with_history_favorites()
        candidates: list[FrequencyCandidate] = []
        baseline_powers = baseline.powers_by_frequency_hz if baseline else {}
        try:
            for frequency_hz in request.channel_frequencies_hz:
                samples = capture_iq_samples_with_rtl_sdr(
                    path=capture_path,
                    center_frequency_hz=frequency_hz,
                    sample_rate_hz=sample_rate_hz,
                    sample_count=sample_count,
                    device_index=0,
                    gain_db=_receiver_gain_db(self.receiver.gain, request.gain_db),
                    ppm=self.receiver.ppm,
                )
                estimate = estimate_iq_peak(
                    samples,
                    center_frequency_hz=frequency_hz,
                    sample_rate_hz=sample_rate_hz,
                    max_offset_hz=tolerance_hz,
                )
                self._frequency_scan_powers.setdefault(estimate.frequency_hz, []).append(
                    estimate.power_db
                )
                baseline_power = self._nearest_power(baseline_powers, estimate.frequency_hz)
                candidates.append(
                    candidate_from_iq_peak(
                        estimate,
                        catalog=catalog,
                        tolerance_hz=tolerance_hz,
                        baseline_power_db=baseline_power,
                        channel_frequencies_hz=request.channel_frequencies_hz,
                    )
                )
        except (RuntimeError, ValueError) as exc:
            self.status.error = str(exc)
            self._frequency_scan = self._replace_scan(
                self._frequency_scan,
                status="error",
                progress=1.0,
                stopped_at=utc_now(),
                error=self.status.error,
            )
            session.status = "error"
            self.status.process_running = False
            raise RuntimeError(self.status.error) from exc
        elapsed = time.monotonic() - self._frequency_scan_started_monotonic
        self._message_count = len(candidates)
        self.status.message_count = self._message_count
        self.status.messages_per_second = (
            float(self._message_count) if elapsed == 0 else self._message_count / elapsed
        )
        self._frequency_scan = self._replace_scan(
            self._frequency_scan,
            candidates=sorted(candidates, key=lambda item: item.power_db, reverse=True),
            elapsed_sec=elapsed,
            progress=1.0,
            sweeps_completed=len(request.channel_frequencies_hz),
        )
        self._complete_frequency_scan()
        return self._frequency_scan

    def _run_iq_replay_scan(self, *, request: FrequencyScanRequest) -> FrequencyScanStatus:
        """Replay a recorded SigMF file through the same IQ-peak code path.

        Deliberately does not call ``self.stop()`` and does not touch
        ``self.session``/``self.status`` — replay reads a file, it does not
        need exclusive access to the physical receiver, so it must not
        conflict with a live recording (or clobber a live scan/listen mode's
        status surface). It still respects the single ``self._frequency_scan``
        slot every scan backend shares.

        Failures (a missing/corrupt SigMF file, an unsupported datatype, a
        capture with no samples) are routed through this scan's own ``error``
        status, the same convention every other backend uses, rather than
        letting a raw exception escape uncaught.
        """
        if self._frequency_scan and self._frequency_scan.status == "running":
            raise RuntimeError("a frequency scan is already running")
        assert request.replay_path is not None  # enforced by __post_init__

        self._frequency_scan_started_monotonic = time.monotonic()
        self._frequency_scan_powers = {}
        self._frequency_scan_is_baseline = False
        self._frequency_scan_baseline = None
        self._frequency_scan = FrequencyScanStatus.created(
            request=request, session_id=None, baseline_id=None
        )

        try:
            # A cheap probe (one sample) validates the file -- datatype,
            # non-empty captures -- via read_sigmf_iq's own checks, and gives
            # us the file's own sample rate to size the default read window,
            # without a second hand-rolled parse of the sidecar.
            probe = read_sigmf_iq(request.replay_path, max_samples=1)
            max_samples = (
                request.replay_max_samples
                if request.replay_max_samples is not None
                else int(probe.sample_rate_hz * _DEFAULT_REPLAY_SECONDS)
            )
            recording = read_sigmf_iq(request.replay_path, max_samples=max_samples)
            if recording.samples.size == 0:
                raise ValueError(
                    f"SigMF capture has no samples to replay: {request.replay_path}"
                )
            tolerance_hz = request.channel_width_hz or 2_500
            fft_size = _largest_power_of_two_leq(int(recording.samples.size))
            estimate = estimate_iq_peak(
                recording.samples,
                center_frequency_hz=recording.frequency_hz,
                sample_rate_hz=recording.sample_rate_hz,
                fft_size=fft_size,
                max_offset_hz=tolerance_hz,
            )
            catalog = self._catalog_with_history_favorites()
            candidate = candidate_from_iq_peak(
                estimate,
                catalog=catalog,
                tolerance_hz=tolerance_hz,
            )
        except (OSError, ValueError) as exc:
            error = str(exc)
            self._frequency_scan = self._replace_scan(
                self._frequency_scan,
                status="error",
                progress=1.0,
                stopped_at=utc_now(),
                error=error,
            )
            raise RuntimeError(error) from exc

        self._frequency_scan = self._replace_scan(
            self._frequency_scan,
            status="complete",
            candidates=[candidate],
            elapsed_sec=time.monotonic() - self._frequency_scan_started_monotonic,
            progress=1.0,
            sweeps_completed=1,
            stopped_at=utc_now(),
        )
        if self.history:
            self.history.add_frequency_scan(self._frequency_scan)
        return self._frequency_scan

    def start_listen(
        self,
        *,
        demod_path: str | None = None,
        frequency_hz: int | None = None,
        modulation: str | None = None,
    ) -> RadioSession:
        """Start demodulated audio playback for the selected listen frequency."""
        self._demod_path = demod_path or self._decoder_path("rtl_fm", "rtl_fm")
        if frequency_hz is not None:
            self.set_watch_frequency(frequency_hz, modulation=modulation)
        if not self._watch_frequency_hz:
            raise RuntimeError("select a watch frequency before starting listen")
        play_command = self.watch_dict()["play_command"]
        if not isinstance(play_command, str) or not play_command:
            raise RuntimeError("listen command is unavailable")
        return self._start_process_mode(
            mode="listen",
            decoder="rtl_fm",
            command=[play_command],
            shell=True,
        )

    def start_voice_segments(
        self,
        *,
        frequency_hz: int | None = None,
        modulation: str = "NFM",
        backend: str | PcmAudioBackend = "rtl_fm",
        path: str | None = None,
        gain_db: float | None = None,
        sample_rate_hz: int = 16_000,
        frame_ms: int = 40,
        threshold_db: float = 10.0,
        min_active_ms: int = 120,
        hang_time_ms: int = 600,
        min_segment_ms: int = 400,
        max_segment_sec: float = 20.0,
        pre_roll_ms: int = 200,
        detector_mode: str = "hf_ratio",
        hf_ratio_threshold: float = 1.2,
        max_floor_drift_db_per_sec: float = 6.0,
        recalibration_ms: int = 1_000,
        silence_floor_db: float = -60.0,
        audio_spectrum_bins: int = 0,
        dc_block: bool = True,
        deemphasis_us: float | None = 75.0,
        normalize: bool = False,
        normalize_target_dbfs: float = -20.0,
        fir_size: int | None = None,
        atan_math: str | None = None,
        extra_args: list[str] | None = None,
        auto_poll: bool = False,
        poll_interval_sec: float = 0.05,
        on_event: Callable[[VoiceCaptureEvent], None] | None = None,
        on_segment: Callable[[RadioVoiceSegment], None] | None = None,
    ) -> RadioSession:
        """Start PCM capture and transmission segmentation for an analog voice channel."""
        if frequency_hz is None:
            frequency_hz = self._watch_frequency_hz
        if frequency_hz is None:
            raise RuntimeError("frequency_hz is required without a selected watch frequency")
        if poll_interval_sec <= 0:
            raise ValueError("poll_interval_sec must be greater than zero")
        self.stop()
        session = RadioSession(
            session_id=f"session-{uuid4()}",
            mode="voice_segments",
            receiver_id=self.receiver.id,
            status="starting",
            decoder="rtl_fm" if backend == "rtl_fm" else type(backend).__name__,
        )
        if backend == "rtl_fm":
            path = path or self._decoder_path("rtl_fm", "rtl_fm")
            config_fir, config_atan, config_args = self._decoder_settings("rtl_fm")
            # Explicit call arguments win; config supplies the default.
            fir_size = fir_size if fir_size is not None else config_fir
            atan_math = atan_math if atan_math is not None else config_atan
            extra_args = extra_args if extra_args is not None else config_args
            voice_backend: PcmAudioBackend = RtlFmAudioBackend(
                path=path,
                frequency_hz=frequency_hz,
                modulation=modulation,
                sample_rate_hz=sample_rate_hz,
                frame_ms=frame_ms,
                ppm=self.receiver.ppm,
                gain_db=gain_db,
                fir_size=fir_size,
                atan_math=atan_math,
                extra_args=extra_args,
            )
            session.command = voice_backend.command
        elif isinstance(backend, str):
            raise ValueError(f"unsupported voice audio backend: {backend}")
        else:
            voice_backend = backend
            if voice_backend.sample_rate_hz != sample_rate_hz:
                raise ValueError("backend sample rate must match sample_rate_hz")
        self.session = session
        self.status = SensorStatus(sensor_id=self.receiver.id, mode="voice_segments", tool_found=False)
        self._voice_segmenter = RadioVoiceSegmenter(
            session_id=session.session_id,
            frequency_hz=frequency_hz,
            modulation=modulation,
            sample_rate_hz=sample_rate_hz,
            frame_ms=frame_ms,
            threshold_db=threshold_db,
            min_active_ms=min_active_ms,
            hang_time_ms=hang_time_ms,
            min_segment_ms=min_segment_ms,
            max_segment_sec=max_segment_sec,
            pre_roll_ms=pre_roll_ms,
            detector_mode=detector_mode,
            hf_ratio_threshold=hf_ratio_threshold,
            max_floor_drift_db_per_sec=max_floor_drift_db_per_sec,
            recalibration_ms=recalibration_ms,
            silence_floor_db=silence_floor_db,
            audio_spectrum_bins=audio_spectrum_bins,
            conditioner=AudioConditioner(
                sample_rate_hz=sample_rate_hz,
                dc_block=dc_block,
                deemphasis_us=deemphasis_us,
                normalize=normalize,
                normalize_target_dbfs=normalize_target_dbfs,
            ),
        )
        self._voice_params = {
            "frequency_hz": frequency_hz,
            "modulation": modulation,
            "backend": backend,
            "path": path,
            "gain_db": gain_db,
            "sample_rate_hz": sample_rate_hz,
            "frame_ms": frame_ms,
            "threshold_db": threshold_db,
            "min_active_ms": min_active_ms,
            "hang_time_ms": hang_time_ms,
            "min_segment_ms": min_segment_ms,
            "max_segment_sec": max_segment_sec,
            "pre_roll_ms": pre_roll_ms,
            "detector_mode": detector_mode,
            "hf_ratio_threshold": hf_ratio_threshold,
            "max_floor_drift_db_per_sec": max_floor_drift_db_per_sec,
            "recalibration_ms": recalibration_ms,
            "silence_floor_db": silence_floor_db,
            "audio_spectrum_bins": audio_spectrum_bins,
            "dc_block": dc_block,
            "deemphasis_us": deemphasis_us,
            "normalize": normalize,
            "normalize_target_dbfs": normalize_target_dbfs,
            "fir_size": fir_size,
            "atan_math": atan_math,
            "extra_args": extra_args,
            "auto_poll": auto_poll,
            "poll_interval_sec": poll_interval_sec,
            "on_event": on_event,
            "on_segment": on_segment,
        }
        self._voice_segments.clear()
        self._voice_events.clear()
        self._voice_event_callback = on_event
        self._voice_segment_callback = on_segment
        self._voice_backend = voice_backend
        try:
            voice_backend.start()
        except FileNotFoundError as exc:
            self._voice_backend = None
            session.status = "error"
            self.status.error = f"{path or 'voice backend'} not found"
            raise RuntimeError(self.status.error) from exc
        session.status = "running"
        self.status.process_running = voice_backend.is_running()
        self.status.tool_found = True
        self._emit_voice_event("capture_started", state="calibrating")
        if auto_poll:
            self._start_voice_auto_poll(poll_interval_sec)
        return session

    def ingest_voice_segments(self) -> int:
        if not self.session or self.session.mode != "voice_segments" or not self._voice_backend:
            return 0
        segmenter = self._voice_segmenter
        if segmenter is None:
            return 0
        emitted = 0
        for frame in self._voice_backend.read_frames():
            was_active = segmenter.status().active
            segments = segmenter.ingest(frame)
            if not was_active and segmenter.status().active:
                self._emit_voice_event("transmission_started", state="transmitting")
            self._voice_segments.extend(segments)
            emitted += len(segments)
            for segment in segments:
                self._emit_voice_event(
                    "transmission_ended",
                    state="idle",
                    segment_id=segment.segment_id,
                )
                self._notify_voice_segment(segment)
        stderr_lines = self._voice_backend.read_stderr_lines()
        self.status.process_running = self._voice_backend.is_running()
        if not self.status.process_running and self.session.status == "running":
            self.session.status = "stopped"
            self._set_process_exit_error(stderr_lines, fallback="voice audio process stopped")
            self._emit_voice_event(
                "capture_stopped",
                state="error" if self.status.error else "stopped",
                message=self.status.error,
            )
        self._message_count += emitted
        self.status.message_count = self._message_count
        return emitted

    def pop_voice_segments(self) -> list[RadioVoiceSegment]:
        """Return completed segments accumulated since the last call."""
        with self._poll_lock:
            segments, self._voice_segments = self._voice_segments, []
            return segments

    def pop_voice_events(self) -> list[VoiceCaptureEvent]:
        """Return voice capture lifecycle events accumulated since the last call."""
        with self._poll_lock:
            events, self._voice_events = self._voice_events, []
            return events

    def current_voice_segment_status(self) -> VoiceSegmentStatus | None:
        with self._poll_lock:
            if (
                self._voice_segmenter is None
                or self.session is None
                or self.session.mode != "voice_segments"
            ):
                return None
            status = self._voice_segmenter.status(error=self.status.error)
            if self.status.error:
                state = "error"
            elif not self.status.process_running:
                state = "stopped"
            elif status.active:
                state = "transmitting"
            elif status.noise_floor_db is None:
                state = "calibrating"
            else:
                state = "idle"
            return replace(
                status,
                capture_running=self.status.process_running,
                state=state,
            )

    def voice_capture_running(self) -> bool:
        """Return whether a voice PCM capture process is currently running."""
        return bool(
            self.session
            and self.session.mode == "voice_segments"
            and self.status.process_running
        )

    def update_voice_segment_settings(
        self,
        *,
        threshold_db: float | None = None,
        min_active_ms: int | None = None,
        hang_time_ms: int | None = None,
        min_segment_ms: int | None = None,
        max_segment_sec: float | None = None,
        pre_roll_ms: int | None = None,
        detector_mode: str | None = None,
        hf_ratio_threshold: float | None = None,
        max_floor_drift_db_per_sec: float | None = None,
        silence_floor_db: float | None = None,
        audio_spectrum_bins: int | None = None,
        dc_block: bool | None = None,
        deemphasis_us: float | None = None,
        disable_deemphasis: bool = False,
        normalize: bool | None = None,
        normalize_target_dbfs: float | None = None,
    ) -> VoiceSegmentStatus:
        """Update gate and audio-conditioning settings without interrupting capture.

        Conditioning is live because it runs in Python on the PCM stream rather
        than inside ``rtl_fm``. Frequency, gain, ppm, sample rate, and backend
        still require a respawn -- see ``restart_voice_capture``.
        """
        if (
            self.session is None
            or self.session.mode != "voice_segments"
            or self._voice_segmenter is None
        ):
            raise RuntimeError("voice segment capture is not active")
        with self._poll_lock:
            self._voice_segmenter.update_settings(
                threshold_db=threshold_db,
                min_active_ms=min_active_ms,
                hang_time_ms=hang_time_ms,
                min_segment_ms=min_segment_ms,
                max_segment_sec=max_segment_sec,
                pre_roll_ms=pre_roll_ms,
                detector_mode=detector_mode,
                hf_ratio_threshold=hf_ratio_threshold,
                max_floor_drift_db_per_sec=max_floor_drift_db_per_sec,
                silence_floor_db=silence_floor_db,
                audio_spectrum_bins=audio_spectrum_bins,
                dc_block=dc_block,
                deemphasis_us=deemphasis_us,
                disable_deemphasis=disable_deemphasis,
                normalize=normalize,
                normalize_target_dbfs=normalize_target_dbfs,
            )
            # Record what was applied, so a respawn restores the tuning instead
            # of silently reverting to whatever the session started with. This is
            # a translation, not a copy: the updater and the starter use
            # different vocabularies for the same state, and `None` means "leave
            # unchanged" here but "disabled" there.
            applied: dict[str, object] = {
                "threshold_db": threshold_db,
                "min_active_ms": min_active_ms,
                "hang_time_ms": hang_time_ms,
                "min_segment_ms": min_segment_ms,
                "max_segment_sec": max_segment_sec,
                "pre_roll_ms": pre_roll_ms,
                "detector_mode": detector_mode,
                "hf_ratio_threshold": hf_ratio_threshold,
                "max_floor_drift_db_per_sec": max_floor_drift_db_per_sec,
                "silence_floor_db": silence_floor_db,
                "audio_spectrum_bins": audio_spectrum_bins,
                "dc_block": dc_block,
                "normalize": normalize,
                "normalize_target_dbfs": normalize_target_dbfs,
            }
            self._voice_params.update({k: v for k, v in applied.items() if v is not None})
            if disable_deemphasis:
                # start_voice_segments has no `disable_deemphasis` parameter at
                # all; writing the updater's own vocabulary back would make every
                # later respawn raise TypeError.
                self._voice_params["deemphasis_us"] = None
            elif deemphasis_us is not None:
                self._voice_params["deemphasis_us"] = deemphasis_us
            return self._voice_segmenter.status(error=self.status.error)

    def restart_voice_capture(self, **overrides: object) -> RadioSession:
        """Respawn the receiver for a startup-only parameter change.

        Gain, ppm, frequency and sample rate are fixed when ``rtl_fm`` execs, so
        changing them means a new process. These guarantees live here rather than
        in a UI, because every ``SessionManager`` consumer needs them:

        * **Refuses during an active segment.** ``reset_calibration()`` raises
          there anyway, and force-closing would truncate a live transmission.
        * **Preserves completed-but-unpopped segments and events.**
          ``start_voice_segments()`` clears both, which during a capture run would
          be silent data loss rather than a cosmetic reset.
        * **Carries run counters**, so totals stay monotonic across a respawn.
        * **Preserves live settings.** Gate and conditioning values applied since
          the session started are replayed, rather than reverting to whatever it
          began with. A stored ``audio_spectrum_bins`` illegal at the new rate is
          clamped, not rejected; the stored value keeps what was *requested*, so
          respawning back restores it.

        A ``sample_rate_hz`` change additionally rebuilds the segmenter and so
        discards the learned noise floor -- unavoidable, since the byte caps and
        frame sizing derive from the rate at construction.

        Callers who injected a backend object must pass a fresh one in
        ``overrides``: the existing instance has already been stopped, and reusing
        it would restart a dead process.
        """
        if (
            self.session is None
            or self.session.mode != "voice_segments"
            or self._voice_segmenter is None
        ):
            raise RuntimeError("voice segment capture is not active")

        # Ordering matters twice over. Stop the poller first so the active-segment
        # check cannot race a transmission starting between check and stop; then,
        # if we must refuse, restart it before raising -- an earlier version
        # refused *after* stopping and never restarted, so frame consumption died,
        # the segment could never end, and the remedy the error recommends became
        # unreachable. The session wedged on its own guard.
        requested_bins: int | None = None
        was_auto_polling = self._voice_poll_thread is not None
        poll_interval = float(self._voice_params.get("poll_interval_sec", 0.05) or 0.05)
        self._stop_voice_auto_poll()
        with self._poll_lock:
            status = self._voice_segmenter.status()
        if status.active:
            if was_auto_polling and self._voice_backend is not None:
                self._start_voice_auto_poll(poll_interval)
            raise RuntimeError(
                "cannot respawn the receiver during an active segment; "
                "apply startup parameters between transmissions"
            )

        with self._poll_lock:
            pending_segments = list(self._voice_segments)
            pending_events = list(self._voice_events)
            carried = (
                status.completed_segments,
                status.dropped_segments,
                status.capped_closes,
            )
            params = dict(self._voice_params)
        params.update(overrides)
        # A stored bin count may be illegal at a new sample rate or frame length.
        # Clamp it rather than raising for a parameter this caller never passed --
        # but only when it *was* replayed. An explicitly supplied value must still
        # be rejected, or the same argument would behave differently depending on
        # which entry point it arrived through.
        if "audio_spectrum_bins" not in overrides and params.get("audio_spectrum_bins"):
            limit = spectrum_bin_limit(
                int(params.get("sample_rate_hz", 16_000)), int(params.get("frame_ms", 40))
            )
            stored = int(params["audio_spectrum_bins"])
            if limit < MIN_SPECTRUM_BINS:
                # Clamping to the raw cap would produce a value the validator
                # itself refuses, so the feature disables instead.
                params["audio_spectrum_bins"] = 0
            elif stored > limit:
                params["audio_spectrum_bins"] = limit
            # start_voice_segments rebuilds _voice_params from its own arguments,
            # so without restoring this afterwards the clamped value would become
            # the stored one and the setting would ratchet down permanently after
            # a temporary excursion to a lower rate.
            requested_bins = stored

        try:
            session = self.start_voice_segments(**params)  # type: ignore[arg-type]
        except Exception:
            # Do not leave the caller with a dead poller and a dropped backlog
            # because the respawn failed.
            with self._poll_lock:
                self._voice_segments[:0] = pending_segments
                self._voice_events[:0] = pending_events
            if was_auto_polling and self._voice_backend is not None:
                self._start_voice_auto_poll(poll_interval)
            raise

        with self._poll_lock:
            self._voice_segments[:0] = pending_segments
            self._voice_events[:0] = pending_events
            if requested_bins is not None:
                # Stored means "what was asked for"; the array length in status
                # means "what is in effect".
                self._voice_params["audio_spectrum_bins"] = requested_bins
            if self._voice_segmenter is not None:
                self._voice_segmenter.carry_counters(
                    completed=carried[0], dropped=carried[1], capped_closes=carried[2]
                )
        return session

    def reset_voice_segment_calibration(self) -> VoiceSegmentStatus:
        """Reset the idle FM-noise estimate without restarting PCM capture."""
        if (
            self.session is None
            or self.session.mode != "voice_segments"
            or self._voice_segmenter is None
        ):
            raise RuntimeError("voice segment capture is not active")
        with self._poll_lock:
            self._voice_segmenter.reset_calibration()
            return self._voice_segmenter.status(error=self.status.error)

    def iter_voice_segments(
        self,
        *,
        frequency_hz: int | None = None,
        modulation: str = "NFM",
        backend: str | PcmAudioBackend = "rtl_fm",
        path: str | None = None,
        gain_db: float | None = None,
        sample_rate_hz: int = 16_000,
        frame_ms: int = 40,
        threshold_db: float = 10.0,
        min_active_ms: int = 120,
        hang_time_ms: int = 600,
        min_segment_ms: int = 400,
        max_segment_sec: float = 20.0,
        pre_roll_ms: int = 200,
        detector_mode: str = "hf_ratio",
        hf_ratio_threshold: float = 1.2,
        dc_block: bool = True,
        deemphasis_us: float | None = 75.0,
        duration_sec: float | None = None,
        max_segments: int | None = None,
        debug_wav_dir: str | Path | None = None,
    ) -> Iterator[RadioVoiceSegment]:
        """Yield complete radio transmissions and always stop capture on exit."""
        if duration_sec is not None and duration_sec <= 0:
            raise ValueError("duration_sec must be greater than zero")
        if max_segments is not None and max_segments <= 0:
            raise ValueError("max_segments must be greater than zero")
        self.start_voice_segments(
            frequency_hz=frequency_hz,
            modulation=modulation,
            backend=backend,
            path=path,
            gain_db=gain_db,
            sample_rate_hz=sample_rate_hz,
            frame_ms=frame_ms,
            threshold_db=threshold_db,
            min_active_ms=min_active_ms,
            hang_time_ms=hang_time_ms,
            min_segment_ms=min_segment_ms,
            max_segment_sec=max_segment_sec,
            pre_roll_ms=pre_roll_ms,
            detector_mode=detector_mode,
            hf_ratio_threshold=hf_ratio_threshold,
            dc_block=dc_block,
            deemphasis_us=deemphasis_us,
        )
        started = time.monotonic()
        yielded = 0
        try:
            while duration_sec is None or time.monotonic() - started < duration_sec:
                self.poll()
                for segment in self.pop_voice_segments():
                    if debug_wav_dir is not None:
                        wav_path = Path(debug_wav_dir) / f"{segment.segment_id}.wav"
                        segment.save_wav(wav_path)
                        segment = replace(segment, wav_path=str(wav_path))
                    yield segment
                    yielded += 1
                    if max_segments is not None and yielded >= max_segments:
                        return
                if not self.status.process_running:
                    return
                time.sleep(0.01)
        finally:
            self.stop(clear_error=False)

    # --- priority scan (issue #7) -------------------------------------------
    #
    # Non-preemptive round-robin over a caller-supplied list of catalog
    # channels: SCANNING dwells on each channel long enough to detect a
    # carrier, LOCKED follows an active transmission to completion, then
    # advances to the next channel. Reuses self._voice_backend/
    # self._voice_segmenter (decision 2) under session.mode="priority_scan",
    # so stop()/_is_other_mode_active()/start_recording() exclusion all work
    # unchanged. Both backend and segmenter are rebuilt fresh on every
    # channel visit (decision 9) -- no state survives a visit gap except the
    # scan-lifetime completed/dropped/capped_closes totals, carried forward
    # via RadioVoiceSegmenter.carry_counters() (decision 16).

    def _resolve_priority_scan_channel(
        self, catalog: FrequencyCatalog, channel_id: str
    ) -> tuple[FrequencyCatalogRange, FrequencyChannel]:
        """Resolve a bare channel id or a 'range_id.channel_id' qualified id."""
        if "." in channel_id:
            range_id, _, bare_id = channel_id.partition(".")
            frequency_range = catalog.range_by_id(range_id)
            if frequency_range is None:
                raise ValueError(f"unknown range id in priority scan channel id: {channel_id!r}")
            for channel in frequency_range.channels:
                if channel.id == bare_id:
                    return frequency_range, channel
            raise ValueError(f"unknown channel id {bare_id!r} in range {range_id!r}")
        matches = [
            (frequency_range, channel)
            for frequency_range in catalog.ranges
            for channel in frequency_range.channels
            if channel.id == channel_id
        ]
        if not matches:
            raise ValueError(f"unknown priority scan channel id: {channel_id!r}")
        if len(matches) > 1:
            colliding = ", ".join(sorted(frequency_range.id for frequency_range, _ in matches))
            raise ValueError(
                f"ambiguous priority scan channel id {channel_id!r}: present in ranges "
                f"{colliding}; use 'range_id.channel_id' to disambiguate"
            )
        return matches[0]

    def _resolve_priority_scan_channels(
        self, channel_ids: list[str]
    ) -> list[tuple[FrequencyCatalogRange, FrequencyChannel]]:
        if not channel_ids:
            raise ValueError("channel_ids must not be empty")
        catalog = self.frequency_catalog
        seen: set[str] = set()
        resolved: list[tuple[FrequencyCatalogRange, FrequencyChannel]] = []
        for channel_id in channel_ids:
            # De-duplicated by the raw string a caller passed, preserving
            # first occurrence's position: round-robin has no use for
            # visiting the same channel twice per cycle. Deliberately *not*
            # de-duplicated by resolved channel id: ["kbuf_tower",
            # "local_airports.kbuf_tower"] still visits the same channel
            # twice, since telling those two spellings apart would need
            # resolving before de-duplicating, one extra pass this simple
            # case doesn't seem worth. detector_overrides, by contrast, is a
            # dict keyed by whatever the caller writes and so is naturally
            # de-duplicated by *resolved* id once looked up -- the two are
            # accepted to disagree on this edge case, not reconciled.
            if channel_id in seen:
                continue
            seen.add(channel_id)
            resolved.append(self._resolve_priority_scan_channel(catalog, channel_id))
        return resolved

    def _resolve_priority_scan_overrides(
        self,
        resolved: list[tuple[FrequencyCatalogRange, FrequencyChannel]],
        detector_overrides: dict[str, dict[str, object]] | None,
    ) -> dict[str, dict[str, object]]:
        """Resolve and validate ``detector_overrides`` eagerly.

        Every mistake here must surface as a ``ValueError`` from
        ``start_priority_scan`` itself, before ``self.stop()`` runs and before
        any backend is touched -- deferring validation to
        ``RadioVoiceSegmenter.__init__`` (which raises on a bad
        ``detector_mode``) means the failure instead lands wherever the
        affected channel's *visit* happens to occur, which can be well after
        the manager has already started a live backend for an earlier
        channel, or from inside ``poll()`` where a raised exception is never
        expected.
        """
        if not detector_overrides:
            return {}
        resolved_channel_ids = {channel.id for _range, channel in resolved}
        catalog = self.frequency_catalog
        allowed_keys = {"detector_mode", "hf_ratio_threshold"}
        result: dict[str, dict[str, object]] = {}
        for key, override in detector_overrides.items():
            _range, channel = self._resolve_priority_scan_channel(catalog, key)
            if channel.id not in resolved_channel_ids:
                raise ValueError(
                    f"detector_overrides key {key!r} resolves to channel {channel.id!r}, "
                    "which is not present in channel_ids"
                )
            unknown_keys = set(override) - allowed_keys
            if unknown_keys:
                raise ValueError(
                    f"detector_overrides[{key!r}] has unrecognised key(s) "
                    f"{sorted(unknown_keys)!r}; only {sorted(allowed_keys)!r} are supported"
                )
            if "detector_mode" in override and override["detector_mode"] not in DETECTOR_MODES:
                raise ValueError(
                    f"detector_overrides[{key!r}]['detector_mode'] must be one of "
                    f"{list(DETECTOR_MODES)}"
                )
            if "hf_ratio_threshold" in override:
                threshold = override["hf_ratio_threshold"]
                if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or threshold <= 0:
                    raise ValueError(
                        f"detector_overrides[{key!r}]['hf_ratio_threshold'] must be a "
                        "positive number"
                    )
            result[channel.id] = override
        return result

    def start_priority_scan(
        self,
        channel_ids: list[str],
        *,
        dwell_ms: int = 2_000,
        max_segment_sec: float = 20.0,
        hang_time_ms: int = 600,
        max_lock_ms: int | None = None,
        detector_overrides: dict[str, dict[str, object]] | None = None,
        dc_block: bool = True,
        deemphasis_us: Any = _UNSET,
        normalize: bool = False,
        normalize_target_dbfs: float = -20.0,
        gain_db: float | None = None,
        frame_ms: int = 40,
        on_segment: Callable[[RadioVoiceSegment], None] | None = None,
    ) -> RadioSession:
        """Start a non-preemptive round-robin scan over ``channel_ids``.

        v1 is non-preemptive round-robin: channels are visited in list order,
        one dwell/lock at a time; there is no priority-driven preemption of
        an active lock. Advance the scan with ``poll()``, drain completed
        segments with ``pop_voice_segments()``, and inspect state with
        ``current_priority_scan_status()`` -- or use ``priority_scan()`` for
        a blocking generator that does all three.

        Each ``channel_ids`` entry is a bare catalog channel id (must be
        unique across the whole merged catalog) or a qualified
        ``"range_id.channel_id"`` form to disambiguate. ``wbfm``/``wfm``
        channels are rejected: ``RtlFmAudioBackend`` cannot express that
        modulation at an explicit sample rate without emitting a broken
        rtl_fm command. ``detector_overrides`` maps a channel id (same bare-
        or-qualified form) to a ``{"detector_mode": ..., "hf_ratio_threshold":
        ...}`` override for that channel only -- an unknown key raises rather
        than being silently ignored. AM channels are detection-unvalidated
        for v1 (the ``hf_ratio`` gate was tuned on NFM captures) but get
        ``deemphasis_us=None`` automatically unless the caller passes an
        explicit value, since AM audio was never FM pre-emphasised.
        """
        if dwell_ms <= 0:
            raise ValueError("dwell_ms must be greater than zero")
        if max_lock_ms is not None and max_lock_ms <= 0:
            raise ValueError("max_lock_ms must be greater than zero")
        resolved = self._resolve_priority_scan_channels(channel_ids)
        overrides = self._resolve_priority_scan_overrides(resolved, detector_overrides)
        effective_max_lock_ms = (
            max_lock_ms
            if max_lock_ms is not None
            else int(max_segment_sec * 1000) + hang_time_ms + 1_000
        )
        channels: list[_PriorityScanChannel] = []
        for frequency_range, channel in resolved:
            modulation = channel.modulation or frequency_range.default_modulation or "NFM"
            rtl_fm_mode = _priority_scan_rtl_fm_mode(modulation)
            if rtl_fm_mode == "wbfm":
                raise ValueError(
                    f"channel {channel.id!r} resolves to wbfm modulation, which "
                    "start_priority_scan does not support (RtlFmAudioBackend cannot "
                    "express wbfm at an explicit sample rate)"
                )
            sample_rate_hz = rtl_fm_audio_rate_hz(modulation)
            is_am = rtl_fm_mode == "am"
            channel_deemphasis_us = (
                deemphasis_us if deemphasis_us is not _UNSET else (None if is_am else 75.0)
            )
            override = overrides.get(channel.id, {})
            channels.append(
                _PriorityScanChannel(
                    channel_id=channel.id,
                    channel_label=channel.label,
                    range_id=frequency_range.id,
                    range_label=frequency_range.label,
                    frequency_hz=channel.frequency_hz,
                    modulation=modulation,
                    sample_rate_hz=sample_rate_hz,
                    deemphasis_us=channel_deemphasis_us,
                    detector_mode=str(override.get("detector_mode", "hf_ratio")),
                    hf_ratio_threshold=float(override.get("hf_ratio_threshold", 1.2)),
                )
            )
        self.stop()
        session = RadioSession(
            session_id=f"session-{uuid4()}",
            mode="priority_scan",
            receiver_id=self.receiver.id,
            status="starting",
            decoder="rtl_fm",
        )
        self.session = session
        self.status = SensorStatus(sensor_id=self.receiver.id, mode="priority_scan", tool_found=False)
        state = _PriorityScanState(
            scan_id=f"priority-scan-{uuid4()}",
            session_id=session.session_id,
            channels=channels,
            dwell_ms=dwell_ms,
            max_lock_ms=effective_max_lock_ms,
            max_segment_sec=max_segment_sec,
            hang_time_ms=hang_time_ms,
            frame_ms=frame_ms,
            dc_block=dc_block,
            normalize=normalize,
            normalize_target_dbfs=normalize_target_dbfs,
            gain_db=gain_db,
        )
        self._priority_scan_state = state
        self._voice_segment_callback = on_segment
        # A priority scan has no on_event parameter of its own (v1 doesn't
        # expose one), so any callback left over from a prior
        # start_voice_segments(on_event=...) call must not silently receive
        # this scan's capture_started/capture_stopped events.
        self._voice_event_callback = None
        self._voice_segments.clear()
        try:
            self._start_priority_scan_visit(state)
        except FileNotFoundError as exc:
            self._priority_scan_state = None
            session.status = "error"
            self.status.error = str(exc)
            raise RuntimeError(self.status.error) from exc
        except ValueError as exc:
            # Defense in depth: _resolve_priority_scan_overrides already
            # validates detector_overrides eagerly, so this should be
            # unreachable via normal API misuse, but a future construction
            # failure of some other kind must still leave the manager in a
            # clean IDLE-like state rather than a half-started scan whose
            # session.status is stuck at "starting".
            self._priority_scan_state = None
            session.status = "error"
            self.status.error = str(exc)
            raise
        session.status = "running"
        self.status.process_running = self._voice_backend.is_running() if self._voice_backend else False
        self.status.tool_found = True
        self._emit_voice_event("capture_started", state="calibrating")
        return session

    def _start_priority_scan_visit(
        self, state: _PriorityScanState, *, keep_backend: bool = False
    ) -> None:
        """(Re)build the backend/segmenter for ``state``'s current channel.

        Only rebuilds the backend when ``keep_backend`` is false (or none
        exists yet) -- the single-channel short-circuit passes
        ``keep_backend=True`` to avoid a pointless device re-open. The
        segmenter is always rebuilt fresh (decision 9): its run counters are
        seeded from ``state``'s scan-lifetime totals via ``carry_counters()``
        so they read as scan totals, not per-visit counts (decision 16).

        Any failure here -- either ``backend.start()`` (``FileNotFoundError``)
        or segmenter construction (a ``ValueError``, since
        ``detector_overrides`` values are validated eagerly in
        ``_resolve_priority_scan_overrides`` but this is still defense in
        depth against any other future construction failure) -- must not
        leave a started backend stranded holding the RTL-SDR with nothing
        recorded about it. If this call built a fresh backend, that backend
        is stopped and ``self._voice_backend`` cleared before the exception
        propagates.
        """
        channel = state.channels[state.index]
        built_backend_this_call = not keep_backend or self._voice_backend is None
        if built_backend_this_call:
            path = self._decoder_path("rtl_fm", "rtl_fm")
            config_fir, config_atan, config_args = self._decoder_settings("rtl_fm")
            backend = RtlFmAudioBackend(
                path=path,
                frequency_hz=channel.frequency_hz,
                modulation=channel.modulation,
                sample_rate_hz=channel.sample_rate_hz,
                frame_ms=state.frame_ms,
                ppm=self.receiver.ppm,
                gain_db=state.gain_db,
                fir_size=config_fir,
                atan_math=config_atan,
                extra_args=config_args,
            )
            try:
                backend.start()
            except FileNotFoundError as exc:
                raise FileNotFoundError(f"{path or 'voice backend'} not found") from exc
            self._voice_backend = backend
        try:
            self._voice_segmenter = RadioVoiceSegmenter(
                session_id=state.session_id,
                frequency_hz=channel.frequency_hz,
                modulation=channel.modulation,
                sample_rate_hz=channel.sample_rate_hz,
                frame_ms=state.frame_ms,
                hang_time_ms=state.hang_time_ms,
                max_segment_sec=state.max_segment_sec,
                detector_mode=channel.detector_mode,
                hf_ratio_threshold=channel.hf_ratio_threshold,
                conditioner=AudioConditioner(
                    sample_rate_hz=channel.sample_rate_hz,
                    dc_block=state.dc_block,
                    deemphasis_us=channel.deemphasis_us,
                    normalize=state.normalize,
                    normalize_target_dbfs=state.normalize_target_dbfs,
                ),
            )
        except Exception:
            if built_backend_this_call and self._voice_backend is not None:
                self._voice_backend.stop()
                self._voice_backend = None
            raise
        self._voice_segmenter.carry_counters(
            completed=state.completed, dropped=state.dropped, capped_closes=state.capped_closes
        )
        self.status.process_running = self._voice_backend.is_running() if self._voice_backend else False

    def _advance_priority_scan_channel(self, state: _PriorityScanState) -> None:
        """Tear down the current visit and move to the next channel."""
        outgoing = self._voice_segmenter
        if outgoing is not None:
            outgoing_status = outgoing.status()
            state.completed = outgoing_status.completed_segments
            state.dropped = outgoing_status.dropped_segments
            state.capped_closes = outgoing_status.capped_closes
        keep_backend = len(state.channels) == 1
        if not keep_backend and self._voice_backend is not None:
            self._voice_backend.stop()
            self._voice_backend = None
        next_index = (state.index + 1) % len(state.channels)
        if next_index == 0:
            state.cycle_count += 1
        state.index = next_index
        state.mode = "scanning"
        state.dwell_frame_count = 0
        state.lock_frame_count = 0
        try:
            self._start_priority_scan_visit(state, keep_backend=keep_backend)
        except (FileNotFoundError, ValueError) as exc:
            # ValueError is defense in depth: _resolve_priority_scan_overrides
            # already validates detector_overrides eagerly at start_priority_scan
            # time, so a bad value should never reach here -- but a mid-scan
            # construction failure on any *other* future ground must abort the
            # whole scan the same way a missing backend binary does (decision
            # 12), not raise out of poll(), which every other mode treats as
            # non-raising.
            self.status.error = str(exc)
            self.stop(clear_error=False)

    def _fail_priority_scan(self, stderr_lines: list[str], *, fallback: str) -> None:
        """Abort the whole scan to IDLE on a mid-scan backend failure (decision 12)."""
        self._set_process_exit_error(stderr_lines, fallback=fallback)
        self.stop(clear_error=False)

    def ingest_priority_scan(self) -> int:
        """Advance an active priority scan by one poll's worth of frames.

        ``backend.read_frames()`` materializes the whole buffered batch up
        front, so once a channel switch happens mid-batch the remaining
        frames in that batch belong to the *old* channel's RF -- feeding
        them into the newly built segmenter would misattribute stale,
        pre-retune audio to the new channel. So a switch always stops
        draining this batch immediately; any leftover frames are simply not
        processed this poll (the new backend will have fresh frames of its
        own by the next one).
        """
        state = self._priority_scan_state
        if not self.session or self.session.mode != "priority_scan" or state is None:
            return 0
        backend = self._voice_backend
        segmenter = self._voice_segmenter
        if backend is None or segmenter is None:
            return 0
        emitted = 0
        switched = False
        for frame in backend.read_frames():
            channel = state.channels[state.index]
            segments = segmenter.ingest(frame)
            now_active = segmenter.status().active
            for segment in segments:
                tagged = replace(
                    segment,
                    metadata={
                        **segment.metadata,
                        "channel_id": channel.channel_id,
                        "channel_label": channel.channel_label,
                        "range_id": channel.range_id,
                        "range_label": channel.range_label,
                    },
                )
                self._voice_segments.append(tagged)
                self._notify_voice_segment(tagged)
                emitted += 1
            if state.mode == "scanning":
                state.dwell_frame_count += 1
                if now_active or segments:
                    state.mode = "locked"
                    state.lock_frame_count = 0
                elif state.dwell_frame_count * state.frame_ms >= state.dwell_ms:
                    self._advance_priority_scan_channel(state)
                    switched = True
                    break
            else:  # locked
                state.lock_frame_count += 1
                timed_out = state.lock_frame_count * state.frame_ms >= state.max_lock_ms
                if (not now_active) or timed_out:
                    self._advance_priority_scan_channel(state)
                    switched = True
                    break
        if switched:
            # A mid-scan failure inside _advance_priority_scan_channel already
            # called stop() (clearing _priority_scan_state and the backend);
            # nothing further to check against a torn-down manager.
            if self._priority_scan_state is None:
                return emitted
            backend = self._voice_backend
        if backend is None:
            return emitted
        stderr_lines = backend.read_stderr_lines()
        self.status.process_running = backend.is_running()
        if not self.status.process_running and self.session.status == "running":
            self._fail_priority_scan(stderr_lines, fallback="priority scan process stopped")
            return emitted
        self._message_count += emitted
        self.status.message_count = self._message_count
        return emitted

    def current_priority_scan_status(self) -> PriorityScanStatus | None:
        """Return the current priority scan's status, or ``None`` if none is active."""
        state = self._priority_scan_state
        if state is None:
            return None
        channel = state.channels[state.index]
        segmenter_status = self._voice_segmenter.status() if self._voice_segmenter else None
        if self.status.error:
            scan_state = "error"
        elif not (self.session and self.session.status == "running"):
            scan_state = "stopped"
        elif state.mode == "locked":
            scan_state = "locked"
        else:
            scan_state = "scanning"
        return PriorityScanStatus(
            scan_id=state.scan_id,
            session_id=state.session_id,
            channel_ids=[c.channel_id for c in state.channels],
            state=scan_state,
            current_channel_id=channel.channel_id,
            current_channel_label=channel.channel_label,
            current_range_id=channel.range_id,
            current_range_label=channel.range_label,
            cycle_count=state.cycle_count,
            completed_segments=(
                segmenter_status.completed_segments if segmenter_status else state.completed
            ),
            dropped_segments=(
                segmenter_status.dropped_segments if segmenter_status else state.dropped
            ),
            capped_closes=(
                segmenter_status.capped_closes if segmenter_status else state.capped_closes
            ),
            error=self.status.error,
            noise_floor_db=segmenter_status.noise_floor_db if segmenter_status else None,
            last_frame_band_ratio=(
                segmenter_status.last_frame_band_ratio if segmenter_status else None
            ),
            active=segmenter_status.active if segmenter_status else False,
            recalibrating=segmenter_status.recalibrating if segmenter_status else False,
            started_at=state.started_at,
        )

    def priority_scan_dict(self) -> dict[str, object] | None:
        status = self.current_priority_scan_status()
        return status.to_dict() if status else None

    def priority_scan(
        self,
        channel_ids: list[str],
        *,
        dwell_ms: int = 2_000,
        max_segment_sec: float = 20.0,
        hang_time_ms: int = 600,
        max_lock_ms: int | None = None,
        detector_overrides: dict[str, dict[str, object]] | None = None,
        dc_block: bool = True,
        deemphasis_us: Any = _UNSET,
        normalize: bool = False,
        normalize_target_dbfs: float = -20.0,
        gain_db: float | None = None,
        frame_ms: int = 40,
        duration_sec: float | None = None,
        max_segments: int | None = None,
        debug_wav_dir: str | Path | None = None,
    ) -> Iterator[RadioVoiceSegment]:
        """Blocking generator over ``start_priority_scan``: yield each segment as it completes.

        v1 is non-preemptive round-robin (see ``start_priority_scan``). Calls
        ``start_priority_scan`` internally, polls on a fixed safe interval,
        and always stops the scan on exit -- normal exhaustion, an early
        ``break``/``return`` from the consuming loop, or a
        ``KeyboardInterrupt``/``GeneratorExit`` raised while iterating. This
        is the primary entry point for a script author (``import olab_rf``);
        ``start_priority_scan()``/``poll()`` remain available directly for a
        caller (e.g. a GUI) that needs to integrate scanning into its own
        request/response cycle without blocking.

        Mirrors ``iter_voice_segments()``'s shape exactly (parameter names,
        the 10ms poll interval, the ``try/finally`` teardown) rather than
        inventing a new one.
        """
        if duration_sec is not None and duration_sec <= 0:
            raise ValueError("duration_sec must be greater than zero")
        if max_segments is not None and max_segments <= 0:
            raise ValueError("max_segments must be greater than zero")
        self.start_priority_scan(
            channel_ids,
            dwell_ms=dwell_ms,
            max_segment_sec=max_segment_sec,
            hang_time_ms=hang_time_ms,
            max_lock_ms=max_lock_ms,
            detector_overrides=detector_overrides,
            dc_block=dc_block,
            deemphasis_us=deemphasis_us,
            normalize=normalize,
            normalize_target_dbfs=normalize_target_dbfs,
            gain_db=gain_db,
            frame_ms=frame_ms,
        )
        started = time.monotonic()
        yielded = 0
        try:
            while duration_sec is None or time.monotonic() - started < duration_sec:
                self.poll()
                for segment in self.pop_voice_segments():
                    if debug_wav_dir is not None:
                        wav_path = Path(debug_wav_dir) / f"{segment.segment_id}.wav"
                        segment.save_wav(wav_path)
                        segment = replace(segment, wav_path=str(wav_path))
                    yield segment
                    yielded += 1
                    if max_segments is not None and yielded >= max_segments:
                        return
                if not self.status.process_running:
                    return
                time.sleep(0.01)
        finally:
            self.stop(clear_error=False)

    def _start_process_mode(
        self,
        *,
        mode: str,
        decoder: str,
        command: list[str],
        shell: bool = False,
    ) -> RadioSession:
        self.stop()
        session = RadioSession(
            session_id=f"session-{uuid4()}",
            mode=mode,
            receiver_id=self.receiver.id,
            status="starting",
            decoder=decoder,
            command=command,
        )
        self.session = session
        self.status = SensorStatus(sensor_id=self.receiver.id, mode=mode, tool_found=False)
        self._message_count = 0
        self._process = DecoderProcess(command=command, shell=shell)
        try:
            self._process.start()
        except FileNotFoundError as exc:
            session.status = "error"
            self.status.error = f"{command[0]} not found"
            raise RuntimeError(self.status.error) from exc
        session.status = "running"
        self.status.process_running = self._process.is_running()
        self.status.tool_found = True
        return session

    def _start_voice_auto_poll(self, interval_sec: float) -> None:
        self._stop_voice_auto_poll()
        stop_event = Event()
        self._voice_poll_stop = stop_event

        def run() -> None:
            while not stop_event.is_set() and self.voice_capture_running():
                self.poll()
                stop_event.wait(interval_sec)

        self._voice_poll_thread = Thread(
            target=run,
            name=f"olab-rf-voice-poll-{self.receiver.id}",
            daemon=True,
        )
        self._voice_poll_thread.start()

    def _emit_voice_event(
        self,
        event: str,
        *,
        state: str,
        segment_id: str | None = None,
        message: str | None = None,
    ) -> None:
        if self.session is None:
            return
        voice_event = VoiceCaptureEvent(
            event=event,
            session_id=self.session.session_id,
            state=state,
            segment_id=segment_id,
            message=message,
        )
        self._voice_events.append(voice_event)
        if self._voice_event_callback:
            try:
                self._voice_event_callback(voice_event)
            except Exception as exc:
                self.status.error = f"voice event callback failed: {exc}"

    def _notify_voice_segment(self, segment: RadioVoiceSegment) -> None:
        if self._voice_segment_callback:
            try:
                self._voice_segment_callback(segment)
            except Exception as exc:
                self.status.error = f"voice segment callback failed: {exc}"

    def _stop_voice_auto_poll(self) -> None:
        if self._voice_poll_stop is not None:
            self._voice_poll_stop.set()
            self._voice_poll_stop = None
        thread, self._voice_poll_thread = self._voice_poll_thread, None
        if thread is not None and thread is not current_thread():
            thread.join(timeout=1.0)

    def advance_replay(self, messages_per_tick: int = 2) -> None:
        if not self.status.process_running or self.session is None or self.session.mode != "replay":
            return
        if self._replay_messages is None:
            return
        try:
            for _ in range(messages_per_tick):
                message = next(self._replay_messages)
                if message.track:
                    self.track_store.upsert(message.track)
                    self.status.last_message_at = message.track.last_seen
                    if self.history:
                        self.history.upsert_track(message.track)
                if self.history:
                    self.history.add_observation(message.observation)
                self._message_count += 1
        except StopIteration:
            self._replay_messages = None
            self.status.process_running = False
            if self.session:
                self.session.status = "complete"
        self.status.message_count = self._message_count
        self.status.messages_per_second = float(self._message_count)

    def ingest_adsb_json(self) -> int:
        if not self.session or self.session.mode != "adsb" or not self._readsb_json_dir:
            return 0
        stderr_lines = self._process.read_stderr_lines() if self._process else []
        messages = parse_readsb_aircraft_file(
            self._readsb_json_dir / "aircraft.json",
            sensor_id=self.receiver.id,
            session_id=self.session.session_id,
        )
        for message in messages:
            if message.track:
                self.track_store.upsert(message.track)
                self.status.last_message_at = message.track.last_seen
                if self.history:
                    self.history.upsert_track(message.track)
            if self.history:
                self.history.add_observation(message.observation)
        if messages:
            self._message_count += len(messages)
            self.status.message_count = self._message_count
            self.status.messages_per_second = float(len(messages))
        if self._process:
            self.status.process_running = self._process.is_running()
            if not self.status.process_running and self.session.status == "running":
                self.session.status = "stopped"
                self._set_process_exit_error(stderr_lines, fallback="readsb process stopped")
        return len(messages)

    def ingest_ais_stdout(self) -> int:
        if not self.session or self.session.mode != "ais" or not self._process:
            return 0
        count = 0
        stdout_lines = self._process.read_stdout_lines()
        stderr_lines = self._process.read_stderr_lines()
        lines = stdout_lines + stderr_lines
        for line in lines:
            message = parse_ais_nmea_line(
                line,
                sensor_id=self.receiver.id,
                session_id=self.session.session_id,
            )
            if not message:
                continue
            if message.track:
                self.track_store.upsert(message.track)
                self.status.last_message_at = message.track.last_seen
                if self.history:
                    self.history.upsert_track(message.track)
            if self.history:
                self.history.add_observation(message.observation)
            count += 1
        if count:
            self._message_count += count
            self.status.message_count = self._message_count
            self.status.messages_per_second = float(count)
        self.status.process_running = self._process.is_running()
        if not self.status.process_running and self.session.status == "running":
            self.session.status = "stopped"
            self._set_process_exit_error(stderr_lines, fallback="rtl_ais process stopped")
        return count

    def ingest_listen_stdout(self) -> int:
        if not self.session or self.session.mode != "listen" or not self._process:
            return 0
        lines = self._process.read_stdout_lines() + self._process.read_stderr_lines()
        for line in lines:
            lowered = line.lower()
            if "error" in lowered or "failed" in lowered:
                self.status.error = line
        self.status.process_running = self._process.is_running()
        if not self.status.process_running and self.session.status == "running":
            self.session.status = "stopped"
            if not self.status.error:
                self.status.error = "listen process stopped"
        return len(lines)

    def ingest_spectrum_stdout(self) -> int:
        if not self.session or self.session.mode != "spectrum" or not self._process:
            return 0
        count = 0
        stdout_lines = self._process.read_stdout_lines()
        stderr_lines = self._process.read_stderr_lines()
        for line in stdout_lines:
            snapshot = parse_rtl_power_line(line)
            if not snapshot:
                continue
            self._spectrum = snapshot
            self._record_spectrum_events(snapshot)
            self._spectrum_history.append(snapshot)
            self._spectrum_history = self._spectrum_history[-self.spectrum_history_limit :]
            self.status.last_message_at = snapshot.captured_at
            count += 1
        if stderr_lines and not count:
            self.status.error = stderr_lines[-1]
        if count:
            self._message_count += count
            self.status.message_count = self._message_count
            self.status.messages_per_second = float(count)
            self.status.error = None
        self.status.process_running = self._process.is_running()
        if not self.status.process_running and self.session.status == "running":
            self.session.status = "stopped"
            if not self._spectrum.bins and not self.status.error:
                self.status.error = "rtl_power stopped before producing sweep data"
        return count

    def ingest_frequency_scan_stdout(self) -> int:
        if (
            not self.session
            or self.session.mode not in {"frequency_scan", "frequency_baseline"}
            or not self._process
            or not self._frequency_scan
        ):
            return 0
        count = 0
        stdout_lines = self._process.read_stdout_lines()
        stderr_lines = self._process.read_stderr_lines()
        for line in stdout_lines:
            snapshot = parse_rtl_power_line(line)
            if not snapshot:
                continue
            count += 1
            self._record_frequency_scan_snapshot(snapshot)
            self.status.last_message_at = snapshot.captured_at
        if stderr_lines and not count:
            self.status.error = stderr_lines[-1]
        if count:
            self._message_count += count
            self.status.message_count = self._message_count
            self.status.messages_per_second = float(count)
            self.status.error = None
        self.status.process_running = self._process.is_running()
        self._update_frequency_scan_progress()
        if self._frequency_scan and self._frequency_scan.status == "running":
            elapsed = self._frequency_scan.elapsed_sec
            if elapsed >= self._frequency_scan.request.duration_sec:
                self._complete_frequency_scan()
            elif not self.status.process_running and self.session.status == "running":
                self._complete_frequency_scan(error=self.status.error)
        return count

    def poll(self) -> SensorStatus:
        """Advance the active workflow and return the current receiver status."""
        with self._poll_lock:
            self.advance_replay()
            self.ingest_adsb_json()
            self.ingest_ais_stdout()
            self.ingest_spectrum_stdout()
            self.ingest_listen_stdout()
            self.ingest_frequency_scan_stdout()
            self.ingest_voice_segments()
            self.ingest_priority_scan()
            self._poll_digital_listen()
            self.ingest_recording()
        return self.status

    def poll_frequency_scan(self) -> FrequencyScanStatus | None:
        """Advance only an active frequency scan or baseline capture."""
        with self._poll_lock:
            self.ingest_frequency_scan_stdout()
            return self._frequency_scan

    def stop(
        self,
        *,
        clear_previous: bool = True,
        clear_error: bool = True,
        stop_active_recording: bool = False,
    ) -> None:
        """Stop the active workflow.

        By default this also clears any stored previous request used by
        ``resume_previous``. If an IQ recording is active, ``stop()`` raises
        unless ``stop_active_recording=True`` is passed — silently
        discarding a partially-written corpus file is not this method's
        default behavior. Pass ``stop_active_recording=True`` to end the
        recording as part of this call (the same finalize
        ``stop_recording()`` performs).
        """
        if self._recording and self._recording.status == "running":
            if not stop_active_recording:
                raise RuntimeError(
                    "an IQ recording is active; call stop_recording() or "
                    "stop(stop_active_recording=True) first"
                )
            self._finalize_recording("stopped")
        self._stop_voice_auto_poll()
        if self._voice_backend and self.session and self.session.mode in (
            "voice_segments",
            "priority_scan",
        ):
            self._emit_voice_event("capture_stopped", state="stopped")
        if self._process:
            self._process.stop()
            self._process = None
        if self._digital_backend:
            self._digital_backend.stop()
            self._digital_backend = None
        if self._digital_status:
            self._digital_status.state = "stopped"
            self._digital_status.process_running = False
        if self._voice_backend:
            self._voice_backend.stop()
            self._voice_backend = None
        self._voice_segmenter = None
        self._voice_segments.clear()
        self._priority_scan_state = None
        if self.session:
            self.session.status = "stopped"
        self.status.process_running = False
        self.status.mode = "idle"
        if clear_error:
            self.status.error = None
        self._replay_messages = None
        self._readsb_json_dir = None
        self._cleanup_readsb_temp_dir()
        self._clear_spectrum()
        if clear_previous:
            self._previous_request = None

    def _decoder_settings(self, name: str) -> tuple[int | None, str | None, list[str]]:
        """Return configured ``(fir_size, atan_math, args)`` for a decoder.

        Config that parses and validates but never reaches the command line is
        the same silent-drop trap the validation was added to remove, just moved
        one layer down.
        """
        if self.config and name in self.config.decoders:
            decoder = self.config.decoders[name]
            return decoder.fir_size, decoder.atan_math, list(decoder.args)
        return None, None, []

    def _decoder_path(self, name: str, default: str) -> str:
        if self.config and name in self.config.decoders:
            return self.config.decoders[name].path
        return default

    def _poll_digital_listen(self) -> None:
        if not self._digital_backend or not self._digital_status:
            return
        lines = self._digital_backend.stderr_lines()
        self._digital_status.stderr = _last_nonempty(lines) or self._digital_status.stderr
        running = self._digital_backend.is_running()
        self._digital_status.process_running = running
        self.status.process_running = running
        if not running and self.session and self.session.status == "running":
            self.session.status = "stopped"
            self._digital_status.state = "stopped"
            self._digital_status.error = self._digital_status.stderr or "SDRTrunk process stopped"
            self.status.error = self._digital_status.error

    def _frequency_scan_backend_path(self, backend: str) -> str:
        if backend == "rtl_sdr_iq":
            return self._decoder_path("rtl_sdr", "rtl_sdr")
        return self._decoder_path("rtl_power", "rtl_power")

    def _cleanup_readsb_temp_dir(self) -> None:
        if self._readsb_temp_dir is not None:
            self._readsb_temp_dir.cleanup()
            self._readsb_temp_dir = None

    def _set_process_exit_error(self, stderr_lines: list[str], *, fallback: str) -> None:
        if self.status.error:
            return
        diagnostic = _last_nonempty(stderr_lines)
        self.status.error = diagnostic or fallback

    def _is_other_mode_active(self) -> bool:
        """True when a live device-mode backend is running.

        Recording never sets ``self.session``/``self._process`` (see
        ``start_recording``), so this predicate cannot see a recording's own
        state by construction — no special-case exclusion needed.
        """
        return bool(
            self.session is not None
            and self.session.status == "running"
            and (self._process or self._digital_backend or self._voice_backend)
        )

    def start_recording(self, request: RecordingRequest) -> RecordingStatus:
        """Start recording. Only ``kind="iq"`` is implemented.

        Captures raw ``cu8`` IQ samples straight to a SigMF ``.sigmf-meta``/
        ``.sigmf-data`` pair via a continuous, unbounded ``rtl_sdr`` process
        (see ``olab_rf.decoders.sigmf``). Does not create a ``RadioSession``
        or touch ``self.status`` — a recording is tracked exclusively through
        ``current_recording()``, independent of the scan/listen/digital/voice/
        ADS-B/AIS device-mode subsystem the rest of this class maintains.
        """
        if self._recording and self._recording.status == "running":
            raise RuntimeError("recording is already active")
        if request.kind != "iq":
            self._recording = RecordingStatus(
                request=request,
                status="error",
                error="recording is designed but not implemented",
            )
            return self._recording
        if request.rotate_seconds is not None or request.max_bytes is not None:
            raise NotImplementedError(
                "rotate_seconds/max_bytes are not implemented for kind='iq'"
            )
        if self._is_other_mode_active():
            raise RuntimeError(
                "another mode is active; stop it before starting a recording"
            )

        meta_path, data_path = sigmf_paths(request.path)
        if meta_path.exists() or data_path.exists():
            existing = meta_path if meta_path.exists() else data_path
            raise RuntimeError(f"recording target already exists: {existing}")
        data_path.parent.mkdir(parents=True, exist_ok=True)

        assert request.frequency_hz is not None  # enforced by __post_init__
        assert request.sample_rate_hz is not None  # enforced by __post_init__
        command = rtl_sdr_iq_command(
            path=self._decoder_path("rtl_sdr", "rtl_sdr"),
            center_frequency_hz=request.frequency_hz,
            sample_rate_hz=request.sample_rate_hz,
            sample_count=None,
            device_index=request.device_index,
            gain_db=_receiver_gain_db(self.receiver.gain, request.gain_db),
            ppm=self.receiver.ppm,
            output_path=str(data_path),
        )
        process = DecoderProcess(command=command)
        try:
            process.start()
        except FileNotFoundError as exc:
            error = f"{command[0]} not found"
            self._recording = RecordingStatus(request=request, status="error", error=error)
            raise RuntimeError(error) from exc

        started_at = utc_now()
        try:
            write_sigmf_meta(
                meta_path,
                sample_rate_hz=request.sample_rate_hz,
                frequency_hz=request.frequency_hz,
                datetime_iso=dt_to_iso(started_at),
            )
        except OSError as exc:
            # Never leave a live process behind with no "running" status
            # tracking it — that process would be unreachable by both
            # stop() (keyed off self._process, which recording never uses)
            # and stop_recording() (keyed off a "running" self._recording).
            process.stop()
            error = f"failed to write recording metadata: {exc}"
            self._recording = RecordingStatus(request=request, status="error", error=error)
            raise RuntimeError(error) from exc

        self._recording_process = process
        self._recording = RecordingStatus(
            request=request,
            status="running",
            started_at=started_at,
            bytes_written=0,
        )
        return self._recording

    def ingest_recording(self) -> None:
        """Advance an active recording: refresh bytes written, detect death."""
        if self._recording is None or self._recording.status != "running":
            return
        process = self._recording_process
        stderr_lines = process.read_stderr_lines() if process else []
        running = process.is_running() if process else False
        if not running:
            diagnostic = _last_nonempty(stderr_lines) or "rtl_sdr process stopped"
            self._finalize_recording("error", error=diagnostic)
            return
        _, data_path = sigmf_paths(self._recording.request.path)
        try:
            bytes_written = data_path.stat().st_size if data_path.exists() else 0
        except OSError:
            bytes_written = self._recording.bytes_written or 0
        self._recording = replace(self._recording, bytes_written=bytes_written)

    def _finalize_recording(
        self,
        status: Literal["stopped", "error"],
        *,
        error: str | None = None,
    ) -> None:
        """Stop the capture process and write the final SigMF sidecar.

        Never propagates an exception — this runs from inside
        ``ingest_recording()``/``poll()`` (shared with every other mode's
        ingestion) as well as from ``stop()``/``stop_recording()``, so a
        recording-side failure must land in ``RecordingStatus.error`` rather
        than aborting the caller.
        """
        if self._recording is None:
            return
        request = self._recording.request
        if self._recording_process is not None:
            try:
                self._recording_process.stop()
            except OSError:
                pass
            self._recording_process = None
        bytes_written = self._recording.bytes_written or 0
        finalize_error = error
        try:
            meta_path, data_path = sigmf_paths(request.path)
            bytes_written = truncate_to_iq_pairs(data_path)
            assert request.sample_rate_hz is not None
            assert request.frequency_hz is not None
            write_sigmf_meta(
                meta_path,
                sample_rate_hz=request.sample_rate_hz,
                frequency_hz=request.frequency_hz,
                datetime_iso=dt_to_iso(self._recording.started_at),
                # A recording stopped before rtl_sdr wrote anything has
                # bytes_written == 0; a zero-length annotation is degenerate
                # under the spec (an annotation is meant to apply to
                # samples), so treat it the same as "no count yet" rather
                # than writing one.
                sample_count=(bytes_written // 2) or None,
            )
        except OSError as exc:
            finalize_error = finalize_error or f"failed to finalize recording: {exc}"
        self._recording = replace(
            self._recording,
            status="error" if finalize_error else status,
            stopped_at=utc_now(),
            bytes_written=bytes_written,
            error=finalize_error,
        )

    def stop_recording(self) -> RecordingStatus | None:
        """Stop the active recording, if any.

        A recording whose process has already died but whose ``poll()``
        hasn't run yet still reports ``status == "running"`` here — the same
        way ``stop()`` treats it — since ``poll()`` catching up is what
        clears it, not a bug.
        """
        if self._recording is None:
            return None
        if self._recording.status == "running":
            self._finalize_recording("stopped")
        return self._recording

    def current_recording(self) -> RecordingStatus | None:
        """Return the active or most recent recording status."""
        return self._recording

    def status_dict(self) -> dict[str, object]:
        return self.status.to_dict()

    def session_dict(self) -> dict[str, object] | None:
        return self.session.to_dict() if self.session else None

    def spectrum_dict(self) -> dict[str, object]:
        payload = self._spectrum.to_dict()
        payload["error"] = self.status.error if self.session and self.session.mode == "spectrum" else None
        payload["history"] = [
            snapshot.to_dict() for snapshot in self._spectrum_history[-self.spectrum_history_limit :]
        ]
        payload["peak_hold"] = [item.to_dict() for item in self._spectrum_peak_hold()]
        payload["noise_floor_db"] = self._spectrum_noise_floor()
        payload["event_threshold_db"] = self._spectrum_threshold_db
        payload["events"] = [event.to_dict() for event in self._spectrum_events[-self.spectrum_event_limit :]]
        payload["watch"] = self.watch_dict()
        return payload

    def current_spectrum(self) -> SpectrumSnapshot:
        """Return the latest live spectrum snapshot."""
        return self._spectrum

    def spectrum_history(self, limit: int | None = None) -> list[SpectrumSnapshot]:
        """Return recent spectrum snapshots, oldest to newest."""
        if limit is None:
            limit = self.spectrum_history_limit
        if limit <= 0:
            return []
        return list(self._spectrum_history[-limit:])

    def spectrum_events(self, limit: int | None = None) -> list[SpectrumEvent]:
        """Return recent in-memory spectrum events, oldest to newest."""
        if limit is None:
            limit = self.spectrum_event_limit
        if limit <= 0:
            return []
        return list(self._spectrum_events[-limit:])

    def catalog_with_favorites(self) -> FrequencyCatalog:
        """Return the configured catalog overlaid with SQLite favorites."""
        return self._catalog_with_history_favorites()

    def frequency_catalog_dict(self) -> dict[str, object]:
        return self.catalog_with_favorites().to_dict()

    def current_frequency_scan(self) -> FrequencyScanStatus | None:
        """Return the current or most recently completed frequency scan."""
        return self._frequency_scan

    def frequency_scan_dict(self) -> dict[str, object] | None:
        scan = self.current_frequency_scan()
        if not scan:
            return None
        payload = scan.to_dict()
        if self.session and self.session.session_id == scan.session_id:
            payload["command"] = self.session.command
            payload["decoder"] = self.session.decoder
        return payload

    def latest_frequency_baseline(self) -> FrequencyBaseline | None:
        return self._frequency_scan_baseline

    def resume_previous(self) -> RadioSession | None:
        """Restart the one previously interrupted resumable workflow, if any."""
        if not self._previous_request:
            return None
        mode, kwargs = self._previous_request
        self._previous_request = None
        if mode == "spectrum":
            return self.start_spectrum(**kwargs)
        return None

    def set_watch_frequency(
        self,
        frequency_hz: int,
        modulation: str | None = None,
    ) -> dict[str, object]:
        self._watch_frequency_hz = frequency_hz
        if modulation:
            self._watch_modulation = modulation
        return self.watch_dict()

    def watch_dict(self) -> dict[str, object]:
        command = None
        play_command = None
        if self._watch_frequency_hz:
            command = rtl_fm_command(
                path=self._demod_path,
                frequency_hz=self._watch_frequency_hz,
                modulation=self._watch_modulation,
                device_index=0,
                ppm=self.receiver.ppm,
            )
            audio_rate_hz = rtl_fm_audio_rate_hz(self._watch_modulation)
            play_command = (
                f"{shlex.join(command)} | "
                f"aplay -r {audio_rate_hz} -f S16_LE -t raw -c 1"
            )
        return {
            "frequency_hz": self._watch_frequency_hz,
            "modulation": self._watch_modulation,
            "demod_path": self._demod_path,
            "command": command,
            "play_command": play_command,
        }

    def _clear_spectrum(self) -> None:
        self._spectrum = SpectrumSnapshot()
        self._spectrum_history = []
        self._spectrum_events = []

    def _spectrum_peak_hold(self):
        by_frequency = {}
        for snapshot in self._spectrum_history:
            for spectrum_bin in snapshot.bins:
                existing = by_frequency.get(spectrum_bin.center_hz)
                if existing is None or spectrum_bin.power_db > existing.power_db:
                    by_frequency[spectrum_bin.center_hz] = spectrum_bin
        return [by_frequency[frequency] for frequency in sorted(by_frequency)]

    def _spectrum_noise_floor(self) -> float | None:
        powers = [
            spectrum_bin.power_db
            for snapshot in self._spectrum_history[-10:]
            for spectrum_bin in snapshot.bins
        ]
        return float(median(powers)) if powers else None

    def _record_spectrum_events(self, snapshot: SpectrumSnapshot) -> None:
        noise_floor = self._spectrum_noise_floor()
        if noise_floor is None:
            powers = [spectrum_bin.power_db for spectrum_bin in snapshot.bins]
            noise_floor = float(median(powers)) if powers else None
        if noise_floor is None:
            return
        for peak in snapshot.peaks:
            if peak.power_db - noise_floor < self._spectrum_threshold_db:
                continue
            self._spectrum_events.append(
                event := SpectrumEvent(
                    center_hz=peak.center_hz,
                    power_db=peak.power_db,
                    noise_floor_db=noise_floor,
                    threshold_db=self._spectrum_threshold_db,
                    preset_id=self._spectrum_preset_id,
                    captured_at=snapshot.captured_at,
                )
            )
            if self.history:
                self.history.add_spectrum_event(event)
        self._spectrum_events = self._spectrum_events[-self.spectrum_event_limit :]

    def _record_frequency_scan_snapshot(self, snapshot: SpectrumSnapshot) -> None:
        for spectrum_bin in snapshot.bins:
            self._frequency_scan_powers.setdefault(spectrum_bin.center_hz, []).append(
                spectrum_bin.power_db
            )
        self._update_frequency_scan_progress()

    def _update_frequency_scan_progress(self) -> None:
        if not self._frequency_scan or self._frequency_scan_started_monotonic is None:
            return
        elapsed = time.monotonic() - self._frequency_scan_started_monotonic
        request = self._frequency_scan.request
        progress = min(1.0, elapsed / max(0.001, request.duration_sec))
        candidates = self._frequency_scan_candidates(request)
        self._frequency_scan = self._replace_scan(
            self._frequency_scan,
            elapsed_sec=elapsed,
            progress=progress,
            sweeps_completed=self._message_count,
            candidates=candidates,
            error=self.status.error,
        )

    def _complete_frequency_scan(self, error: str | None = None) -> None:
        if not self._frequency_scan:
            return
        if self._process:
            self._process.stop()
            self._process = None
        status = "error" if error else "complete"
        self._frequency_scan = self._replace_scan(
            self._frequency_scan,
            status=status,
            progress=1.0,
            stopped_at=utc_now(),
            error=error,
        )
        if self.session:
            self.session.status = status
        self.status.process_running = False
        if self._frequency_scan_is_baseline:
            self._frequency_scan_baseline = FrequencyBaseline(
                baseline_id=f"baseline-{uuid4()}",
                request=self._frequency_scan.request,
                powers_by_frequency_hz=self._average_scan_powers(),
            )
        elif self.history:
            self.history.add_frequency_scan(self._frequency_scan)
        if (
            not error
            and self._frequency_scan.request.resume_previous
            and self._previous_request
        ):
            self.resume_previous()

    def _frequency_scan_candidates(
        self,
        request: FrequencyScanRequest,
    ) -> list[FrequencyCandidate]:
        baseline_powers = (
            self._frequency_scan_baseline.powers_by_frequency_hz
            if self._frequency_scan_baseline
            else {}
        )
        candidates = []
        for frequency_hz, powers in self._frequency_scan_powers.items():
            power_db = max(powers)
            baseline_power = self._nearest_power(baseline_powers, frequency_hz)
            margin_db = power_db - baseline_power if baseline_power is not None else None
            match = self._catalog_with_history_favorites().match_frequency(
                frequency_hz,
                tolerance_hz=request.channel_width_hz or max(2_500, request.bin_size_hz // 2),
            )
            candidates.append(
                FrequencyCandidate(
                    frequency_hz=frequency_hz,
                    power_db=power_db,
                    baseline_power_db=baseline_power,
                    margin_db=margin_db,
                    sweeps_seen=len(powers),
                    label=match.label,
                    modulation=match.modulation,
                    range_id=match.range_id,
                    channel_id=match.channel_id,
                    matched_frequency_hz=match.channel_frequency_hz,
                    frequency_offset_hz=match.offset_hz,
                    source=(
                        "channel"
                        if frequency_hz in request.channel_frequencies_hz
                        else "bin"
                    ),
                )
            )
        return sorted(
            candidates,
            key=lambda item: (
                item.margin_db if item.margin_db is not None else item.power_db
            ),
            reverse=True,
        )[:20]

    def _average_scan_powers(self) -> dict[int, float]:
        return {
            frequency_hz: sum(powers) / len(powers)
            for frequency_hz, powers in self._frequency_scan_powers.items()
            if powers
        }

    def _nearest_power(
        self,
        powers_by_frequency_hz: dict[int, float],
        frequency_hz: int,
    ) -> float | None:
        if not powers_by_frequency_hz:
            return None
        nearest = min(powers_by_frequency_hz, key=lambda item: abs(item - frequency_hz))
        return powers_by_frequency_hz[nearest]

    def _replace_scan(
        self,
        scan: FrequencyScanStatus,
        **changes,
    ) -> FrequencyScanStatus:
        data = scan.to_dict()
        if "candidates" in changes:
            changes["candidates"] = [
                item.to_dict() if hasattr(item, "to_dict") else item
                for item in changes["candidates"]
            ]
        data.update(changes)
        return FrequencyScanStatus.from_dict(data)

    def _catalog_with_history_favorites(self) -> FrequencyCatalog:
        if not self.history:
            return self.frequency_catalog
        return self.frequency_catalog.with_favorites(self.history.list_frequency_favorites())

    def persisted_spectrum_events(self, limit: int = 200) -> list[dict[str, object]]:
        if not self.history:
            return []
        return self.history.list_spectrum_events(limit=limit)

    def save_frequency_favorite(
        self,
        *,
        frequency_hz: int,
        modulation: str,
        label: str | None = None,
    ) -> dict[str, object]:
        if self.history:
            self.history.upsert_frequency_favorite(
                frequency_hz=frequency_hz,
                modulation=modulation,
                label=label,
            )
        return {
            "frequency_hz": frequency_hz,
            "modulation": modulation,
            "label": label,
        }

    def list_frequency_favorites(self) -> list[dict[str, object]]:
        if not self.history:
            return []
        return self.history.list_frequency_favorites()

    def delete_frequency_favorite(self, frequency_hz: int) -> dict[str, object]:
        if self.history:
            self.history.delete_frequency_favorite(frequency_hz)
        return {"frequency_hz": frequency_hz, "deleted": True}


def _receiver_gain_db(receiver_gain: str | float, request_gain_db: float | None) -> float | None:
    if request_gain_db is not None:
        return request_gain_db
    if isinstance(receiver_gain, int | float):
        return float(receiver_gain)
    return None


def _last_nonempty(lines: list[str]) -> str | None:
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _largest_power_of_two_leq(n: int) -> int:
    """Return the largest power of two that is <= n.

    Used to bound the FFT length for a replayed capture: a real capture stops
    at an arbitrary byte count, and an arbitrary (e.g. large-prime) FFT length
    can fall back to NumPy's much slower Bluestein algorithm.
    """
    if n <= 0:
        raise ValueError("n must be greater than zero")
    return 1 << (n.bit_length() - 1)
