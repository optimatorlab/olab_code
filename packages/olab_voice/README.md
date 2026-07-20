# olab_voice

`olab_voice` is a local-first Python package for voice capture, speech-to-text,
text-to-speech, wake phrase detection, and transport adapters. It is
capture/transport-agnostic by design: its `audio/` submodule is just
dataclasses (`AudioBlob`, `AudioFrame`) and `Protocol` interfaces
(`AudioFrameSource`, `AudioBlobSource`, `AudioPlaybackSink`) — it does no
device I/O itself. An `olab_audio`-backed adapter bridges the two optionally,
once both packages' APIs are stable; `olab_voice`'s base install does not
depend on `olab_audio`.

Migrated from `~/Projects/CoG/tts_practice_migration/src/ub_voice` (the
canonical copy — see
[`docs/plans/olab_packages_reorg_plan.md`](../../docs/plans/olab_packages_reorg_plan.md)'s
"The `ub_voice` fork" section for why). `tts_practice`'s competing in-tree
copy was retired, not migrated.

## Installing

```bash
pip install "olab-voice[dev,stt-faster-whisper,tts-piper] @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_voice"
```

For local development against a checkout of this repo:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e "packages/olab_voice[dev,stt-faster-whisper,tts-piper]"
```

If Faster-Whisper or Piper are not available on the target host, install only
`[dev]` — backend tests self-skip when their model/dependency isn't present.

## Model Policy

`olab_voice` inference is local-only. Models may be downloaded explicitly
during setup, but runtime STT/TTS backends do not silently download models or
call cloud services. Backends accept explicit local paths and fail clearly
when required models are missing.

Default project-local model root:

```bash
export OLAB_VOICE_MODEL_DIR="$PWD/models/olab_voice"
export OLAB_VOICE_FASTER_WHISPER_MODEL="$OLAB_VOICE_MODEL_DIR/faster-whisper/base.en"
export OLAB_VOICE_PIPER_MODEL="$OLAB_VOICE_MODEL_DIR/piper/en_US-lessac-medium.onnx"
```

### Download Local Models

```bash
pip install "olab-voice[models,stt-faster-whisper,tts-piper] @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_voice"
olab-voice-download-models
```

The command downloads Faster-Whisper `Systran/faster-whisper-base.en` and
Piper `en_US-lessac-medium` (from `rhasspy/piper-voices`) into
`models/olab_voice/`. After it completes, export the printed paths and run
the full backend test suite — see
[`docs/user_guide.md`](docs/user_guide.md) for the manual-download fallback
and further detail.

## OFM Compatibility Adapter

`OfmVoiceAdapter` bridges the legacy OFM voice subjects without making the
core package depend on OFM. It subscribes to `gcs.audio.command`, passes
decoded audio blobs into a `CommandSession`, and publishes legacy transcript
payloads to `gcs.audio.transcribed`.

```python
from olab_voice.integrations import OfmVoiceAdapter

adapter = OfmVoiceAdapter(session=command_session, transport=nats_voice_transport)
await adapter.start()
```

## Transport Contracts

Transport subject and payload helpers live under `olab_voice.transports`.
They do not require NATS, but define the subjects and MessagePack-friendly
dict payloads that NATS adapters use. Legacy OFM subjects (`gcs.audio.*`)
are preserved; generic new functionality uses `voice.sessions.<session_id>.*`.
Install `[nats]` to enable the injectable NATS transport wrapper.

## Command Sessions

`CommandSession` is the transport-neutral push-to-talk workflow. It accepts a
complete `AudioBlob`, runs a `BatchTranscriber`, returns a final
`TranscriptEvent`, and optionally forwards it to transcript recipients. It
does not know about ROS, NATS, browsers, microphones, or command parsing.

```python
from olab_voice import AudioBlob, CommandSession
from olab_voice.stt.faster_whisper import FasterWhisperTranscriber

transcriber = FasterWhisperTranscriber("models/olab_voice/faster-whisper/base.en")
session = CommandSession(transcriber=transcriber)

event = await session.handle_audio_blob(
    AudioBlob(data=wav_bytes, format="audio/wav", source="browser", asset_id=107)
)
print(event.text)
```

## Python API

```python
from olab_voice import synthesize_to_wav, transcribe_file

transcript = transcribe_file("input.wav")
print(transcript.text)

synthesize_to_wav("107 is listening", "response.wav")
```

## Local Speaker Playback

`TtsPlaybackService` adds queued/preemptive local playback on top of Piper
synthesis, with a synchronous API so callers never need `await`:

```python
from olab_voice import AplayPlaybackSink, PiperSynthesizer, TtsPlaybackService, TtsRequest

service = TtsPlaybackService(PiperSynthesizer.from_env(), AplayPlaybackSink())
result = service.speak_and_wait(TtsRequest(text="107 is listening"))
service.close()
```

See [`docs/tts_playback.md`](docs/tts_playback.md) for Piper/ALSA setup,
preemption semantics, and troubleshooting.

## CLI Smoke Tools

```bash
olab-voice-transcribe path/to/audio.wav
olab-voice-synthesize "107 is listening" out.wav
```

Both commands run inference locally and default to the project-local model
paths prepared by `olab-voice-download-models`; override with `--model`.

## Relationship to OFM and realtime_transcription

OFM integration uses NATS adapters and preserves the legacy `gcs.audio.*`
subjects during migration. `CoG/realtime_transcription` consumes this
package's streaming STT engines (`stt/vosk.py`,
`stt/faster_whisper_streaming.py`, `stt/hybrid.py`) directly — see the plan
doc's "The `ub_voice` fork" section for that history.

## Test Strategy

Model-free contract tests (message/event schemas, transport adapters, etc.)
run unconditionally in CI. Tests that require an actual STT/TTS model
self-skip when the model isn't configured locally (see `tests/`) — they are
not something a generic CI runner is expected to provision.
