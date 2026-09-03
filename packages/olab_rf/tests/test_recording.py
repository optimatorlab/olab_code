from __future__ import annotations

import json

import pytest

from olab_rf import RecordingRequest, RecordingStatus, SessionManager
from olab_rf.decoders.sigmf import sigmf_paths
from olab_rf.services import session_manager as session_manager_module


class FakeRecordingProcess:
    """Stand-in for ``DecoderProcess`` used by recording lifecycle tests.

    Configure class-level ``fail_start``/``next_stderr_lines`` before
    constructing a ``SessionManager`` call that creates one; per-instance
    state (``running``) can be flipped afterward to simulate the capture
    process dying.
    """

    fail_start = False
    instances: list["FakeRecordingProcess"] = []

    def __init__(self, command, **kwargs):
        self.command = command
        self.running = True
        self.stopped = False
        self._stderr_lines: list[str] = []
        FakeRecordingProcess.instances.append(self)

    def start(self) -> None:
        if FakeRecordingProcess.fail_start:
            raise FileNotFoundError(self.command[0])

    def stop(self, timeout_s: float = 3.0) -> None:
        self.stopped = True
        self.running = False

    def is_running(self) -> bool:
        return self.running

    def read_stderr_lines(self, limit: int = 100) -> list[str]:
        lines, self._stderr_lines = self._stderr_lines, []
        return lines

    def queue_stderr(self, line: str) -> None:
        self._stderr_lines.append(line)


@pytest.fixture()
def fake_recording_process(monkeypatch):
    FakeRecordingProcess.fail_start = False
    FakeRecordingProcess.instances = []
    monkeypatch.setattr(session_manager_module, "DecoderProcess", FakeRecordingProcess)
    yield FakeRecordingProcess


def _iq_request(tmp_path, **overrides) -> RecordingRequest:
    kwargs = dict(
        kind="iq",
        path=str(tmp_path / "capture"),
        frequency_hz=462_712_500,
        sample_rate_hz=240_000,
    )
    kwargs.update(overrides)
    return RecordingRequest(**kwargs)


def test_recording_request_round_trip():
    request = RecordingRequest(
        kind="normalized",
        path="data/recordings/session.jsonl",
        format="jsonl",
        include_metadata=True,
        rotate_seconds=60,
        max_bytes=1_000_000,
    )

    restored = RecordingRequest.from_dict(request.to_dict())

    assert restored == request


def test_recording_request_round_trip_for_kind_iq():
    request = RecordingRequest(
        kind="iq",
        path="data/recordings/capture",
        frequency_hz=462_712_500,
        sample_rate_hz=240_000,
        gain_db=28.0,
        device_index=1,
        rotate_seconds=60,
        max_bytes=1_000_000,
    )

    restored = RecordingRequest.from_dict(request.to_dict())

    assert restored == request


def test_recording_request_validation_for_kind_iq():
    with pytest.raises(ValueError, match="frequency_hz"):
        RecordingRequest(kind="iq", path="data/out", sample_rate_hz=240_000)

    with pytest.raises(ValueError, match="sample_rate_hz"):
        RecordingRequest(kind="iq", path="data/out", frequency_hz=462_712_500)

    with pytest.raises(ValueError, match="device_index"):
        RecordingRequest(
            kind="iq",
            path="data/out",
            frequency_hz=462_712_500,
            sample_rate_hz=240_000,
            device_index=-1,
        )


def test_recording_request_validation():
    with pytest.raises(ValueError, match="kind"):
        RecordingRequest(kind="unknown", path="data/out")

    with pytest.raises(ValueError, match="path"):
        RecordingRequest(kind="normalized", path="")

    with pytest.raises(ValueError, match="rotate_seconds"):
        RecordingRequest(kind="normalized", path="data/out", rotate_seconds=0)

    with pytest.raises(ValueError, match="max_bytes"):
        RecordingRequest(kind="normalized", path="data/out", max_bytes=0)


