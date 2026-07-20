# `olab_voice` TTS Playback and Piper Deployment Plan

## Goal

Deliver a reusable, local-first Python TTS capability in `olab_voice`: explicit
local Piper synthesis, Linux speaker playback, queued/preemptive delivery, and
a lifecycle-managed service that applications invoke directly.

`olab_voice` must not require NATS for this capability. Applications such as
OFM own their own NATS subjects, subscriptions, controls, and deployment
processes, then call the `olab_voice` service through its Python API.

## Context from codebase exploration

- `olab_voice` already exposes `TtsRequest`, `TtsAudio`, a
  `SpeechSynthesizer` protocol, and `PiperSynthesizer`. Piper takes an
  explicit local `.onnx` path and produces WAV bytes without runtime downloads.
- `AudioPlaybackSink` is currently only a protocol. There is no concrete
  speaker backend, playback queue, cancellation policy, lifecycle, or playback
  CLI.
- The package already has optional NATS transport helpers for existing voice
  integrations. Those remain optional and unchanged; they are not part of the
  playback API proposed here.
- The package policy is local-first, explicit model setup, and optional
  dependencies. The implementation must preserve that policy.

## Agreed decisions

- `olab_voice` owns playback; applications do not need to reimplement a
  speaker player.
- The first supported backend is Linux-first and runs the installed `aplay`
  executable without a shell. The public queue/controller interface remains
  platform-neutral so a later PyAudio/PortAudio backend can support other
  operating systems without an API change.
- An ordinary request queues behind active and pending speech. A request with
  `preempt=true` stops active playback, clears pending playback, and starts
  next; it is recorded as cancellation rather than failure.
- Piper work remains local and explicit. The initial default/configured voice
  is `en_US-lessac-medium`, the voice used in soar_rover. Each service loads
  one voice; per-request voice selection is out of scope.
- The package exposes direct Python lifecycle/control methods, including an
  in-memory enabled state. Applications decide how to change that state at
  runtime and whether to persist it.
- Consumers are assumed trusted in this initial deployment model, while inputs
  and resource usage are strictly validated and bounded.

## Proposed design

### Playback API and Linux backend

1. Add a concrete playback package with:
   - an internal player protocol for `TtsAudio`;
   - a lifecycle-managed playback controller owning its queue, background task,
     cancellation/preemption, `start()`, and orderly `close()/drain()`;
   - deterministic outcomes for completed, rejected, failed, and preempted
     items; and
   - configurable maximum text length and queue size. Full queues reject new
     ordinary requests explicitly rather than dropping them silently.
2. Implement an optional `AplayPlaybackSink` for Linux. It invokes `aplay`
   with `asyncio.create_subprocess_exec`, passes WAV bytes through stdin, and
   accepts an optional ALSA device name. It must never construct a shell
   command from text, models, or device paths.
3. Preserve or non-breakingly adapt the existing `AudioPlaybackSink` export so
   existing consumers remain valid.
4. Reserve a future optional PyAudio/PortAudio backend as a second
   implementation of the same player protocol. Cross-platform playback is not
   part of this delivery.

### Direct synthesis-and-playback service

1. Extend `TtsRequest` with `preempt: bool = False`; keep it separate from
   `TtsAudio`, which is already synthesized media.
2. Add a `TtsPlaybackService` (exact name to be finalized during
   implementation) that owns one `SpeechSynthesizer` and one playback
   controller. Its primary API is synchronous: `speak(request)` immediately
   validates and queues work, returning a typed job/handle;
   `speak_and_wait(request)` blocks until that job completes for simple
   scripts. Both honor `preempt`.
3. Expose direct synchronous lifecycle and control methods: `start()`,
   `set_enabled(bool)`, readable enabled/queue state, and `close()/drain()`.
   Startup may be lazy. Disabled services return a documented disabled result;
   they do not synthesize or touch the speaker.
4. A returned job supports synchronous status inspection and `wait(timeout)`;
   typed outcomes report accepted, disabled, rejected, synthesis-failed,
   playback-failed, completed, and preempted work. Do not couple outcomes to a
   transport.
