from __future__ import annotations

from olab_rf.cli import _default_config_path, check_main, history_main, replay_main
from olab_rf.history import SqliteHistory
from olab_rf.models.scanning import FrequencyCandidate, FrequencyScanRequest, FrequencyScanStatus


def test_check_main_prints_tool_report(tmp_path, capsys, monkeypatch):
    def fake_environment_check(**kwargs):
        assert kwargs["config_path"] is None
        assert kwargs["tool_paths"]["readsb"] == "readsb"
        return {"tools": [], "context": {"config_path": kwargs["config_path"]}}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("olab_rf.cli.environment_check", fake_environment_check)

    assert check_main([]) == 0

    output = capsys.readouterr().out
    assert '"tools"' in output


def test_check_main_uses_local_config(tmp_path, capsys, monkeypatch):
    config_path = tmp_path / "olab_rf.yaml"
    config_path.write_text(
        "decoders:\n  readsb:\n    path: external/readsb/readsb\n",
        encoding="utf-8",
    )

    def fake_environment_check(**kwargs):
        assert kwargs["config_path"] == "olab_rf.yaml"
        assert kwargs["tool_paths"]["readsb"] == "external/readsb/readsb"
        return {"tools": [], "context": {"config_path": kwargs["config_path"]}}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("olab_rf.cli.environment_check", fake_environment_check)

    assert check_main([]) == 0

    output = capsys.readouterr().out
    assert '"config_path": "olab_rf.yaml"' in output


def test_replay_main_prints_tracks(capsys):
    assert replay_main(["--steps", "1"]) == 0

    output = capsys.readouterr().out
    assert "adsb-N123RF" in output
    assert "ais-367000001" in output


def test_default_config_path_uses_local_olab_rf_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _default_config_path(None) is None

    (tmp_path / "olab_rf.yaml").write_text("history: {}\n", encoding="utf-8")

    assert _default_config_path(None) == "olab_rf.yaml"
    assert _default_config_path("custom.yaml") == "custom.yaml"


def test_history_main_prints_favorites(tmp_path, capsys):
    config_path = tmp_path / "olab_rf.yaml"
    db_path = tmp_path / "olab_rf.sqlite"
    config_path.write_text(
        f"history:\n  sqlite_path: {db_path}\n",
        encoding="utf-8",
    )
    history = SqliteHistory(db_path)
    history.upsert_frequency_favorite(
        frequency_hz=462_612_500,
        modulation="NFM",
        label="FRS test",
    )
    history.close()

    assert history_main(["favorites", "--config", str(config_path)]) == 0

    output = capsys.readouterr().out
    assert "frequency_hz" in output
    assert "FRS test" in output


def test_history_main_prints_concise_frequency_scan_table(tmp_path, capsys):
    config_path = tmp_path / "olab_rf.yaml"
    db_path = tmp_path / "olab_rf.sqlite"
    config_path.write_text(
        f"history:\n  sqlite_path: {db_path}\n",
        encoding="utf-8",
    )
    history = SqliteHistory(db_path)
    history.add_frequency_scan(
        FrequencyScanStatus(
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
    )
    history.close()

    assert history_main(["frequency-scans", "--config", str(config_path)]) == 0

    output = capsys.readouterr().out
    assert "range_hz" in output
    assert "best_frequency_hz" in output
    assert "observed_frequency_hz" in output
    assert "462612500" in output
    assert "462611164" in output
    assert "FRS/GMRS Ch 3" in output
    assert "iq_peak" in output
    assert "baseline_power_db" not in output
