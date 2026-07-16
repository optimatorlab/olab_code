# olab_audio

Audio device I/O (`Mic`, `Speaker`, recording, device enumeration,
PulseAudio port control) plus an optional `analysis` extra with a
DSP/teaching toolkit (`Wave`, `Spectrogram`, `Spectrum`, synthesis,
plotting).

**Status: scaffold only — not yet migrated.** Source will extract from
`~/Projects/ofm/ofm/sensor/ub_audio.py` per
[`docs/plans/olab_packages_reorg_plan.md`](../../docs/plans/olab_packages_reorg_plan.md)'s
"`olab_audio` v1 scope" section and Migration sequence step 5 — including
fixes for the known ALSA pseudo-device segfault risk, hardcoded sample
rate, `Mic.start()` failure handling, and duplicated `convert_to_db()`.
Embedded Whisper transcription hooks are excluded, not migrated (that's
`olab_voice`'s territory).
