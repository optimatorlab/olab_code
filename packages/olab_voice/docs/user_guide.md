# olab_voice User Guide

`olab_voice` is a local-first voice package. Audio capture, transcription,
synthesis, and transport adapters are designed so inference runs locally. Model
downloads are explicit setup steps; runtime STT/TTS calls use local files.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install "olab-voice[models,stt-faster-whisper,tts-piper,nats] @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_voice"
olab-voice-download-models
```

Once release wheels exist, prefer pinning the release's exact URL and
SHA-256 hash instead of a git reference. For local development against an
`olab_code` checkout, use `pip install -e "packages/olab_voice[dev,models,stt-faster-whisper,tts-piper,nats]"`
from the repo root instead.

The default local model paths are:

```bash
models/olab_voice/faster-whisper/base.en
models/olab_voice/piper/en_US-lessac-medium.onnx
```

You can override them with:

```bash
export OLAB_VOICE_MODEL_DIR="$PWD/models/olab_voice"
export OLAB_VOICE_FASTER_WHISPER_MODEL="$OLAB_VOICE_MODEL_DIR/faster-whisper/base.en"
export OLAB_VOICE_PIPER_MODEL="$OLAB_VOICE_MODEL_DIR/piper/en_US-lessac-medium.onnx"
```

## CLI

Synthesize local speech to WAV:

```bash
venv/bin/olab-voice-synthesize "107 is listening" /tmp/olab_voice_test.wav
```

Synthesize and immediately play it on the local speaker with `aplay`:

```bash
venv/bin/olab-voice-synthesize "107 is listening" /tmp/olab_voice_test.wav --play
```

For queued/preemptive playback and the `TtsPlaybackService` Python API, see
[`tts_playback.md`](tts_playback.md).

Transcribe a local audio file:

```bash
venv/bin/olab-voice-transcribe /tmp/olab_voice_test.wav --json
```

## Browser Mic Demo

Run the local demo server:

```bash
venv/bin/olab-voice-demo-server --host 127.0.0.1 --port 8765
```

Open this URL in a browser on the same machine:

```text
http://127.0.0.1:8765
```

Press `Start recording`, speak, then press `Stop and transcribe`. The browser
captures microphone audio with `MediaRecorder`, sends it to the local Python
server, and Faster-Whisper transcribes it locally.

## Python API

```python
from olab_voice import synthesize_to_wav, transcribe_file

transcript = transcribe_file("input.wav")
print(transcript.text)

synthesize_to_wav("107 is listening", "response.wav")
```

For push-to-talk workflows that already have audio bytes:

```python
from olab_voice import AudioBlob, CommandSession
from olab_voice.stt.faster_whisper import FasterWhisperTranscriber

transcriber = FasterWhisperTranscriber("models/olab_voice/faster-whisper/base.en")
session = CommandSession(transcriber=transcriber)
event = await session.handle_audio_blob(AudioBlob(data=wav_bytes, format="audio/wav"))
```

## OFM/NATS Integration

`OfmVoiceAdapter` preserves legacy OFM subjects while using the transport-neutral
command session internally:

```python
from olab_voice.integrations import OfmVoiceAdapter
from olab_voice.transports import NatsVoiceTransport

transport = NatsVoiceTransport(existing_nats_client)
adapter = OfmVoiceAdapter(session=command_session, transport=transport)
await adapter.start()
```

The adapter subscribes to `gcs.audio.command` and publishes transcripts to
`gcs.audio.transcribed`. Response audio can be published to `gcs.audio.response`.

## Live OFM NATS Smoke Test

With OFM's NATS server running, start the olab_voice OFM adapter:

```bash
venv/bin/olab-voice-ofm-adapter --nats nats://127.0.0.1:4222
```

Then use OFM's existing browser microphone control. The browser already publishes
recorded blobs to `gcs.audio.command`; this adapter listens on that subject,
transcribes locally with Faster-Whisper, and publishes the transcript to
`gcs.audio.transcribed`.

Do not run this at the same time as OFM's legacy GCS audio callback unless you
expect both services to respond to the same audio command.

You can test the NATS path without the OFM browser by using two terminals.

Terminal 1:

```bash
venv/bin/olab-voice-ofm-adapter --nats nats://127.0.0.1:4222
```

Terminal 2:

```bash
venv/bin/olab-voice-synthesize "107 is listening" /tmp/olab_voice_ofm_smoke.wav
venv/bin/olab-voice-ofm-publish-audio /tmp/olab_voice_ofm_smoke.wav --nats nats://127.0.0.1:4222 --user-id 1 --asset-id 107
```

The second command publishes to `gcs.audio.command` and waits for a
`gcs.audio.transcribed` response.
