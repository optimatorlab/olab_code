from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
import os
import shlex
from statistics import median
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread, current_thread
import time
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
from olab_rf.history import SqliteHistory
from olab_rf.models import ReceiverConfig, RecordingRequest, RecordingStatus, SensorStatus
from olab_rf.models.digital import DigitalListenStatus
from olab_rf.decoders.sdrtrunk import SdrTrunkBackend
from olab_rf.models.voice import RadioVoiceSegment, VoiceCaptureEvent, VoiceSegmentStatus
from olab_rf.models.scanning import (
    FrequencyScanBackend,
    FrequencyBaseline,
    FrequencyCandidate,
    FrequencyScanRequest,
    FrequencyScanStatus,
)
from olab_rf.models.sessions import RadioSession
from olab_rf.models.spectrum import (
    FrequencyRange,
    SpectrumEvent,
    SpectrumSnapshot,
)
from olab_rf.receivers.rtlsdr_iq import capture_iq_samples_with_rtl_sdr
from olab_rf.services.iq_candidates import candidate_from_iq_peak
from olab_rf.services.range_scanner import build_frequency_range_scan_plan
from olab_rf.services.track_store import TrackStore
from olab_rf.services.frequency_catalog import FrequencyCatalog
from olab_rf.services.voice_segments import PcmAudioBackend, RadioVoiceSegmenter, RtlFmAudioBackend
from olab_rf.models.tracks import utc_now


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
    _voice_backend: PcmAudioBackend | None = None
    _voice_segmenter: RadioVoiceSegmenter | None = None
    _voice_segments: list[RadioVoiceSegment] = field(default_factory=list)
    _voice_poll_stop: Event | None = None
    _voice_poll_thread: Thread | None = None
    _voice_events: list[VoiceCaptureEvent] = field(default_factory=list)
    _voice_event_callback: Callable[[VoiceCaptureEvent], None] | None = None
    _voice_segment_callback: Callable[[RadioVoiceSegment], None] | None = None
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

    def _start_frequency_scan(
        self,
        *,
        request: FrequencyScanRequest,
        path: str,
        baseline: FrequencyBaseline | None,
        is_baseline: bool,
    ) -> FrequencyScanStatus:
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
        rtl_fm_squelch_db: int | None = None,
        frame_ms: int = 40,
        threshold_db: float = 10.0,
        min_active_ms: int = 120,
        hang_time_ms: int = 600,
        min_segment_ms: int = 400,
        max_segment_sec: float = 20.0,
        pre_roll_ms: int = 200,
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
            voice_backend: PcmAudioBackend = RtlFmAudioBackend(
                path=path,
                frequency_hz=frequency_hz,
                modulation=modulation,
                sample_rate_hz=sample_rate_hz,
                frame_ms=frame_ms,
                ppm=self.receiver.ppm,
                gain_db=gain_db,
                squelch_db=rtl_fm_squelch_db,
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
        )
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
    ) -> VoiceSegmentStatus:
        """Update carrier-gate settings without interrupting active PCM capture.

        Frequency, modulation, gain, sample rate, and backend changes still
        require a new voice session because they change the SDR process.
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
            )
            return self._voice_segmenter.status(error=self.status.error)

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
        rtl_fm_squelch_db: int | None = None,
        frame_ms: int = 40,
        threshold_db: float = 10.0,
        min_active_ms: int = 120,
        hang_time_ms: int = 600,
        min_segment_ms: int = 400,
        max_segment_sec: float = 20.0,
        pre_roll_ms: int = 200,
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
            rtl_fm_squelch_db=rtl_fm_squelch_db,
            frame_ms=frame_ms,
            threshold_db=threshold_db,
            min_active_ms=min_active_ms,
            hang_time_ms=hang_time_ms,
            min_segment_ms=min_segment_ms,
            max_segment_sec=max_segment_sec,
            pre_roll_ms=pre_roll_ms,
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
            self._poll_digital_listen()
        return self.status

    def poll_frequency_scan(self) -> FrequencyScanStatus | None:
        """Advance only an active frequency scan or baseline capture."""
        with self._poll_lock:
            self.ingest_frequency_scan_stdout()
            return self._frequency_scan

    def stop(self, *, clear_previous: bool = True, clear_error: bool = True) -> None:
        """Stop the active workflow.

        By default this also clears any stored previous request used by
        ``resume_previous``.
        """
        self._stop_voice_auto_poll()
        if self._voice_backend and self.session and self.session.mode == "voice_segments":
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

    def start_recording(self, request: RecordingRequest) -> RecordingStatus:
        """Validate and record the requested recording contract.

        Actual recording is intentionally not implemented yet. The returned
        status is an explicit error so callers can build against the stable
        request/status shape without assuming bytes are being captured.
        """
        if self._recording and self._recording.status == "running":
            raise RuntimeError("recording is already active")
        self._recording = RecordingStatus(
            request=request,
            status="error",
            error="recording is designed but not implemented",
        )
        return self._recording

    def stop_recording(self) -> RecordingStatus | None:
        """Stop the active recording placeholder, if any."""
        if self._recording is None:
            return None
        if self._recording.status == "running":
            self._recording = RecordingStatus(
                request=self._recording.request,
                recording_id=self._recording.recording_id,
                status="stopped",
                started_at=self._recording.started_at,
                stopped_at=utc_now(),
                bytes_written=self._recording.bytes_written,
            )
        return self._recording

    def current_recording(self) -> RecordingStatus | None:
        """Return the active or most recent recording placeholder status."""
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
