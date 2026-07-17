from __future__ import annotations

from datetime import timedelta

from olab_rf.models import Track
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
