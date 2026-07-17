from __future__ import annotations

from importlib.resources import files

import pytest

from olab_rf.config import DecoderConfig, OlabRfConfig
from olab_rf.history import SqliteHistory
from olab_rf.models.scanning import FrequencyScanRequest, FrequencyScanStatus
from olab_rf.models.spectrum import SpectrumEvent
from olab_rf.services.session_manager import SessionManager
from olab_rf.web.app import create_app


def test_static_app_uses_track_websocket():
    app_js = files("olab_rf").joinpath("web/static/app.js").read_text(encoding="utf-8")
    index_html = files("olab_rf").joinpath("web/static/index.html").read_text(encoding="utf-8")

    assert "/ws/tracks" in app_js
    assert 'id="session"' in index_html
    assert "No tracks yet." in app_js
    assert "commandWasOpen" in app_js
    assert 'id="session-command"' in app_js
    assert 'value="spectrum"' in index_html
    assert "/api/frequency/catalog" in app_js
    assert "/api/frequency/scan" in app_js
    assert "/api/frequency/baseline" in app_js
    assert "startFrequencyScan" in app_js
    assert "startFrequencyBaseline" in app_js
    assert "renderFrequencyScan" in app_js
    assert "frequencyScanDetails" in app_js
    assert "frequencyScanCandidates" in app_js
    assert "Matched channels" in index_html
    assert 'id="frequency-candidate-view"' in index_html
    assert "SDR Gain dB" in index_html
    assert 'id="frequency-scan-gain"' in index_html
    assert "gain_db: frequencyScanGainEl.value" in app_js
    assert "Monitor Gain dB" in index_html
    assert "frequencyCandidateRow" in app_js
    assert "Frequency (MHz)" in app_js
    assert "Power (dB)" in app_js
    assert "drawBinLabels" in app_js
    assert "selectPeak" in app_js
    assert "drawWaterfall" in app_js
    assert "drawWaterfallAxes" in app_js
    assert "waterfallTooltipEl" in app_js
    assert "scheduleSpectrumRestart" in app_js
    assert "renderSpectrumEvents" in app_js
    assert "loadPersistedSpectrumEvents" in app_js
    assert "mergedSpectrumEvents" in app_js
    assert "Saved event" in app_js
    assert "event-table" in app_js
    assert "data-event-watch" in app_js
    assert "data-event-favorite" in app_js
    assert "formatEventTime" in app_js
    assert "renderSpectrumWatch" in app_js
    assert "applyManualWatchFrequency" in app_js
    assert "/api/spectrum/watch" in app_js
    assert "copyText" in app_js
    assert "Listen" in app_js
    assert "startListen" in app_js
    assert "Starting Listen stops Spectrum" in app_js
    assert "Stop Listen" in app_js
    assert "Save Favorite" in app_js
    assert "favoriteForFrequency" in app_js
    assert "annotationForFrequency" in app_js
    assert "spectrumAnnotations" in app_js
    assert "frequencyHeading" in app_js
    assert "frequency-label" in app_js
    assert "deleteFavorite" in app_js
    assert "Remove" in app_js
    assert "Listened This Page" in app_js
    assert "Playback command" in app_js
    assert "Decoder command" in app_js
    assert "raw audio samples to stdout" in app_js
    assert "pipes its raw audio into aplay" in app_js
    assert "resuming spectrum" not in app_js
    assert "delay(1500)" not in app_js
    assert "noise floor" in app_js
    assert "latestSpectrumMeterText" in app_js
    assert "Event dB" in index_html
    assert "Listen MHz" in index_html
    assert 'id="watch-frequency"' in index_html
    assert 'id="watch-frequency-apply"' in index_html
    assert "/api/spectrum/events/export.csv" in index_html
    assert "/api/spectrum/events/export.json" in index_html
    assert "/api/frequency/scans/export.csv" in index_html
    assert "/api/frequency/scans/export.json" in index_html
    assert 'class="peak" type="button"' in app_js
    assert "Approximate width of each frequency bucket" in index_html
    assert 'role="img"' in index_html
    assert 'id="waterfall-canvas"' in index_html
    assert 'id="waterfall-tooltip"' in index_html
    assert 'id="spectrum-events"' in index_html
    assert 'id="frequency-range"' in index_html
    assert 'id="frequency-scan-status"' in index_html
    assert 'id="spectrum-watch"' in index_html


