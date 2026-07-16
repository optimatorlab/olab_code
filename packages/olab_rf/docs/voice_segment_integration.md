# Voice Segment Integration

This guide is the transport contract for applications that consume `olab_rf`
radio voice segments. It applies to NATS, HTTP/WebSocket services, and other
framework adapters. The Python library owns receiver access, carrier-window
detection, PCM segmentation, and WAV creation. Consumers own presentation,
message transport, and transcription.

The web demo and Jupyter notebook are examples only; neither is required by an
integration.

## Install

Install `olab-rf` with the `nats` extra in the environment that publishes or
consumes NATS messages — see [`docs/install.md`](install.md) for the normal
(pinned git tag/SHA, or release wheel once available) installation command;
add `nats` to whatever extras list you use there. For local development
against an `olab_code` checkout: `pip install -e "packages/olab_rf[nats]"`.

Live receiver processes also need a local `rtl_fm` installation and an
appropriate `olab_rf.yaml` configuration.

## Core Python Contract

Use one `SessionManager` per physical receiver. The manager owns one active
receiver workflow at a time.

```python
from olab_rf import SessionManager
from olab_rf.config import load_config

manager = SessionManager.from_config(load_config("olab_rf.yaml"))
manager.start_voice_segments(frequency_hz=462_712_500)
```

The transport-neutral integration surface is:

| API | Purpose |
| --- | --- |
| `manager.poll()` | Advance receiver ingestion and segment creation. |
| `manager.current_voice_segment_status()` | Return current capture state and input level meter. |
| `manager.pop_voice_events()` | Return lifecycle transitions since the prior call. |
| `manager.pop_voice_segments()` | Return completed transmission segments since the prior call. |
| `manager.update_voice_segment_settings(...)` | Update carrier-gate settings without restarting capture. |
| `manager.reset_voice_segment_calibration()` | Discard the idle-level estimate between transmissions. |

An async server should own explicit polling. Do not also enable `auto_poll=True`
for that manager. `auto_poll=True` is intended for simple standalone consumers
that do not otherwise have an event loop.

## Status And Lifecycle

`current_voice_segment_status()` returns `VoiceSegmentStatus`, which has a
JSON-friendly `to_dict()` method. Important fields are:

- `capture_running`: binary receiver-process indicator.
- `state`: `calibrating`, `idle`, `transmitting`, `stopped`, or `error`.
- `last_frame_rms_db` and `last_frame_peak_db`: current PCM input-level meter.
- `last_frame_at`: timestamp for the current level reading.
- `noise_floor_db` and `threshold_db`: current relative carrier-gate values.
- `error`: decoder or callback diagnostic, when present.

`pop_voice_events()` returns `VoiceCaptureEvent` objects. Events are:

- `capture_started`
- `transmission_started`
- `transmission_ended`
- `capture_stopped`

Each event has `event`, `session_id`, `state`, `occurred_at`, optional
`segment_id`, and optional `message`. Use `event.to_dict()` for JSON or
MessagePack payloads.

## Completed Segments And Audio

`pop_voice_segments()` returns `RadioVoiceSegment` objects.

```python
for segment in manager.pop_voice_segments():
    metadata = segment.to_dict()  # PCM bytes excluded by default
    wav_bytes = segment.to_wav_bytes()
    audio_blob = segment.to_audio_blob_payload()
```

`segment.to_dict()` is appropriate for browser status/history views. It omits
audio bytes by default. `segment.to_audio_blob_payload()` contains WAV bytes in
the current dictionary shape accepted by `olab_voice.AudioBlob.from_dict(...)`.
It includes `data`, `format`, `session_id`, `source`, `sample_rate`,
`channels`, `timestamp`, `user_id`, and `asset_id`.

Keep metadata and audio as separate messages or endpoints. This lets browser
clients render a transmission immediately and fetch/play audio only when
needed.

## NATS Contract

The optional `NatsRfTransport` uses an injected NATS client and MessagePack
payloads. It does not create network connections itself.

| Subject | Payload |
| --- | --- |
| `rf.voice.status` | `VoiceSegmentStatus.to_dict()` |
| `rf.voice.event` | `VoiceCaptureEvent.to_dict()` |
| `rf.voice.segment` | `RadioVoiceSegment.to_dict()`; metadata only |
| `rf.voice.audio` | `RadioVoiceSegment.to_audio_blob_payload()`; WAV bytes included |

Example publisher loop:

```python
import asyncio

from nats.aio.client import Client as NatsClient

from olab_rf import SessionManager
from olab_rf.config import load_config
from olab_rf.transports.nats import NatsRfTransport


async def publish_voice() -> None:
    manager = SessionManager.from_config(load_config("olab_rf.yaml"))
    client = NatsClient()
    await client.connect("nats://127.0.0.1:4222")
    transport = NatsRfTransport(client)

    manager.start_voice_segments(frequency_hz=462_712_500)
    try:
        while manager.voice_capture_running():
            manager.poll()

            status = manager.current_voice_segment_status()
            if status:
                await transport.publish_voice_status(status)

            for event in manager.pop_voice_events():
                await transport.publish_voice_event(event)

            for segment in manager.pop_voice_segments():
                await transport.publish_voice_segment(segment)
                await transport.publish_voice_audio(segment)

            await asyncio.sleep(0.05)
    finally:
        manager.stop()
        await client.drain()


asyncio.run(publish_voice())
```

NATS consumers need a MessagePack decoder. The `rf.voice.audio` payload uses a
binary `data` field; do not serialize it through JSON without an explicit
encoding such as base64.

## HTTP, WebSocket, And JavaScript Clients

An HTTP or WebSocket adapter should use the same manager methods and forward
JSON dictionaries to JavaScript clients:

```text
VoiceSegmentStatus.to_dict()  -> live status/meter UI
VoiceCaptureEvent.to_dict()   -> capture/transmission notifications
RadioVoiceSegment.to_dict()   -> completed-segment list
```

Deliver WAV data separately as an HTTP download/stream endpoint, object-store
URL, or WebSocket binary frame. A JavaScript client should not receive Python
callbacks, dataclasses, or raw PCM through JSON. For a browser audio element,
serve the segment's WAV bytes with `Content-Type: audio/wav`.

## Live Tuning

The following settings can change without stopping the SDR process:

```python
manager.update_voice_segment_settings(
    threshold_db=8.0,
    min_active_ms=120,
    hang_time_ms=700,
    min_segment_ms=400,
    max_segment_sec=20.0,
    pre_roll_ms=250,
)
```

`threshold_db` is relative to the current local idle FM-noise level, not an
absolute RF or audio level. After a material environment change, call
`reset_voice_segment_calibration()` only while no transmission is active.

Frequency, modulation, gain, sample rate, and backend changes require a new
voice session.
