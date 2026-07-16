from __future__ import annotations

import os
import textwrap
import time

import pytest

from olab_rf.decoders.process import DecoderProcess
from olab_rf.decoders.rtl_power import parse_rtl_power_line, rtl_power_command
from olab_rf.models import ReceiverConfig
from olab_rf.models.spectrum import FrequencyRange
from olab_rf.services.frequency_catalog import FrequencyCatalog
from olab_rf.services.session_manager import SessionManager


def test_default_catalog_includes_scanner_targets():
    range_ids = {frequency_range.id for frequency_range in FrequencyCatalog.default().ranges}

    assert {
        "noaa_weather",
        "aviation_am",
        "frs_gmrs",
        "murs",
        "ham_2m_70cm",
        "fm_broadcast",
    } <= range_ids


def test_default_catalog_includes_channel_annotations():
    catalog = FrequencyCatalog.default()
    channels = {
        channel.label: channel
        for frequency_range in catalog.ranges
        for channel in frequency_range.channels
    }

    assert channels["NOAA WX2"].frequency_hz == 162_400_000
    assert channels["FRS/GMRS Ch 3"].frequency_hz == 462_612_500
    assert channels["MURS Ch 5"].frequency_hz == 154_600_000
    assert channels["Aviation emergency"].modulation == "AM"


def test_frequency_catalog_lookup_prefers_channel_over_range():
    catalog = FrequencyCatalog.default()

    assert catalog.match_frequency(462_612_500).label == "FRS/GMRS Ch 3"
    assert catalog.match_frequency(121_500_000).label == "Aviation emergency"
    assert catalog.match_frequency(145_000_000).label == "Ham 2m/70cm"
    assert catalog.match_frequency(75_000_000).label == ""


def test_rtl_power_command_includes_frequency_and_receiver_fields():
    command = rtl_power_command(
        path="/usr/bin/rtl_power",
        ranges=[FrequencyRange(start_hz=162_400_000, stop_hz=162_550_000, bin_hz=25_000)],
        ppm=1,
        gain_db=19.7,
        sample_rate_hz=2_400_000,
        interval_s=3,
    )

    assert command[:2] == ["/usr/bin/rtl_power", "-d"]
    assert "162400000:162550000:25000" in command
    assert ["-i", "3s"] == command[command.index("-i") : command.index("-i") + 2]
    assert ["-p", "1"] == command[command.index("-p") : command.index("-p") + 2]
    assert ["-g", "19.7"] == command[command.index("-g") : command.index("-g") + 2]
    assert ["-s", "2400000"] == command[command.index("-s") : command.index("-s") + 2]


def test_start_spectrum_uses_frequency_catalog_range(monkeypatch):
    monkeypatch.setattr(DecoderProcess, "start", lambda self: None)
    monkeypatch.setattr(DecoderProcess, "is_running", lambda self: True)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))

    session = manager.start_spectrum(path="rtl_power", preset_id="frs_gmrs")

    assert session.command
    assert "462000000:468000000:12500" in session.command
    assert manager.watch_dict()["modulation"] == "NFM"
    manager.stop()


def test_parse_rtl_power_line_builds_bins_and_peaks():
    snapshot = parse_rtl_power_line(
        "2026-07-05, 12:00:00, 162400000, 162500000, 25000, 10, -52.0, -39.5, -47.2, -41.0"
    )

    assert snapshot is not None
    assert len(snapshot.bins) == 4
    assert snapshot.bins[0].center_hz == 162_412_500
    assert snapshot.peaks[0].center_hz == 162_437_500
    assert snapshot.peaks[0].power_db == -39.5


def test_ingest_spectrum_stdout_updates_latest_snapshot(tmp_path):
    script = tmp_path / "fake-rtl-power"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '2026-07-05, 12:00:00, 162400000, 162500000, 25000, 10, -52.0, -39.5, -47.2, -41.0'
            sleep 30
            """
        ),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))
    session = manager.start_spectrum(path=str(script), preset_id="noaa_weather")

    count = 0
    for _ in range(10):
        count = manager.ingest_spectrum_stdout()
        if count:
            break
        time.sleep(0.05)

    assert session.mode == "spectrum"
    assert count == 1
    assert manager.status.message_count == 1
    assert manager.spectrum_dict()["peaks"][0]["center_hz"] == 162_437_500
    manager.stop()


def test_spectrum_dict_includes_history_peak_hold_and_noise_floor(tmp_path):
    script = tmp_path / "fake-rtl-power-history"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '2026-07-05, 12:00:00, 162400000, 162500000, 25000, 10, -52.0, -39.5, -47.2, -41.0'
            sleep 0.1
            echo '2026-07-05, 12:00:02, 162400000, 162500000, 25000, 10, -51.0, -45.5, -44.2, -38.0'
            sleep 30
            """
        ),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))
    manager.start_spectrum(path=str(script), preset_id="noaa_weather")

    count = 0
    for _ in range(10):
        count += manager.ingest_spectrum_stdout()
        if count >= 2:
            break
        time.sleep(0.05)

    payload = manager.spectrum_dict()

    assert len(payload["history"]) == 2
    assert payload["peak_hold"][1]["power_db"] == -39.5
    assert payload["peak_hold"][3]["power_db"] == -38.0
    assert payload["noise_floor_db"] == -44.85
    assert payload["event_threshold_db"] == 12.0
    manager.stop()