def test_create_app_health_and_replay_tracks():
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    assert fastapi
    client = TestClient(create_app())

    assert client.get("/health").json() == {"ok": True}
    assert client.get("/api/session").json() is None
    config = client.get("/api/config").json()
    assert config["receivers"][0]["id"] == "rtlsdr-1"
    check = client.get("/api/check").json()
    assert "tools" in check
    catalog = client.get("/api/frequency/catalog").json()
    frs = next(item for item in catalog["ranges"] if item["id"] == "frs_gmrs")
    assert any(item["label"] == "FRS/GMRS Ch 3" for item in frs["channels"])
    assert frs["default_bin_size_hz"] == 12500
    assert client.get("/api/spectrum").json()["bins"] == []
    watch_response = client.post("/api/spectrum/watch", json={"frequency_hz": 462612500, "modulation": "NFM"})
    assert watch_response.status_code == 200
    assert watch_response.json()["command"][0] == "rtl_fm"
    favorite_response = client.post(
        "/api/spectrum/favorites",
        json={"frequency_hz": 462612500, "modulation": "NFM", "label": "FRS test"},
    )
    assert favorite_response.status_code == 200
    assert client.get("/api/spectrum/favorites").json() == []
    response = client.post("/api/session/start", json={"mode": "replay"})
    assert response.status_code == 200
    session = client.get("/api/session").json()
    assert session["mode"] == "replay"
    tracks = client.get("/api/tracks").json()
    assert {track["domain"] for track in tracks} == {"air", "marine"}


