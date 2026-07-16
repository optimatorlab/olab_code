from __future__ import annotations

from olab_rf.decoders.replay import ReplayDecoder
from olab_rf.history import SqliteHistory
from olab_rf.services.session_manager import SessionManager


def test_replay_decoder_emits_air_and_marine_tracks():
    messages = list(ReplayDecoder(steps=1).messages())

    assert {message.track.domain for message in messages if message.track} == {"air", "marine"}


def test_session_manager_start_replay_updates_tracks_and_status():
    manager = SessionManager()

    session = manager.start_replay(steps=2)

    assert session.mode == "replay"
    assert manager.status.process_running is True
    assert manager.status.message_count == 2
    assert len(manager.track_store.list()) == 2


def test_session_manager_writes_replay_to_history(tmp_path):
    history = SqliteHistory(tmp_path / "olab_rf.sqlite")
    manager = SessionManager(history=history)

    manager.start_replay(steps=1)

    assert {track.domain for track in history.list_tracks()} == {"air", "marine"}
    assert len(history.trail_for("adsb-N123RF")) == 1
    history.close()
