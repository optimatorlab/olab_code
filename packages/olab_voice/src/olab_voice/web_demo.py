from __future__ import annotations

import argparse
import asyncio
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence

from olab_voice.audio.models import AudioBlob
from olab_voice.defaults import default_faster_whisper_model
from olab_voice.stt.faster_whisper import FasterWhisperTranscriber


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>olab_voice live test</title>
  <style>
    :root { color-scheme: light dark; font-family: Inter, system-ui, -apple-system, sans-serif; }
    body { margin: 0; background: #f7f8f5; color: #1f2523; }
    main { max-width: 760px; margin: 0 auto; padding: 32px 20px; }
    h1 { font-size: 28px; margin: 0 0 24px; }
    .panel { border: 1px solid #ccd2cc; border-radius: 8px; padding: 18px; background: #fff; }
    .controls { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
    button { border: 1px solid #1f2523; border-radius: 6px; padding: 10px 14px; font: inherit; cursor: pointer; }
    button.primary { background: #245b47; color: white; border-color: #245b47; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    .status { margin-top: 14px; min-height: 24px; color: #4c5652; }
    .transcript { margin-top: 18px; padding: 16px; min-height: 64px; border-radius: 6px; background: #eef2ee; white-space: pre-wrap; }
    .meta { margin-top: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: #5b645f; white-space: pre-wrap; }
  </style>
</head>
<body>
  <main>
    <h1>olab_voice live test</h1>
    <section class="panel">
      <div class="controls">
        <button id="start" class="primary">Start recording</button>
        <button id="stop" disabled>Stop and transcribe</button>
      </div>
      <div id="status" class="status">Ready.</div>
      <div id="transcript" class="transcript"></div>
      <div id="meta" class="meta"></div>
    </section>
  </main>
<script>
let recorder = null;
let chunks = [];
const startButton = document.getElementById('start');
const stopButton = document.getElementById('stop');
const statusEl = document.getElementById('status');
const transcriptEl = document.getElementById('transcript');
const metaEl = document.getElementById('meta');

function setStatus(text) { statusEl.textContent = text; }

startButton.addEventListener('click', async () => {
  transcriptEl.textContent = '';
  metaEl.textContent = '';
  chunks = [];
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const options = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? { mimeType: 'audio/webm;codecs=opus' }
    : undefined;
  recorder = new MediaRecorder(stream, options);
  recorder.ondataavailable = event => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  };
  recorder.onstop = async () => {
    stream.getTracks().forEach(track => track.stop());
    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
    setStatus(`Uploading ${blob.size} bytes for local transcription...`);
    const response = await fetch('/api/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': blob.type || 'application/octet-stream' },
      body: blob,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || response.statusText);
    transcriptEl.textContent = payload.text || '';
    metaEl.textContent = JSON.stringify(payload, null, 2);
    setStatus('Done.');
  };
  recorder.start();
  startButton.disabled = true;
  stopButton.disabled = false;
  setStatus('Recording...');
});

stopButton.addEventListener('click', () => {
  if (!recorder || recorder.state === 'inactive') return;
  stopButton.disabled = true;
  startButton.disabled = false;
  setStatus('Stopping...');
  recorder.stop();
});
</script>
</body>
</html>
"""


def demo_server_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a local browser push-to-talk olab_voice demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", type=Path, default=default_faster_whisper_model())
    parser.add_argument("--language", default="en")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    args = parser.parse_args(argv)

    transcriber = FasterWhisperTranscriber(
        model_path=args.model,
        language=args.language or None,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
    )
    handler_cls = make_handler(transcriber)
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    print(f"olab_voice demo server listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def make_handler(transcriber: Any) -> type[BaseHTTPRequestHandler]:
    class DemoHandler(BaseHTTPRequestHandler):
        server_version = "olab_voice_demo/0.1"

        def do_GET(self) -> None:
            if self.path in {"/", "/index.html"}:
                self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path == "/health":
                self._send_json({"ok": True})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path != "/api/transcribe":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = self.rfile.read(length)
                if not data:
                    raise ValueError("empty audio payload")
                content_type = self.headers.get("Content-Type") or "application/octet-stream"
                audio = AudioBlob(data=data, format=content_type, source="browser")
                event = asyncio.run(transcriber.transcribe(audio))
                self._send_json(_jsonable(event.to_dict()))
            except Exception as exc:  # noqa: BLE001 - dev server should return readable errors.
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send_bytes(
                json.dumps(payload, sort_keys=True).encode("utf-8"),
                "application/json",
                status=status,
            )

        def _send_bytes(
            self,
            data: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return DemoHandler


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if not isinstance(value, bytes)}


if __name__ == "__main__":
    raise SystemExit(demo_server_main())