5. The service owns a background worker thread (and any internal async/subprocess
   machinery), so callers do not need an event loop or `await`. A future async
   adapter may be added for applications that prefer it, but is not the primary
   API in this slice.

### Local CLI and documentation

1. Add a local playback CLI or a `--play` mode on the existing synthesis CLI.
   It takes an explicit model/config path and optional ALSA device, validates
   `aplay` availability, synthesizes once, and plays locally. It is a smoke and
   deployment aid, not a service or message consumer.
2. Add a focused TTS deployment guide covering installation extras,
   `alsa-utils`/`aplay`, local model directory permissions, environment/config
   paths, ALSA device selection, direct service lifecycle, troubleshooting, and
   a local verification sequence.
3. Make acquisition explicit. Enhance the existing model download workflow to
   obtain both the `.onnx` model and paired `.onnx.json` configuration for the
   documented default voice, report their exact paths, and never download at
   synthesis time.
4. Explain that this release configures one voice per service, defaulting to
   `en_US-lessac-medium`. Document how to browse the upstream Piper catalog,
   compare locale/speaker/quality, inspect model-card licensing, and stage an
   alternate local model for a later service configuration. Link the upstream
   catalog rather than copying a stale catalog into the repository.

## Implementation steps

1. Finalize `TtsRequest` preemption, direct service result/outcome models, and
   their serialization/backward-compatibility tests.
2. Implement the backend-neutral controller and its model-free queue,
   lifecycle, bounded-resource, enabled-state, and preemption tests.
3. Implement the optional Linux `aplay` backend with subprocess-fake unit
   tests and an opt-in live-device smoke test.
4. Implement the synchronous `TtsPlaybackService` and local playback CLI,
   ensuring neither imports nor requires NATS.
5. Update the Piper model downloader and write the TTS deployment and
   voice-selection documentation.
6. Validate an end-to-end Linux path: direct Python request, Piper WAV
   synthesis, selected speaker playback, preemption, disable/enable, and
   orderly shutdown.
7. Publish/tag the `olab_voice` change before application integrations use it.

## Handoff contract for application integrations

Applications such as OFM can depend on the released `olab-voice` version and
own all transport behavior. They must:

1. provision `piper-tts`, the Linux playback extra, `aplay`, and one explicit
   local Piper model/config;
2. construct one `PiperSynthesizer`, Linux sink, and `TtsPlaybackService` in
   their own process;
3. call `service.speak(TtsRequest(text=..., preempt=...))` after translating
   their own message/API into the package contract; simple scripts can call
   blocking `service.speak_and_wait(...)` or `job.wait(...)` instead;
4. call `service.set_enabled(...)` in response to their own configuration or
   control mechanism; and
5. translate package outcomes into their own logging, status, and messaging
   contracts.

## Testing and verification

- Model-free unit tests for request serialization, validation, enabled state,
  queue ordering/full rejection, preemption, shutdown, and service outcomes.
- Subprocess-fake tests for `aplay` stdin, device arguments, cancellation,
  non-zero exits, and missing executable errors.
- Existing fake-Piper tests continue to validate WAV assembly; real-model tests
  remain opt-in and require the configured Lessac model.
- A manual Linux smoke test verifies direct Python/API and CLI paths against
  the configured Piper model and speaker.

## Risks and mitigations

- **Device/ALSA failures:** keep playback optional, validate CLI/service setup,
  return typed failures, and expose device configuration.
- **Blocking synthesis:** keep it in the service-owned worker thread so callers
  and application event loops remain responsive.
- **Unbounded work:** enforce text and queue bounds and return rejection
  outcomes.
- **Model/license drift:** use explicit local paths, download both model and
  config, and link upstream model cards.

## Out of scope

- NATS request/control/status subjects or a NATS worker in `olab_voice`.
- Cross-platform backend implementation.
- Multiple installed voices or per-request voice switching.
- Browser/mobile JavaScript changes.
- Cloud inference, silent runtime downloads, persistent runtime-control
  overrides, application-level authorization, and OFM-specific business logic.
