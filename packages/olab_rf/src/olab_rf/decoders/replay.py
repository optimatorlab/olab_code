from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import count
from uuid import uuid4

from olab_rf.decoders.base import DecodedMessage, Decoder
from olab_rf.models import Observation, Track
from olab_rf.models.tracks import utc_now


@dataclass(slots=True)
class ReplayDecoder(Decoder):
    sensor_id: str = "replay"
    session_id: str = "replay-session"
    steps: int = 12
    mode: str = "replay"

    def messages(self) -> Iterator[DecodedMessage]:
        for step in range(self.steps):
            yield self._aircraft(step)
            yield self._vessel(step)

    def _aircraft(self, step: int) -> DecodedMessage:
        now = utc_now()
        track = Track(
            track_id="adsb-N123RF",
            domain="air",
            protocol="replay",
            label="N123RF",
            lat=40.72 + step * 0.01,
            lon=-73.95 + step * 0.012,
            altitude_m=1200 + step * 20,
            speed_mps=95,
            course_deg=62,
            heading_deg=62,
            source_sensor=self.sensor_id,
            first_seen=now,
            last_seen=now,
            metadata={"synthetic": True, "icao": "A1B2C3"},
        )
        return DecodedMessage(
            observation=Observation(
                observation_id=f"obs-{uuid4()}",
                sensor_id=self.sensor_id,
                session_id=self.session_id,
                protocol="replay",
                domain="air",
                timestamp=now,
                track_id=track.track_id,
                metadata={"step": step},
            ),
            track=track,
        )

    def _vessel(self, step: int) -> DecodedMessage:
        now = utc_now()
        track = Track(
            track_id="ais-367000001",
            domain="marine",
            protocol="replay",
            label="UB TEST VESSEL",
            lat=40.64 + step * 0.004,
            lon=-74.05 + step * 0.003,
            speed_mps=4.2,
            course_deg=25,
            heading_deg=27,
            source_sensor=self.sensor_id,
            first_seen=now,
            last_seen=now,
            metadata={"synthetic": True, "mmsi": "367000001"},
        )
        return DecodedMessage(
            observation=Observation(
                observation_id=f"obs-{next(_OBS_COUNTER)}",
                sensor_id=self.sensor_id,
                session_id=self.session_id,
                protocol="replay",
                domain="marine",
                timestamp=now,
                track_id=track.track_id,
                metadata={"step": step},
            ),
            track=track,
        )


_OBS_COUNTER = count(1)
