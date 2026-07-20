# TTS Playback Deployment Guide

This covers deploying local Piper synthesis plus speaker playback: the
`TtsPlaybackService` API, the `AplayPlaybackSink` Linux backend, model setup,
and troubleshooting. It assumes you have already read
[`user_guide.md`](user_guide.md) for base package setup.

`olab_voice` does not require NATS for this capability. Applications such as
OFM own their own transport/subjects and call `TtsPlaybackService` directly
from their own process (see optimatorlab/ofm#41 for that follow-up work).

## Installing

```bash
pip install "olab-voice[tts-piper,models] @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_voice"
```

For local development against a checkout:

```bash
pip install -e "packages/olab_voice[dev,tts-piper,models]"
```

Speaker playback uses the system `aplay` executable (part of `alsa-utils`),
not a Python package:

```bash
sudo apt-get install alsa-utils
aplay -l   # list ALSA playback devices; note the card/device you want, e.g. hw:1,0
```

If `aplay` is not on `PATH`, `AplayPlaybackSink` raises
`PlaybackUnavailableError` when you try to play — it never fails silently or
falls back to another backend.

## Local Model Setup

Piper synthesis requires an explicit local `.onnx` model plus its paired
`.onnx.json` config; nothing is downloaded at synthesis time. The documented
default voice is `en_US-lessac-medium`, matching `soar_rover`.

```bash
pip install "olab-voice[models] @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_voice"
olab-voice-download-models --only piper
```

This fetches both `en_US-lessac-medium.onnx` and
`en_US-lessac-medium.onnx.json` from the upstream
[`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) catalog
into `models/olab_voice/piper/` and prints the resolved paths. Export them (or
rely on the printed `OLAB_VOICE_PIPER_MODEL` default):

```bash
export OLAB_VOICE_PIPER_MODEL="$PWD/models/olab_voice/piper/en_US-lessac-medium.onnx"
```

`PiperSynthesizer` looks for `<model>.onnx.json` next to the model by default;
pass `config_path=` explicitly if you stage it elsewhere.

### Choosing a Different Voice

Each `TtsPlaybackService` configures exactly one voice; there is no
per-request voice switching in this release. To evaluate an alternative:

1. Browse the upstream catalog at
   [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) and
   pick a locale/speaker/quality tier.
2. Open that voice's model card and confirm its license is acceptable for
   your deployment — licensing varies per voice and is not tracked in this
   repository.
3. Download both the `.onnx` and `.onnx.json` files for that voice into your
   local model directory (e.g. with `hf_hub_download`, following the pattern
   in `download_models.py`).
4. Point `PiperSynthesizer(model_path=..., config_path=...)` (or
   `OLAB_VOICE_PIPER_MODEL`) at the new pair for that service instance.

## Python API

```python
from olab_voice import AplayPlaybackSink, PiperSynthesizer, TtsPlaybackService, TtsRequest

service = TtsPlaybackService(
    PiperSynthesizer.from_env(),      # or PiperSynthesizer(model_path=..., config_path=...)
    AplayPlaybackSink(device="hw:1,0"),  # device is optional; omit for the ALSA default
)

job = service.speak(TtsRequest(text="107 is listening"))
# ... do other work; speak() does not block on synthesis or playback ...
result = job.wait(timeout=10.0)
print(result.outcome)  # "completed", "rejected", "disabled", "synthesis_failed",
                        # "playback_failed", or "preempted"

# Or, for simple scripts:
result = service.speak_and_wait(TtsRequest(text="107 is listening"))

# Alerts jump the queue and cancel whatever is currently playing/pending:
service.speak(TtsRequest(text="obstacle detected", preempt=True))

service.set_enabled(False)  # subsequent speak() calls return outcome="disabled"
print(service.status)       # ServiceStatus(enabled, closed, queue_depth, active)

service.close(timeout=5.0)  # cancels active/pending work and stops the background thread
```

`speak()` and `speak_and_wait()` never require an `await` or an event loop in
the caller — the service owns a background thread with its own asyncio loop
internally. `max_text_length` (default 500) and `max_queue_size` (default 8)
bound resource use; a full queue rejects new ordinary requests explicitly
(`outcome="rejected"`) rather than blocking or dropping them silently.

### `close()` and non-interruptible synthesis

Piper synthesis is a synchronous, non-interruptible call that runs on a
dedicated worker thread so it never blocks `speak()` or preemption. That
worker thread is not daemonic, so if it's still synthesizing when you call
`close()`, `close()` waits (up to `timeout`) for it to actually finish before
returning — otherwise the process would stay alive after `close()` claims
everything is stopped. A `preempt=True` request that arrives mid-synthesis
still returns immediately and is not itself blocked by this; only `close()`
waits.

If `timeout` elapses before that worker thread finishes, `close()` raises
`TimeoutError`. The service is still marked closed (no further `speak()`
calls are accepted) and its event loop thread is stopped either way, but the
synthesis worker thread may still be running in the background and your
process will not exit until it does. Pass `timeout=None` (the default) to
wait indefinitely instead.

## Local CLI Smoke Test

```bash
olab-voice-synthesize "107 is listening" /tmp/olab_voice_smoke.wav --play
olab-voice-synthesize "107 is listening" /tmp/olab_voice_smoke.wav --play --device hw:1,0
```

`--play` synthesizes once, writes the WAV, then plays it locally through
`AplayPlaybackSink`. This is a one-shot smoke/deployment aid, not the
queued/preemptive service above — use it to validate that Piper and the
speaker/ALSA device work before wiring up `TtsPlaybackService`.

## Verification Sequence

1. `aplay -l` shows the intended output device.
2. `olab-voice-download-models --only piper` reports both `.onnx` and
   `.onnx.json` paths and they exist on disk.
3. `olab-voice-synthesize "107 is listening" /tmp/olab_voice_smoke.wav --play`
   writes a WAV and you hear it.
4. In Python, construct a `TtsPlaybackService` as above, call
   `speak_and_wait(...)`, confirm `result.outcome == "completed"`, then call
   `service.speak(TtsRequest(text="...", preempt=True))` while something else
   is playing and confirm it interrupts. Call `service.close()` and confirm
   the process can exit cleanly (no non-daemon threads left running).

## Troubleshooting

- **`PlaybackUnavailableError: 'aplay' executable not found on PATH`** —
  install `alsa-utils`, or pass `executable=` to `AplayPlaybackSink` if it's
  installed under a different name/path.
- **`RuntimeError: aplay exited with code 1: ...`** — the message includes
  `aplay`'s stderr; common causes are an invalid `-D` device name (check
  `aplay -L` for full device identifiers) or the device being held by
  another process.
- **`PiperUnavailableError`** — install the `tts-piper` extra
  (`piper-tts==1.4.2`).
- **`FileNotFoundError` for the model/config path** — re-run
  `olab-voice-download-models` or double check `OLAB_VOICE_PIPER_MODEL`
  points at an existing `.onnx` file with a sibling `.onnx.json`.
- **Nothing plays but no error is raised** — confirm you're calling
  `job.wait(...)` or `speak_and_wait(...)`; `speak()` alone returns
  immediately without playing anything itself.