def test_recording_status_round_trip_and_validation():
    status = RecordingStatus(
        request=RecordingRequest(kind="decoder_stdout", path="data/readsb.log"),
        status="error",
        bytes_written=0,
        error="not implemented",
    )

    restored = RecordingStatus.from_dict(status.to_dict())

    assert restored.recording_id == status.recording_id
    assert restored.request == status.request
    assert restored.status == "error"
    assert restored.error == "not implemented"

    with pytest.raises(ValueError, match="recording status"):
        RecordingStatus(
            request=RecordingRequest(kind="normalized", path="data/out"),
            status="unknown",
        )

    with pytest.raises(ValueError, match="bytes_written"):
        RecordingStatus(
            request=RecordingRequest(kind="normalized", path="data/out"),
            bytes_written=-1,
        )


def test_session_manager_recording_placeholder_returns_explicit_error():
    manager = SessionManager()
    request = RecordingRequest(kind="normalized", path="data/recordings/session.jsonl")

    status = manager.start_recording(request)

    assert status.status == "error"
    assert status.request == request
    assert "not implemented" in status.error
    assert manager.current_recording() == status
    assert manager.stop_recording() == status


def test_start_recording_rejects_rotate_seconds_and_max_bytes(fake_recording_process, tmp_path):
    manager = SessionManager()

    with pytest.raises(NotImplementedError, match="rotate_seconds"):
        manager.start_recording(_iq_request(tmp_path, rotate_seconds=60))

    with pytest.raises(NotImplementedError, match="max_bytes"):
        manager.start_recording(_iq_request(tmp_path / "other", max_bytes=1_000))


def test_start_recording_ignores_format_for_kind_iq(fake_recording_process, tmp_path):
    manager = SessionManager()

    status = manager.start_recording(_iq_request(tmp_path, format="anything"))

    assert status.status == "running"


def test_start_recording_writes_initial_sidecar_and_creates_parent_dirs(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    base = tmp_path / "nested" / "capture"

    status = manager.start_recording(_iq_request(tmp_path, path=str(base)))

    meta_path, data_path = sigmf_paths(str(base))
    assert status.status == "running"
    assert meta_path.exists()
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["global"]["core:sample_rate"] == 240_000
    assert payload["captures"][0]["core:frequency"] == 462_712_500
    assert not data_path.exists()  # the fake process never actually writes


def test_start_recording_raises_on_double_start(fake_recording_process, tmp_path):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))

    with pytest.raises(RuntimeError, match="already active"):
        manager.start_recording(_iq_request(tmp_path / "second"))


def test_start_recording_succeeds_after_a_prior_mode_was_cleanly_stopped(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    manager.start_spectrum(path="rtl_power", preset_id="frs_gmrs")
    manager.stop()

    status = manager.start_recording(_iq_request(tmp_path))

    assert status.status == "running"


def test_start_recording_raises_while_another_mode_is_active(fake_recording_process, tmp_path):
    manager = SessionManager()
    manager.start_spectrum(path="rtl_power", preset_id="frs_gmrs")

    with pytest.raises(RuntimeError, match="another mode is active"):
        manager.start_recording(_iq_request(tmp_path))

    assert manager.status.process_running is True  # the scan is untouched


def test_starting_another_mode_while_recording_is_live_raises_and_leaves_recording_running(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))

    with pytest.raises(RuntimeError, match="an IQ recording is active"):
        manager.start_spectrum(path="rtl_power", preset_id="frs_gmrs")

    assert manager.current_recording().status == "running"


def test_second_start_recording_raises_its_own_error_not_other_mode_error(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))

    with pytest.raises(RuntimeError, match="already active") as excinfo:
        manager.start_recording(_iq_request(tmp_path / "second"))

    assert "another mode is active" not in str(excinfo.value)


def test_stop_raises_while_recording_is_live_and_leaves_it_untouched(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))

    with pytest.raises(RuntimeError, match="an IQ recording is active"):
        manager.stop()

    assert manager.current_recording().status == "running"


