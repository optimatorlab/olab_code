from __future__ import annotations

import json

import pytest

from olab_rf import FrequencyScanRequest, SessionManager
from olab_rf.decoders.sigmf import sigmf_paths, write_sigmf_meta
from olab_rf.services import session_manager as session_manager_module
from olab_rf.services.session_manager import _largest_power_of_two_leq

from test_recording import FakeRecordingProcess, _iq_request  # noqa: E402


@pytest.fixture()
def fake_recording_process(monkeypatch):
    FakeRecordingProcess.fail_start = False
    FakeRecordingProcess.instances = []
    monkeypatch.setattr(session_manager_module, "DecoderProcess", FakeRecordingProcess)
    yield FakeRecordingProcess


def _write_synthetic_capture(
    base_path,
    *,
    count: int,
    sample_rate_hz: int = 240_000,
    center_frequency_hz: int = 462_700_000,
    tone_offset_hz: int = 12_500,
):
    np = pytest.importorskip("numpy")
    time = np.arange(count) / sample_rate_hz
    tone = np.exp(2j * np.pi * tone_offset_hz * time)
    i = np.clip(np.round(tone.real * 100 + 127.5), 0, 255).astype(np.uint8)
    q = np.clip(np.round(tone.imag * 100 + 127.5), 0, 255).astype(np.uint8)
    interleaved = np.empty(2 * count, dtype=np.uint8)
    interleaved[0::2] = i
    interleaved[1::2] = q

    meta_path, data_path = sigmf_paths(str(base_path))
    data_path.write_bytes(interleaved.tobytes())
    write_sigmf_meta(
        meta_path,
        sample_rate_hz=sample_rate_hz,
        frequency_hz=center_frequency_hz,
        datetime_iso="2026-09-01T00:00:00+00:00",
    )
    return tone


def test_largest_power_of_two_leq():
    assert _largest_power_of_two_leq(1) == 1
    assert _largest_power_of_two_leq(2) == 2
    assert _largest_power_of_two_leq(3) == 2
    assert _largest_power_of_two_leq(4096) == 4096
    assert _largest_power_of_two_leq(4097) == 4096

    with pytest.raises(ValueError):
        _largest_power_of_two_leq(0)


def test_start_iq_replay_scan_matches_live_iq_capture(tmp_path, monkeypatch):
    sample_rate_hz = 240_000
    center_frequency_hz = 462_700_000
    tone_offset_hz = 12_500
    tone = _write_synthetic_capture(
        tmp_path / "capture",
        count=4096,
        sample_rate_hz=sample_rate_hz,
        center_frequency_hz=center_frequency_hz,
        tone_offset_hz=tone_offset_hz,
    )

    monkeypatch.setattr(
        session_manager_module,
        "capture_iq_samples_with_rtl_sdr",
        lambda **kwargs: tone,
    )
    live_manager = SessionManager()
    live_scan = live_manager.start_range_scan(
        backend="rtl_sdr_iq",
        channel_frequencies_hz=[center_frequency_hz],
        sample_rate_hz=sample_rate_hz,
        duration_sec=4096 / sample_rate_hz,
    )
    live_candidate = live_scan.best_candidate

    replay_manager = SessionManager()
    replay_scan = replay_manager.start_iq_replay_scan(
        replay_path=str(tmp_path / "capture"),
        channel_width_hz=12_500,
    )
    replay_candidate = replay_scan.best_candidate

    assert replay_scan.status == "complete"
    assert replay_scan.sweeps_completed == 1
    assert replay_candidate.frequency_hz == pytest.approx(live_candidate.frequency_hz, abs=1)
    assert replay_manager.session is None
    assert replay_manager.status.mode != "frequency_scan"