def test_spectrum_events_log_peaks_above_noise_threshold(tmp_path):
    script = tmp_path / "fake-rtl-power-events"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '2026-07-05, 12:00:00, 162400000, 162500000, 25000, 10, -60.0, -59.0, -58.0, -57.0'
            sleep 0.1
            echo '2026-07-05, 12:00:02, 162400000, 162500000, 25000, 10, -61.0, -38.0, -58.5, -56.5'
            sleep 30
            """
        ),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))
    manager.start_spectrum(path=str(script), preset_id="noaa_weather", threshold_db=12.0)

    count = 0
    for _ in range(10):
        count += manager.ingest_spectrum_stdout()
        if count >= 2:
            break
        time.sleep(0.05)

    events = manager.spectrum_dict()["events"]

    assert len(events) == 1
    assert events[0]["center_hz"] == 162_437_500
    assert events[0]["power_db"] == -38.0
    assert events[0]["margin_db"] > 12.0
    assert events[0]["preset_id"] == "noaa_weather"
    manager.stop()


def test_spectrum_events_compare_peaks_to_prior_noise_floor(tmp_path):
    script = tmp_path / "fake-rtl-power-prior-noise"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '2026-07-05, 12:00:00, 462650000, 462750000, 25000, 10, -60.0, -59.0, -58.0, -57.0'
            sleep 0.1
            echo '2026-07-05, 12:00:02, 462650000, 462750000, 25000, 10, -61.0, -30.0, -58.5, -56.5'
            sleep 30
            """
        ),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))
    manager.start_spectrum(path=str(script), preset_id="frs_gmrs", threshold_db=20.0)

    count = 0
    for _ in range(10):
        count += manager.ingest_spectrum_stdout()
        if count >= 2:
            break
        time.sleep(0.05)

    events = manager.spectrum_dict()["events"]

    assert len(events) == 1
    assert events[0]["center_hz"] == 462_687_500
    assert events[0]["noise_floor_db"] == -58.5
    assert events[0]["margin_db"] == 28.5
    manager.stop()


def test_watch_frequency_includes_rtl_fm_command_preview():
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001", ppm=1))

    watch = manager.set_watch_frequency(121_500_000, modulation="AM")

    assert watch["frequency_hz"] == 121_500_000
    assert watch["modulation"] == "AM"
    assert watch["command"][:6] == ["rtl_fm", "-d", "0", "-f", "121500000", "-M"]
    assert "am" in watch["command"]
    assert watch["play_command"].endswith("aplay -r 12000 -f S16_LE -t raw -c 1")


def test_start_listen_uses_watch_frequency_without_hardware(monkeypatch):
    monkeypatch.setattr(DecoderProcess, "start", lambda self: None)
    monkeypatch.setattr(DecoderProcess, "is_running", lambda self: True)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))
    manager.set_watch_frequency(462_612_500, modulation="NFM")

    session = manager.start_listen(demod_path="fake-rtl_fm")

    assert session.mode == "listen"
    assert session.decoder == "rtl_fm"
    assert session.command
    assert "fake-rtl_fm" in session.command[0]
    assert "aplay -r 24000" in session.command[0]


def test_start_listen_requires_watch_frequency():
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))

    with pytest.raises(RuntimeError, match="watch frequency"):
        manager.start_listen()


def test_ingest_spectrum_stdout_reports_process_error(tmp_path):
    script = tmp_path / "fake-rtl-power-fail"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo 'usb_open error -4' >&2
            echo 'Failed to open rtlsdr device #0.' >&2
            exit 1
            """
        ),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))
    manager.start_spectrum(path=str(script), preset_id="noaa_weather")

    for _ in range(10):
        manager.ingest_spectrum_stdout()
        if manager.status.error:
            break
        time.sleep(0.05)

    assert manager.status.process_running is False
    assert manager.status.error == "Failed to open rtlsdr device #0."
    assert manager.spectrum_dict()["error"] == "Failed to open rtlsdr device #0."
    manager.stop()
    assert manager.status.mode == "idle"
    assert manager.status.error is None


def test_frequency_scan_discovers_ranked_candidates_without_hardware(tmp_path):
    script = tmp_path / "fake-rtl-power-scan"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '2026-07-05, 12:00:00, 462600000, 462650000, 12500, 10, -61.0, -35.0, -58.5, -56.5'
            sleep 30
            """
        ),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))

    status = manager.start_frequency_scan(
        path=str(script),
        min_freq_hz=462_600_000,
        max_freq_hz=462_650_000,
        bin_size_hz=12_500,
        duration_sec=0.1,
        channel_frequencies_hz=[462_612_500],
    )

    assert status.status == "running"
    for _ in range(10):
        manager.poll()
        status_payload = manager.frequency_scan_dict()
        if status_payload and status_payload["status"] == "complete":
            break
        time.sleep(0.05)

    payload = manager.frequency_scan_dict()

    assert payload["status"] == "complete"
    assert payload["sweeps_completed"] == 1
    assert payload["best_candidate"]["frequency_hz"] == 462_618_750
    assert payload["best_candidate"]["observed_frequency_hz"] == 462_618_750
    assert payload["best_candidate"]["matched_frequency_hz"] == 462_612_500
    assert payload["best_candidate"]["frequency_offset_hz"] == 6_250
    assert payload["best_candidate"]["label"] == "FRS/GMRS Ch 3"


