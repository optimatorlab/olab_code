from __future__ import annotations

import asyncio

import pytest

from olab_rf.models import Track, VoiceCaptureEvent, VoiceSegmentStatus
from olab_rf.transports.codec import unpack_payload
from olab_rf.transports.nats import NatsRfTransport
from olab_rf.transports.payloads import track_from_payload, track_to_payload
from olab_rf.transports.subjects import RF_STATUS, RF_TRACK_UPDATED, RF_VOICE_EVENT, RF_VOICE_STATUS


def test_subject_constants_are_flat():
    assert RF_STATUS == "rf.status"
    assert RF_TRACK_UPDATED == "rf.track.updated"
    assert RF_VOICE_EVENT == "rf.voice.event"
    assert RF_VOICE_STATUS == "rf.voice.status"


def test_track_payload_round_trip():
    track = Track(
        track_id="adsb-a1",
        domain="air",
        protocol="adsb",
        lat=40.0,
        lon=-74.0,
        source_sensor="rtlsdr-1",
    )

    restored = track_from_payload(track_to_payload(track))

    assert restored.track_id == track.track_id
    assert restored.lat == 40.0


def test_nats_transport_publishes_voice_status_and_events():
    pytest.importorskip("msgpack", reason="msgpack is not installed; install olab-rf[nats]")

    class Client:
        def __init__(self) -> None:
            self.published = []

        async def publish(self, subject: str, payload: bytes) -> None:
            self.published.append((subject, payload))

        async def subscribe(self, subject: str, cb):  # type: ignore[no-untyped-def]
            return None

    client = Client()
    transport = NatsRfTransport(client)
    status = VoiceSegmentStatus(
        session_id="session-voice",
        active=False,
        capture_running=True,
        state="idle",
        sample_rate_hz=16_000,
        last_frame_rms_db=-30.0,
    )
    event = VoiceCaptureEvent(
        event="capture_started",
        session_id="session-voice",
        state="calibrating",
    )

    asyncio.run(transport.publish_voice_status(status))
    asyncio.run(transport.publish_voice_event(event))

    assert client.published[0][0] == RF_VOICE_STATUS
    assert unpack_payload(client.published[0][1])["capture_running"] is True
    assert client.published[1][0] == RF_VOICE_EVENT
    assert unpack_payload(client.published[1][1])["event"] == "capture_started"