def test_stop_with_stop_active_recording_finalizes_it(fake_recording_process, tmp_path):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))

    manager.stop(stop_active_recording=True)

    status = manager.current_recording()
    assert status.status == "stopped"
    assert status.stopped_at is not None


def test_stop_recording_when_nothing_started_returns_none():
    manager = SessionManager()

    assert manager.stop_recording() is None


def test_stop_recording_finalizes_sidecar_and_truncates_data(fake_recording_process, tmp_path):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))
    meta_path, data_path = sigmf_paths(str(tmp_path / "capture"))
    data_path.write_bytes(bytes(range(0, 40)) + b"\x00")  # 41 bytes: one dangling byte

    status = manager.stop_recording()

    assert status.status == "stopped"
    assert status.bytes_written == 40
    assert data_path.stat().st_size == 40
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["global"]["core:datatype"] == "cu8"
    # the finalized sidecar carries the true final sample count -- not the
    # empty-annotations shape the initial (pre-finalize) sidecar has, since
    # that count isn't known until finalize.
    assert payload["annotations"] == [{"core:sample_start": 0, "core:sample_count": 20}]


def test_finalized_sidecar_sample_count_is_consistent_with_data_file(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))
    meta_path, data_path = sigmf_paths(str(tmp_path / "capture"))

    initial = json.loads(meta_path.read_text(encoding="utf-8"))
    assert initial["annotations"] == []  # final count isn't known yet

    data_path.write_bytes(bytes(range(0, 64)))
    manager.stop_recording()

    finalized = json.loads(meta_path.read_text(encoding="utf-8"))
    assert finalized["annotations"] == [{"core:sample_start": 0, "core:sample_count": 32}]
    assert finalized["annotations"] != initial["annotations"]  # finalize actually rewrote it


def test_finalizing_with_zero_bytes_written_omits_the_degenerate_annotation(
    fake_recording_process, tmp_path
):
    """A recording stopped before rtl_sdr wrote anything must not produce a

    zero-length "core:sample_count": 0 annotation -- degenerate under the
    spec (an annotation is meant to apply to samples) -- and should instead
    leave the finalized sidecar's annotations empty, the same shape as the
    initial one.
    """
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))
    meta_path, _ = sigmf_paths(str(tmp_path / "capture"))

    manager.stop_recording()  # data file was never written to

    finalized = json.loads(meta_path.read_text(encoding="utf-8"))
    assert finalized["annotations"] == []


def test_recording_re_arms_after_stop(fake_recording_process, tmp_path):
    manager = SessionManager()
    first = manager.start_recording(_iq_request(tmp_path / "one"))
    manager.stop_recording()

    second = manager.start_recording(_iq_request(tmp_path / "two"))

    assert second.status == "running"
    assert second.recording_id != first.recording_id


def test_start_recording_raises_if_target_files_already_exist(fake_recording_process, tmp_path):
    meta_path, data_path = sigmf_paths(str(tmp_path / "capture"))
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_bytes(b"existing corpus data")
    manager = SessionManager()

    with pytest.raises(RuntimeError, match="already exists"):
        manager.start_recording(_iq_request(tmp_path))

    assert data_path.read_bytes() == b"existing corpus data"  # not truncated


def test_start_recording_accepts_a_path_with_sigmf_suffix_already_present(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    base = tmp_path / "capture"
    request = RecordingRequest(
        kind="iq",
        path=str(base) + ".sigmf-meta",
        frequency_hz=462_712_500,
        sample_rate_hz=240_000,
    )

    status = manager.start_recording(request)

    meta_path, _ = sigmf_paths(str(base))
    assert status.status == "running"
    assert meta_path.exists()


def test_ingest_recording_advances_bytes_written(fake_recording_process, tmp_path):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))
    _, data_path = sigmf_paths(str(tmp_path / "capture"))
    data_path.write_bytes(bytes(range(0, 16)))

    manager.poll()

    assert manager.current_recording().bytes_written == 16


def test_poll_immediately_after_start_before_data_file_exists_does_not_raise(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))

    manager.poll()

    assert manager.current_recording().status == "running"
    assert manager.current_recording().bytes_written == 0


