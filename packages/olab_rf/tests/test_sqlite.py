from __future__ import annotations

from olab_rf.history import SqliteHistory
from olab_rf.models import Observation, Track
from olab_rf.models.scanning import FrequencyCandidate, FrequencyScanRequest, FrequencyScanStatus
from olab_rf.models.spectrum import SpectrumEvent


def test_sqlite_history_stores_track_trail_and_observation(tmp_path):
    history = SqliteHistory(tmp_path / "olab_rf.sqlite")
    track = Track(
        track_id="ais-1",
        domain="marine",
        protocol="ais",
        lat=40.0,
        lon=-74.0,
        source_sensor="rtlsdr-1",
    )
    observation = Observation(
        observation_id="obs-1",
        sensor_id="rtlsdr-1",
        session_id="session-1",
        protocol="ais",
        domain="marine",
        track_id=track.track_id,
    )

    history.upsert_track(track)
    history.add_observation(observation)

    assert history.list_tracks()[0].track_id == "ais-1"
    assert history.trail_for("ais-1")[0]["lat"] == 40.0
    history.close()


def test_sqlite_history_stores_spectrum_events_and_favorites(tmp_path):
    history = SqliteHistory(tmp_path / "olab_rf.sqlite")
    event = SpectrumEvent(
        center_hz=462_612_500,
        power_db=-38.0,
        noise_floor_db=-58.0,
        threshold_db=12.0,
        preset_id="frs_gmrs",
    )

    history.add_spectrum_event(event)
    history.upsert_frequency_favorite(
        frequency_hz=462_612_500,
        modulation="NFM",
        label="FRS test",
    )

    assert history.list_spectrum_events()[0]["center_hz"] == 462_612_500
    favorite = history.list_frequency_favorites()[0]
    assert favorite["frequency_hz"] == 462_612_500
    assert favorite["label"] == "FRS test"
    history.delete_frequency_favorite(462_612_500)
    assert history.list_frequency_favorites() == []
    history.close()


def test_sqlite_history_stores_frequency_scans(tmp_path):
    history = SqliteHistory(tmp_path / "olab_rf.sqlite")
    scan = FrequencyScanStatus(
        scan_id="scan-1",
        request=FrequencyScanRequest(
            min_freq_hz=462_000_000,
            max_freq_hz=468_000_000,
            bin_size_hz=12_500,
            duration_sec=5,
        ),
        status="complete",
        candidates=[
            FrequencyCandidate(
                frequency_hz=462_611_164,
                power_db=-38.0,
                label="FRS/GMRS Ch 3",
                matched_frequency_hz=462_612_500,
                frequency_offset_hz=-1_336,
                source="iq_peak",
            )
        ],
    )

    history.add_frequency_scan(scan)

    rows = history.list_frequency_scans()
    assert rows[0]["scan_id"] == "scan-1"
    best = rows[0]["best_candidate"]
    assert best["frequency_hz"] == 462_611_164
    assert best["observed_frequency_hz"] == 462_611_164
    assert best["matched_frequency_hz"] == 462_612_500
    assert best["frequency_offset_hz"] == -1_336
    assert best["source"] == "iq_peak"
    scan_payload = history.get_frequency_scan("scan-1")
    assert scan_payload["status"] == "complete"
    assert scan_payload["best_candidate"]["source"] == "iq_peak"
    history.close()
