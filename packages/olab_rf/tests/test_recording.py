from __future__ import annotations

import pytest

from olab_rf import RecordingRequest, RecordingStatus, SessionManager


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