def test_process_dying_mid_recording_finalizes_with_error_and_consistent_sidecar(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))
    process = fake_recording_process.instances[-1]
    _, data_path = sigmf_paths(str(tmp_path / "capture"))
    data_path.write_bytes(bytes(range(0, 8)))
    process.queue_stderr("usb_claim_interface error -6")
    process.running = False

    manager.poll()

    status = manager.current_recording()
    assert status.status == "error"
    assert status.error == "usb_claim_interface error -6"
    assert data_path.stat().st_size == 8
    meta_path, _ = sigmf_paths(str(tmp_path / "capture"))
    assert meta_path.exists()


def test_start_recording_reports_missing_binary_and_leaves_nothing_active(
    fake_recording_process, tmp_path
):
    fake_recording_process.fail_start = True
    manager = SessionManager()

    with pytest.raises(RuntimeError, match="not found"):
        manager.start_recording(_iq_request(tmp_path))

    assert manager.current_recording().status == "error"
    fake_recording_process.fail_start = False

    # A missing binary must not brick every other mode.
    manager.stop()
    session = manager.start_spectrum(path="rtl_power", preset_id="frs_gmrs")
    assert session.mode == "spectrum"


def test_finalize_failure_is_contained_in_recording_error_not_raised(
    fake_recording_process, tmp_path, monkeypatch
):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(session_manager_module, "write_sigmf_meta", _boom)

    status = manager.stop_recording()  # must not raise

    assert status.status == "error"
    assert "disk full" in status.error


def test_finalize_failure_during_poll_death_path_does_not_escape(
    fake_recording_process, tmp_path, monkeypatch
):
    """_finalize_recording() failing from inside ingest_recording()/poll()

    (the process-death path) must land in RecordingStatus.error rather than
    raising out of poll() -- poll() is shared with every other mode's
    ingestion (ADS-B/AIS/voice/spectrum), so a recording-side finalize
    failure here must not abort the whole poll() cycle.
    """
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path))
    process = fake_recording_process.instances[-1]
    process.queue_stderr("usb_claim_interface error -6")
    process.running = False

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(session_manager_module, "write_sigmf_meta", _boom)

    manager.poll()  # must not raise, even though _finalize_recording's own
    # sidecar write fails on top of the death it's already reporting

    status = manager.current_recording()
    assert status.status == "error"
    assert status.error  # the death diagnostic wins; finalize's own
    # OSError is swallowed rather than escaping poll() or overwriting it


def test_start_recording_stops_orphaned_process_when_initial_sidecar_write_fails(
    fake_recording_process, tmp_path, monkeypatch
):
    """A failure writing the initial sidecar, after the capture process has

    already started, must not leave a live process that neither stop() nor
    stop_recording() can reach -- see round-9 plan-approval nit 1.
    """

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(session_manager_module, "write_sigmf_meta", _boom)
    manager = SessionManager()

    with pytest.raises(RuntimeError, match="failed to write recording metadata"):
        manager.start_recording(_iq_request(tmp_path))

    process = fake_recording_process.instances[-1]
    assert process.stopped is True  # the orphan window is closed
    assert manager.current_recording().status == "error"

    # Neither stop() nor stop_recording() sees a "running" recording to
    # fight over -- the failure never got that far, so both proceed
    # normally instead of raising or trying to tear down a live process.
    assert manager.stop_recording().status == "error"  # no-op, already terminal
    manager.stop()


def test_sequencing_stop_recording_then_poll_and_reverse_are_consistent(
    fake_recording_process, tmp_path
):
    manager = SessionManager()
    manager.start_recording(_iq_request(tmp_path / "a"))
    manager.stop_recording()
    manager.poll()
    assert manager.current_recording().status == "stopped"

    manager2 = SessionManager()
    manager2.start_recording(_iq_request(tmp_path / "b"))
    manager2.poll()
    manager2.stop_recording()
    assert manager2.current_recording().status == "stopped"
