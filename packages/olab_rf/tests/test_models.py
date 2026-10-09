from __future__ import annotations

from datetime import timedelta

from olab_rf.models import PriorityScanStatus, Track
from olab_rf.models.tracks import utc_now


def test_track_round_trip_and_state():
    now = utc_now()
    track = Track(
        track_id="adsb-a1",
        domain="air",
        protocol="adsb",
        lat=40.0,
        lon=-74.0,
        source_sensor="rtlsdr-1",
        first_seen=now,
        last_seen=now - timedelta(seconds=90),
        stale_after_s=30,
        expire_after_s=300,
    )

    restored = Track.from_dict(track.to_dict())

    assert restored.track_id == "adsb-a1"
    assert restored.state(now) == "stale"


def test_priority_scan_status_to_dict_shape():
    status = PriorityScanStatus(
        scan_id="priority-scan-1",
        session_id="session-1",
        channel_ids=["erie_fire_vhf.buffalo_fd_f1", "noaa_wx_1"],
        state="locked",
        current_channel_id="buffalo_fd_f1",
        current_channel_label="Buffalo FD F1",
        current_range_id="erie_fire_vhf",
        current_range_label="Erie County Fire VHF",
        cycle_count=2,
        completed_segments=3,
        dropped_segments=1,
        capped_closes=0,
        noise_floor_db=-40.0,
        last_frame_band_ratio=0.4,
        active=True,
        recalibrating=False,
    )

    payload = status.to_dict()

    assert payload["state"] == "locked"
    assert payload["channel_ids"] == ["erie_fire_vhf.buffalo_fd_f1", "noaa_wx_1"]
    assert payload["current_channel_id"] == "buffalo_fd_f1"
    assert payload["completed_segments"] == 3
    assert payload["active"] is True
    assert payload["stopped_at"] is None