def test_api_check_uses_configured_readsb_path(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    readsb = tmp_path / "readsb"
    readsb.write_text("#!/bin/sh\n", encoding="utf-8")
    config = OlabRfConfig.default()
    config.decoders["readsb"] = DecoderConfig(path=str(readsb))
    client = TestClient(create_app(config=config, config_path="olab_rf.yaml"))

    check = client.get("/api/check").json()
    tools = {tool["name"]: tool for tool in check["tools"]}

    assert tools["readsb"]["found"] is True
    assert tools["readsb"]["path"] == str(readsb)
    assert check["context"]["config_path"] == "olab_rf.yaml"


def test_frequency_scan_endpoint_uses_range_scan_api(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    calls = []

    def fake_start_range_scan(self, **kwargs):
        calls.append(kwargs)
        return FrequencyScanStatus.created(
            request=FrequencyScanRequest(
                min_freq_hz=kwargs["min_freq_hz"] or 462_000_000,
                max_freq_hz=kwargs["max_freq_hz"] or 468_000_000,
                bin_size_hz=kwargs["step_hz"],
                duration_sec=kwargs["duration_sec"],
                channel_width_hz=kwargs["channel_width_hz"],
                backend=kwargs["backend"],
                gain_db=kwargs["gain_db"],
                sample_rate_hz=kwargs["sample_rate_hz"],
                resume_previous=kwargs["resume_previous"],
            )
        )

    monkeypatch.setattr(SessionManager, "start_range_scan", fake_start_range_scan)
    rtl_power = tmp_path / "rtl_power"
    config = OlabRfConfig.default()
    config.decoders["rtl_power"] = DecoderConfig(path=str(rtl_power))
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/frequency/scan",
        json={
            "range_id": "frs_gmrs",
            "min_freq_hz": 462_000_000,
            "max_freq_hz": 468_000_000,
            "bin_size_hz": 12_500,
            "duration_sec": 20,
            "channel_width_hz": 12_500,
            "gain_db": "",
            "sample_rate_hz": "",
            "resume_previous": True,
        },
    )

    assert response.status_code == 200
    assert calls[0] == {
        "path": None,
        "backend": "rtl_power",
        "range_id": "frs_gmrs",
        "min_freq_hz": None,
        "max_freq_hz": None,
        "step_hz": 12_500,
        "duration_sec": 20.0,
        "channel_frequencies_hz": [],
        "channel_width_hz": 12_500,
        "gain_db": None,
        "sample_rate_hz": None,
        "resume_previous": True,
    }

    custom_response = client.post(
        "/api/frequency/scan",
        json={
            "range_id": "custom",
            "min_freq_hz": 150_000_000,
            "max_freq_hz": 150_500_000,
            "bin_size_hz": 25_000,
            "duration_sec": 10,
            "channel_width_hz": 25_000,
            "resume_previous": False,
        },
    )

    assert custom_response.status_code == 200
    assert calls[1]["range_id"] == "custom"
    assert calls[1]["min_freq_hz"] == 150_000_000
    assert calls[1]["max_freq_hz"] == 150_500_000


def test_frequency_scan_status_includes_active_command(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    script = tmp_path / "fake-rtl-power"
    script.write_text(
        "#!/bin/sh\n"
        "echo '2026-07-05, 12:00:00, 462600000, 462625000, 12500, 10, -60.0, -58.0'\n"
        "sleep 30\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    config = OlabRfConfig.default()
    config.decoders["rtl_power"] = DecoderConfig(path=str(script))
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/frequency/scan",
        json={
            "range_id": "custom",
            "min_freq_hz": 462_600_000,
            "max_freq_hz": 462_625_000,
            "bin_size_hz": 12_500,
            "duration_sec": 5,
            "gain_db": 9,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["command"][0] == str(script)
    assert "-g" in payload["command"]
    assert payload["command"][payload["command"].index("-g") + 1] == "9"
    assert payload["decoder"] == "rtl_power"


def test_frequency_baseline_endpoint_uses_range_baseline_api(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    calls = []

    def fake_capture_range_baseline(self, **kwargs):
        calls.append(kwargs)
        return FrequencyScanStatus.created(
            request=FrequencyScanRequest(
                min_freq_hz=kwargs["min_freq_hz"] or 462_000_000,
                max_freq_hz=kwargs["max_freq_hz"] or 468_000_000,
                bin_size_hz=kwargs["step_hz"],
                duration_sec=kwargs["duration_sec"],
                channel_width_hz=kwargs["channel_width_hz"],
                backend=kwargs["backend"],
                gain_db=kwargs["gain_db"],
                sample_rate_hz=kwargs["sample_rate_hz"],
            )
        )

    monkeypatch.setattr(SessionManager, "capture_range_baseline", fake_capture_range_baseline)
    rtl_power = tmp_path / "rtl_power"
    config = OlabRfConfig.default()
    config.decoders["rtl_power"] = DecoderConfig(path=str(rtl_power))
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/frequency/baseline",
        json={
            "range_id": "frs_gmrs",
            "min_freq_hz": 462_000_000,
            "max_freq_hz": 468_000_000,
            "bin_size_hz": 12_500,
            "duration_sec": 10,
            "channel_width_hz": 12_500,
            "gain_db": "",
            "sample_rate_hz": "",
        },
    )

    assert response.status_code == 200
    assert calls[0] == {
        "path": None,
        "backend": "rtl_power",
        "range_id": "frs_gmrs",
        "min_freq_hz": None,
        "max_freq_hz": None,
        "step_hz": 12_500,
        "duration_sec": 10.0,
        "channel_frequencies_hz": [],
        "channel_width_hz": 12_500,
        "gain_db": None,
        "sample_rate_hz": None,
    }

    custom_response = client.post(
        "/api/frequency/baseline",
        json={
            "range_id": "custom",
            "min_freq_hz": 150_000_000,
            "max_freq_hz": 150_500_000,
            "bin_size_hz": 25_000,
            "duration_sec": 10,
            "channel_width_hz": 25_000,
        },
    )

    assert custom_response.status_code == 200
    assert calls[1]["range_id"] == "custom"
    assert calls[1]["min_freq_hz"] == 150_000_000
    assert calls[1]["max_freq_hz"] == 150_500_000


def test_websocket_track_stream_sends_status_and_tracks():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    client.post("/api/session/start", json={"mode": "replay"})

    with client.websocket_connect("/ws/tracks") as websocket:
        payload = websocket.receive_json()

    assert payload["status"]["mode"] == "replay"
    assert {track["domain"] for track in payload["tracks"]} == {"air", "marine"}


def test_spectrum_favorites_api_uses_history(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    history = SqliteHistory(tmp_path / "olab_rf.sqlite")
    client = TestClient(create_app(manager=SessionManager(history=history)))

    response = client.post(
        "/api/spectrum/favorites",
        json={"frequency_hz": 462612500, "modulation": "NFM", "label": "FRS test"},
    )

    assert response.status_code == 200
    assert client.get("/api/spectrum/favorites").json()[0]["label"] == "FRS test"
    delete_response = client.delete("/api/spectrum/favorites/462612500")
    assert delete_response.status_code == 200
    assert client.get("/api/spectrum/favorites").json() == []
    history.close()


def test_spectrum_event_export_endpoints_use_history(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    history = SqliteHistory(tmp_path / "olab_rf.sqlite")
    history.add_spectrum_event(
        SpectrumEvent(
            center_hz=462612500,
            power_db=-38.0,
            noise_floor_db=-58.0,
            threshold_db=12.0,
            preset_id="frs_gmrs",
        )
    )
    history.upsert_frequency_favorite(
        frequency_hz=462612500,
        modulation="NFM",
        label="Test walkie",
    )
    client = TestClient(create_app(manager=SessionManager(history=history)))

    json_response = client.get("/api/spectrum/events/export.json")
    csv_response = client.get("/api/spectrum/events/export.csv")
    events_response = client.get("/api/spectrum/events")

    assert json_response.status_code == 200
    assert json_response.json()[0]["center_hz"] == 462612500
    assert json_response.json()[0]["label"] == "Test walkie"
    assert json_response.json()[0]["annotation_label"] == "FRS/GMRS Ch 3"
    assert json_response.json()[0]["preset_label"] == "FRS/GMRS"
    assert json_response.json()[0]["modulation"] == "NFM"
    assert csv_response.status_code == 200
    assert "annotation_label" in csv_response.text
    assert "Test walkie" in csv_response.text
    assert "462612500" in csv_response.text
    assert events_response.status_code == 200
    assert events_response.json()[0]["label"] == "Test walkie"
    history.close()


def test_live_spectrum_endpoint_enriches_events_with_catalog_and_favorites(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    history = SqliteHistory(tmp_path / "olab_rf.sqlite")
    history.upsert_frequency_favorite(
        frequency_hz=462612500,
        modulation="NFM",
        label="Test walkie",
    )
    manager = SessionManager(history=history)
    manager._spectrum_events.append(
        SpectrumEvent(
            center_hz=462612500,
            power_db=-38.0,
            noise_floor_db=-58.0,
            threshold_db=12.0,
            preset_id="frs_gmrs",
        )
    )
    client = TestClient(create_app(manager=manager))

    spectrum = client.get("/api/spectrum").json()

    assert spectrum["events"][0]["label"] == "Test walkie"
    assert spectrum["events"][0]["annotation_label"] == "FRS/GMRS Ch 3"
    assert spectrum["events"][0]["range_label"] == "FRS/GMRS"
    assert spectrum["events"][0]["modulation"] == "NFM"
    history.close()
