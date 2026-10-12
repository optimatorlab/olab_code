# Changelog

All notable changes to `olab-rf` are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-10-12

Voice capture rework, IQ record/replay, and priority scanning (PR #61),
validated against live RF on 2026-10-08 (RTL-SDR, FRS channel 7, NOAA weather).

**Breaking (0.x):**

- `start_voice_segments()` and `iter_voice_segments()` no longer accept
  `rtl_fm_squelch_db`, and `RtlFmAudioBackend` no longer accepts `squelch_db`.
  Passing either now raises `TypeError`. `rtl_fm`'s own squelch was deleting
  the hiss that the carrier gate measures, so squelch is now the gate's job
  (`threshold_db`, live-tunable). `rtl_fm_command()` keeps `squelch_db` for
  multi-frequency scanning, which needs `-l`.
- The default carrier detector is now `hf_ratio` (a band-energy ratio). 0.1.0
  had only the level-based gate. Pass `detector_mode="rms_quieting"` for the
  old behavior, or `"hybrid"` to accept either detector.
- Emitted voice audio is conditioned by default: DC blocking on and 75 us
  de-emphasis (`dc_block=True`, `deemphasis_us=75.0`). The carrier detector
  still reads raw audio, so conditioning cannot move a detector threshold. Pass
  `dc_block=False, deemphasis_us=None` for unconditioned PCM.
- `decoders.<name>.args` in the YAML config is now validated when the config
  loads. In 0.1.0 it was parsed and never used. It is now an allowlist that
  fails closed: a flag that could change the stdout contract, the sample rate,
  demodulator-side conditioning, or squelch raises `ValueError`.

**Added:**

- Three selectable carrier detectors (`detector_mode`: `rms_quieting`,
  `hf_ratio`, `hybrid`) and live settings via
  `update_voice_segment_settings()` (thresholds, detector, DC blocking,
  de-emphasis, normalization), which survive a receiver respawn
  (`restart_voice_capture()`).
- An optional audio spectrum on voice status (`audio_spectrum_bins`), raw and
  conditioned, off by default.
- IQ recording to SigMF (`start_recording` with `kind="iq"`, via `rtl_sdr`) and
  replay (`start_iq_replay_scan()`, the `iq_replay` scan backend,
  `read_sigmf_iq`).
- Multi-channel priority scanning over catalog channels
  (`start_priority_scan()`, `priority_scan()`), tagging each segment with its
  source channel. v1 is round-robin without preemption.
- `fir_size` and `atan_math` as typed decoder settings, and a
  `stop_active_recording` argument on `SessionManager.stop()`.

**Fixed:**

- A noise-floor runaway that latched the voice gate open on real hiss: the
  floor now updates only from hiss-like frames, recovers while the gate is
  open, and drifts at a bounded rate.
- `rms_quieting` missed real transmissions from a carrier a few kHz off tune:
  FM demodulation turns the offset into DC, which counted as audio level and
  hid the drop in hiss. Level is now measured about the frame mean (found on
  live RF).
- `-M wbfm` with a sample rate below 32 kHz built an `rtl_fm` command that
  aborted with SIGFPE.

**Known limitations:**

- A priority-scan lock ends when its segment closes, so a message with a pause
  of `hang_time_ms` (default 600 ms) or longer is split (#77).
- IQ replay only searches within `channel_width_hz` of the recording's center
  frequency; signals further off-center are ignored.
- AM channels are not validated, and AM broadcast (below the RTL-SDR's tuning
  range) is unsupported (#76).
- The optional `pyrtlsdr` extra does not import against Ubuntu's `librtlsdr`
  2.0.1 (#78). Nothing else in `olab_rf` uses it.

## [0.1.0] - 2026-10-06

First PyPI release. Local-first RF telemetry package for ADS-B, AIS,
scanner, and future Remote ID workflows.