def test_start_iq_replay_scan_handles_an_awkward_prime_sample_count(tmp_path, monkeypatch):
    prime_count = 4093  # prime, would force NumPy's slow Bluestein FFT path unbounded
    _write_synthetic_capture(tmp_path / "capture", count=prime_count)

    fft_sizes = []
    real_estimate_iq_peak = session_manager_module.estimate_iq_peak

    def spy_estimate_iq_peak(*args, **kwargs):
        fft_sizes.append(kwargs.get("fft_size"))
        return real_estimate_iq_peak(*args, **kwargs)

    monkeypatch.setattr(session_manager_module, "estimate_iq_peak", spy_estimate_iq_peak)

    manager = SessionManager()
    scan = manager.start_iq_replay_scan(replay_path=str(tmp_path / "capture"))

    assert scan.status == "complete"
    assert scan.sweeps_completed == 1
    assert scan.best_candidate is not None
    # the awkward prime count must never reach estimate_iq_peak as fft_size --
    # pinning that the power-of-two guard actually reaches the call, not just
    # that _largest_power_of_two_leq works in isolation.
    assert len(fft_sizes) == 1
    assert fft_sizes[0] is not None
    assert fft_sizes[0] & (fft_sizes[0] - 1) == 0  # power of two
    assert fft_sizes[0] <= prime_count


def test_start_iq_replay_scan_bounds_the_default_read(tmp_path, monkeypatch):
    sample_rate_hz = 8_000
    total_seconds = 20
    _write_synthetic_capture(
        tmp_path / "capture",
        count=sample_rate_hz * total_seconds,
        sample_rate_hz=sample_rate_hz,
    )

    max_samples_seen = []
    real_read_sigmf_iq = session_manager_module.read_sigmf_iq

    def spy_read_sigmf_iq(*args, **kwargs):
        max_samples_seen.append(kwargs.get("max_samples"))
        return real_read_sigmf_iq(*args, **kwargs)

    monkeypatch.setattr(session_manager_module, "read_sigmf_iq", spy_read_sigmf_iq)

    manager = SessionManager()
    scan = manager.start_iq_replay_scan(replay_path=str(tmp_path / "capture"))

    assert scan.status == "complete"
    # one 1-sample probe read (to learn the file's own sample rate) plus one
    # bounded real read at the default window -- not the full 20 s (160,000
    # samples) the file actually contains.
    assert max_samples_seen == [1, int(sample_rate_hz * 5.0)]


def test_start_iq_replay_scan_raises_while_another_scan_is_running(tmp_path):
    _write_synthetic_capture(tmp_path / "capture", count=64)
    manager = SessionManager()
    manager._frequency_scan = manager._replace_scan(
        manager.start_iq_replay_scan(replay_path=str(tmp_path / "capture")),
        status="running",
    )

    with pytest.raises(RuntimeError, match="already running"):
        manager.start_iq_replay_scan(replay_path=str(tmp_path / "capture"))


def test_start_iq_replay_scan_succeeds_while_a_recording_is_live(
    fake_recording_process, tmp_path
):
    _write_synthetic_capture(tmp_path / "corpus", count=64)
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path / "live"))

    scan = manager.start_iq_replay_scan(replay_path=str(tmp_path / "corpus"))

    assert scan.status == "complete"
    assert manager.current_recording().status == "running"


@pytest.mark.parametrize(
    "call",
    [
        lambda manager: manager.start_range_scan(backend="iq_replay"),
        lambda manager: manager.start_frequency_scan(
            min_freq_hz=1, max_freq_hz=2, bin_size_hz=1, duration_sec=1, backend="iq_replay"
        ),
        lambda manager: manager.find_active_channels(range_id="frs_gmrs", backend="iq_replay"),
        lambda manager: manager.capture_frequency_baseline(
            min_freq_hz=1, max_freq_hz=2, bin_size_hz=1, duration_sec=1, backend="iq_replay"
        ),
        lambda manager: manager.capture_range_baseline(backend="iq_replay"),
    ],
)
def test_scan_entry_points_reject_iq_replay_backend(call):
    manager = SessionManager()

    with pytest.raises(ValueError, match="iq_replay"):
        call(manager)


def test_start_iq_replay_scan_reports_missing_file_as_scan_error(tmp_path):
    manager = SessionManager()

    with pytest.raises(RuntimeError, match="does-not-exist"):
        manager.start_iq_replay_scan(replay_path=str(tmp_path / "does-not-exist"))

    scan = manager.current_frequency_scan()
    assert scan.status == "error"
    assert scan.error