def test_iq_frequency_scan_builds_matched_candidate_without_hardware(monkeypatch):
    from olab_rf.decoders.rtl_sdr_iq import IqPeakEstimate

    captures = []

    def fake_capture_iq_samples_with_rtl_sdr(**kwargs):
        captures.append(kwargs)
        return [1 + 0j] * kwargs["sample_count"]

    def fake_estimate_iq_peak(samples, *, center_frequency_hz, sample_rate_hz, max_offset_hz):
        assert samples
        assert center_frequency_hz == 462_712_500
        assert sample_rate_hz == 240_000
        assert max_offset_hz == 12_500
        return IqPeakEstimate(
            frequency_hz=462_714_800,
            offset_hz=2_300.0,
            power_db=-34.0,
            fft_size=len(samples),
        )

    monkeypatch.setattr(
        "olab_rf.services.session_manager.capture_iq_samples_with_rtl_sdr",
        fake_capture_iq_samples_with_rtl_sdr,
    )
    monkeypatch.setattr(
        "olab_rf.services.session_manager.estimate_iq_peak",
        fake_estimate_iq_peak,
    )
    manager = SessionManager(
        receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001", ppm=2, gain=28.0)
    )

    status = manager.start_frequency_scan(
        backend="rtl_sdr_iq",
        min_freq_hz=462_000_000,
        max_freq_hz=468_000_000,
        bin_size_hz=12_500,
        duration_sec=0.01,
        channel_frequencies_hz=[462_712_500],
        channel_width_hz=12_500,
    )

    payload = status.to_dict()
    best = payload["best_candidate"]
    assert payload["status"] == "complete"
    assert payload["sweeps_completed"] == 1
    assert best["frequency_hz"] == 462_714_800
    assert best["matched_frequency_hz"] == 462_712_500
    assert best["frequency_offset_hz"] == 2_300
    assert best["label"] == "FRS/GMRS Ch 7"
    assert best["source"] == "iq_peak"
    assert manager.status.process_running is False
    assert captures == [
        {
            "center_frequency_hz": 462_712_500,
            "path": "rtl_sdr",
            "sample_rate_hz": 240_000,
            "sample_count": 2400,
            "device_index": 0,
            "gain_db": 28.0,
            "ppm": 2,
        }
    ]


def test_iq_frequency_scan_requires_channel_frequencies():
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1"))

    with pytest.raises(RuntimeError, match="channel_frequencies_hz"):
        manager.start_frequency_scan(
            backend="rtl_sdr_iq",
            min_freq_hz=462_000_000,
            max_freq_hz=468_000_000,
            bin_size_hz=12_500,
            duration_sec=0.01,
        )


def test_frequency_scan_reuses_latest_baseline_without_hardware(tmp_path):
    baseline_script = tmp_path / "fake-rtl-power-baseline"
    baseline_script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '2026-07-05, 12:00:00, 462600000, 462625000, 12500, 10, -60.0, -58.0'
            sleep 30
            """
        ),
        encoding="utf-8",
    )
    active_script = tmp_path / "fake-rtl-power-active"
    active_script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '2026-07-05, 12:00:02, 462600000, 462625000, 12500, 10, -60.0, -38.0'
            sleep 30
            """
        ),
        encoding="utf-8",
    )
    os.chmod(baseline_script, 0o755)
    os.chmod(active_script, 0o755)
    manager = SessionManager(receiver=ReceiverConfig(id="rtlsdr-1", serial="00000001"))

    manager.capture_frequency_baseline(
        path=str(baseline_script),
        min_freq_hz=462_600_000,
        max_freq_hz=462_625_000,
        bin_size_hz=12_500,
        duration_sec=0.1,
    )
    for _ in range(10):
        manager.poll()
        if manager.frequency_scan_dict()["status"] == "complete":
            break
        time.sleep(0.05)
    assert manager.latest_frequency_baseline() is not None

    manager.start_frequency_scan(
        path=str(active_script),
        min_freq_hz=462_600_000,
        max_freq_hz=462_625_000,
        bin_size_hz=12_500,
        duration_sec=0.1,
    )
    for _ in range(10):
        manager.poll()
        if manager.frequency_scan_dict()["status"] == "complete":
            break
        time.sleep(0.05)

    best = manager.frequency_scan_dict()["best_candidate"]

    assert best["frequency_hz"] == 462_618_750
    assert best["matched_frequency_hz"] == 462_612_500
    assert best["frequency_offset_hz"] == 6_250
    assert best["baseline_power_db"] == -58.0
    assert best["margin_db"] == 20.0
