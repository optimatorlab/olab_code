# Issue #2: Consolidate audio capabilities formerly requested for `ub_audio.py`

## Goal

Finish the remaining, concrete audio-capability work in the extracted package
architecture without recreating a monolithic `ub_audio.py`: `olab_audio` owns
local capture, recording, levels, and DSP analysis; `olab_voice` owns local
STT/TTS, browser/transport integration, and typed audio/transcript contracts.

## Context from codebase exploration

- The issue predates the extraction. Its requested Whisper/Faster-Whisper,
  Vosk, local TTS, browser capture, NumPy-frame processing, dB, spectrum, and
  note features now span two packages by design.
- `olab_voice` already provides local batch Faster-Whisper, streaming
  Faster-Whisper with VAD/endpoints and bounded backpressure, streaming Vosk
  with hypothesis/final events, a hybrid backend, Piper TTS, explicit model
  download/setup, typed `AudioBlob`/`AudioFrame` data, and a browser
  push-to-talk demo using `getUserMedia`/`MediaRecorder` and HTTP upload.
- `olab_audio.Mic` already gives Python capture callbacks as float32 NumPy
  arrays, dB readings, recording, safe device enumeration, resampling, and
  optional `analysis` objects (`Wave`, `Spectrum`, `Spectrogram`,
  `pitch_map`). It deliberately has no embedded STT or network streaming.
- The migration plan explicitly reserves an optional `olab_voice`-side
  `olab_audio` adapter. Neither base distribution should depend on the other,
  and current browser push-to-talk is an intentional HTTP blob flow, not a
  WebSocket/ROS requirement.

## Agreed scope and non-goals

- Treat implemented STT/TTS/browser batch capture as delivered foundations;
  do not duplicate Whisper, Vosk, Piper, or a second browser capture server.
- The primary vertical slice is live Python-mic capture into existing
  streaming STT using in-memory PCM/NumPy conversion, plus reusable analysis
  metrics for UI or Python consumers.
- Retain HTTP POST for push-to-talk. Add a browser live-frame transport only
  after a real consumer requires partial transcripts; choose one transport at
  that time rather than adding both WebSockets and ROS speculatively.
- Speaker diarization and robust polyphonic note recognition are research-
  sized follow-ons, not acceptance criteria for the capture/STT/TTS slice.
  No cloud inference or silent model downloads.

## Proposed design

1. Add an optional `olab_voice` integration extra that depends on compatible
   `olab-audio` and PyAudio support. Place the adapter in
   `olab_voice.integrations`, never in `olab_audio`, to preserve dependency
   direction and base-install isolation.
2. Implement an `AudioFrameSource` adapter around `olab_audio.Mic`. It opens
   a selected safe input device, converts each callback to ordered mono
   `pcm_s16le` `AudioFrame` objects at the streaming engine's declared sample
   rate, and uses the existing `StreamResampler` when device and engine rates
   differ. Keep the PortAudio callback bounded: enqueue/copy a frame and do
   conversion/transcription in worker context; expose a documented bounded
   queue/drop-or-backpressure policy and deterministic stop/flush behavior.
3. Provide a small orchestration API/example that binds this frame source to
   either `FasterWhisperStreamingTranscriber` (VAD, interval/endpoint finals)
   or `VoskStreamingTranscriber` (partial hypotheses/finals), publishes the
   existing `TranscriptEvent` stream, and retains capture timestamps/session
   IDs. Make engine/model selection explicit rather than inferring it.
4. Define reusable, model-free analysis results for a PCM/NumPy window:
   peak and RMS dBFS, frequency bins/magnitude spectrum, and an optional
   monophonic dominant-pitch estimate mapped to note + cents/confidence.
   Put pure signal math in `olab_audio`'s optional analysis layer and keep
   rendering/UI adapters outside it. Specify window size, hop size, channel
   downmix policy, floor/silence behavior, and the validity/confidence rules
   so a browser or Python client can render the same data.
5. Extend the existing browser demo only with the smallest compatible UI
   surface: show microphone permission/recording status and, for live mode
   when it is intentionally added, level/spectrum data plus transcript
   hypotheses/finals. Keep the existing upload endpoint for batch
   push-to-talk and use loopback/local-host restrictions and message-size/rate
   limits for any future live transport.
6. Keep file transcription (`AudioBlob` with `source="file"`) and Piper WAV
   synthesis as supported batch paths. Document supported input codecs and
   require decoding at the batch boundary; streaming paths accept only the
   existing explicit PCM frame contract.

## Implementation steps

1. Audit and document the issue checklist against the existing public APIs,
   README/user guide, extras, commands, and tests; mark the existing local
   Faster-Whisper, Vosk, Piper, browser push-to-talk, and spectrum foundation
   as covered with links/examples.
2. Add the optional `olab_voice`↔`olab_audio` adapter packaging and a
   lifecycle-managed `MicAudioFrameSource`; define device selection, target
   PCM format, mono/downmix policy, sequence/session/timestamp ownership,
   queue capacity, and stop/error semantics.
3. Add a streaming runner/example that connects the frame source to the
   existing Vosk and Faster-Whisper streaming protocols without writing WAV
   files or invoking STT inside the audio callback.
4. Add analysis value objects/functions and tests for dBFS, spectra, silence,
   sample-rate/window behavior, and synthetic single-tone note detection.
   Expose the results through a stable Python API; keep plotting optional and
   do not make browser code a dependency of `olab_audio`.
5. Update the local browser demo/documentation for the confirmed live mode.
   Reuse the typed event/result schemas, provide visible errors for permission,
   unsupported codecs, model availability, and overload, and retain batch
   HTTP behavior as a fallback.
6. Create separate follow-up issues/prototypes for speaker diarization and
   polyphonic/multi-instrument pitch tracking. Select algorithms, datasets,
   performance targets, consent/privacy behavior, and evaluation metrics
   before adding runtime dependencies or API promises.

## Testing and verification

- Run the model-free `pytest packages/olab_voice/tests -v` and
  `pytest packages/olab_audio/tests -v` suites, including fake-Mic and fake
  STT backend tests for frame order, resampling, queue overflow, cleanup,
  event timestamps, and no disk writes in the live path.
- Add contract tests proving every adapter frame is valid mono `pcm_s16le` at
  its advertised sample rate and preserving session/sequence metadata.
- Add deterministic DSP tests using silence and generated tones for dBFS,
  spectrum peaks, note/confidence thresholds, and channel downmix behavior.
- Keep real microphone/browser/model tests opt-in. Manually verify browser
  permission denial, batch upload transcription, live levels/spectrum (if
  enabled), Vosk partials, Faster-Whisper endpoint finals/VAD, and Piper WAV
  playback with explicitly provisioned local models.

## Risks and mitigations

- Callback overload can cause dropouts: bound queues, make overflow visible,
  keep inference off the PortAudio callback, and test cleanup/backpressure.
- STT engines require strict PCM/rate/channel inputs: validate at the adapter
  boundary and use the existing stateful resampler, never per-chunk stateless
  conversion.
- Browser codecs differ by platform: preserve the current blob-based decode
  boundary and advertise PCM-only requirements for live framing.
- dB/spectrum/note displays can be misread as calibrated measurement or
  reliable transcription: label dBFS/reference/windowing, return confidence,
  and suppress note output for silence/ambiguous signals.
- Voice identity processing raises accuracy and consent/privacy obligations:
  keep diarization out of the initial implementation until those requirements
  are explicitly defined.