def test_start_iq_replay_scan_reports_empty_capture_as_scan_error(tmp_path):
    meta_path, data_path = sigmf_paths(str(tmp_path / "capture"))
    data_path.write_bytes(b"")
    write_sigmf_meta(
        meta_path,
        sample_rate_hz=8_000,
        frequency_hz=462_712_500,
        datetime_iso="2026-09-01T00:00:00+00:00",
    )
    manager = SessionManager()

    with pytest.raises(RuntimeError, match="no samples"):
        manager.start_iq_replay_scan(replay_path=str(tmp_path / "capture"))

    scan = manager.current_frequency_scan()
    assert scan.status == "error"
    assert "no samples" in scan.error


def test_start_iq_replay_scan_reports_a_file_missing_core_frequency_as_scan_error(tmp_path):
    """core:frequency is optional under SigMF v1.0.0 itself, so a spec-legal

    file can omit it. This must not leave the scan slot stuck at "created"
    (the phantom-in-progress-scan bug a bare escaping KeyError caused) --
    it must land in "error" like every other replay failure.
    """
    meta_path, data_path = sigmf_paths(str(tmp_path / "capture"))
    data_path.write_bytes(b"\x00\x01" * 8)
    meta_path.write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cu8",
                    "core:version": "1.0.0",
                    "core:sample_rate": 8_000,
                    "core:num_channels": 1,
                },
                "captures": [{"core:sample_start": 0}],  # core:frequency omitted
                "annotations": [],
            }
        ),
        encoding="utf-8",
    )
    manager = SessionManager()

    with pytest.raises(RuntimeError, match="core:frequency"):
        manager.start_iq_replay_scan(replay_path=str(tmp_path / "capture"))

    scan = manager.current_frequency_scan()
    assert scan.status == "error"
    assert scan.error


def test_frequency_scan_request_rejects_fields_that_do_not_apply_to_iq_replay():
    with pytest.raises(ValueError, match="channel_frequencies_hz"):
        FrequencyScanRequest(
            backend="iq_replay", replay_path="capture", channel_frequencies_hz=[462_700_000]
        )

    with pytest.raises(ValueError, match="sample_rate_hz"):
        FrequencyScanRequest(backend="iq_replay", replay_path="capture", sample_rate_hz=240_000)

    with pytest.raises(ValueError, match="gain_db"):
        FrequencyScanRequest(backend="iq_replay", replay_path="capture", gain_db=20.0)

    with pytest.raises(ValueError, match="0"):
        FrequencyScanRequest(backend="iq_replay", replay_path="capture", duration_sec=5.0)


def test_frequency_scan_request_rejects_invalid_replay_max_samples():
    with pytest.raises(ValueError, match="replay_max_samples"):
        FrequencyScanRequest(backend="iq_replay", replay_path="capture", replay_max_samples=0)

    with pytest.raises(ValueError, match="replay_max_samples"):
        FrequencyScanRequest(backend="iq_replay", replay_path="capture", replay_max_samples=-5)


def test_frequency_scan_request_rejects_replay_max_samples_for_other_backends():
    with pytest.raises(ValueError, match="replay_max_samples"):
        FrequencyScanRequest(
            min_freq_hz=462_000_000,
            max_freq_hz=468_000_000,
            bin_size_hz=12_500,
            duration_sec=5,
            backend="rtl_power",
            replay_max_samples=1_000,
        )


def test_frequency_scan_request_replay_path_is_required_and_exclusive():
    with pytest.raises(ValueError, match="replay_path"):
        FrequencyScanRequest(backend="iq_replay")

    with pytest.raises(ValueError, match="replay_path"):
        FrequencyScanRequest(
            min_freq_hz=462_000_000,
            max_freq_hz=468_000_000,
            bin_size_hz=12_500,
            duration_sec=5,
            backend="rtl_power",
            replay_path="capture",
        )


def test_frequency_scan_request_replay_fields_round_trip():
    request = FrequencyScanRequest(
        backend="iq_replay",
        replay_path="data/capture",
        replay_max_samples=1_000_000,
        channel_width_hz=5_000,
    )

    restored = FrequencyScanRequest.from_dict(request.to_dict())

    assert restored == request


def test_non_iq_replay_backends_still_validate_scan_shape_fields():
    with pytest.raises(ValueError, match="duration_sec"):
        FrequencyScanRequest(
            min_freq_hz=462_000_000,
            max_freq_hz=468_000_000,
            bin_size_hz=12_500,
            duration_sec=0,
        )
