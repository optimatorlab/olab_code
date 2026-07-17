from __future__ import annotations

from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from olab_voice.audio.models import AudioBlob
from olab_voice.stt.base import TranscriptEvent
from olab_voice.web_demo import INDEX_HTML, make_handler


@dataclass(slots=True)
class _FakeTranscriber:
    seen: list[AudioBlob] = field(default_factory=list)

    async def transcribe(self, audio: AudioBlob) -> TranscriptEvent:
        self.seen.append(audio)
        return TranscriptEvent(text="hello hardware voice", session_id=audio.session_id)


def test_index_html_contains_browser_capture_flow():
    assert "navigator.mediaDevices.getUserMedia" in INDEX_HTML
    assert "MediaRecorder" in INDEX_HTML
    assert "/api/transcribe" in INDEX_HTML


def test_demo_handler_health_endpoint():
    transcriber = _FakeTranscriber()
    with _server(transcriber) as base_url:
        with urlopen(f"{base_url}/health", timeout=5) as response:
            assert response.status == 200
            assert response.read() == b'{"ok": true}'


def test_demo_handler_transcribes_posted_audio():
    transcriber = _FakeTranscriber()
    with _server(transcriber) as base_url:
        request = Request(
            f"{base_url}/api/transcribe",
            data=b"fake-webm",
            headers={"Content-Type": "audio/webm;codecs=opus"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")

    assert '"text": "hello hardware voice"' in body
    assert transcriber.seen[0].data == b"fake-webm"
    assert transcriber.seen[0].format == "audio/webm;codecs=opus"
    assert transcriber.seen[0].source == "browser"


def test_demo_handler_rejects_empty_audio_payload():
    transcriber = _FakeTranscriber()
    with _server(transcriber) as base_url:
        request = Request(f"{base_url}/api/transcribe", data=b"", method="POST")
        try:
            urlopen(request, timeout=5)
        except HTTPError as exc:
            assert exc.code == 500
            assert "empty audio payload" in exc.read().decode("utf-8")
        else:
            raise AssertionError("expected HTTPError")


class _server:
    def __init__(self, transcriber: _FakeTranscriber):
        self.transcriber = transcriber
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(transcriber))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
