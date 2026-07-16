# `olab_*` Package Reorganization Plan

Written 2026-07-16 via `/grillme` session; revised 2026-07-17 after a
parallel independent review (by a second agent, working from a
recommendations-free problem writeup) and reconciliation between the two
resulting plans. Originally scoped as a `ub_*`-namespaced plan; renamed to
`olab_*` mid-session once the user decided to rename the whole package
family (see "Naming and collision check" below) and confirmed against the
codebase that this is safe.

Covers how to organize the lab's reusable packages (currently scattered
across `ub_code`, `cuas_practice`, `tts_practice`, `CoG/tts_practice_migration`,
and embedded directly in `ofm`) so they can be cleanly reused across OFM,
other lab projects, and teaching exercises — plus a scope for consolidating
`ub_audio` into `olab_audio`.

## Motivation

The lab has built several reusable packages (`ub_camera`, `ub_utils`,
`ub_rf`, `ub_voice`, and an unextracted `ub_audio`) that are used, or will
be used, across multiple separate projects — not just OFM. They should not
live inside the OFM repo. Today they're inconsistently organized: some
share one repo/one distribution (`ub_camera`+`ub_utils` as `ub_code`), some
live inside broader "practice" repos alongside unrelated content (`ub_rf`
in `cuas_practice`, `ub_voice` in `tts_practice`), and one hasn't been
extracted from OFM at all (`ub_audio`, embedded as
`ofm/ofm/sensor/ub_audio.py`). `ub_voice` has also silently forked into two
diverging copies.

## Current state (as found)

| Package | Current location | Repo/remote | Notes |
|---|---|---|---|
| `ub_camera` + `ub_utils` | `~/Projects/ub_code` | `github.com/optimatorlab/ub_code` | One `pyproject.toml`, one distribution `ub-code` (`ub_camera`: 5,719 lines; `ub_utils`: 991 lines). CI auto-bumps `_version.py` on every push to `main`; PyPI publishing guide drafted but unused. `requires-python = ">=3.7"`. |
| `ub_rf` | `~/Projects/cuas_practice/src/ub_rf` | `github.com/optimatorlab/cuas_practice` | Own package name `ub_rf` (5,979 lines), but repo also holds `data/`, `notebooks/`, `external/`, `docs/`. `requires-python = ">=3.11"` — inconsistent with `ub_code`'s `>=3.7`, unresolved. No audio/pyaudio dependency — ADS-B/AIS/scanner/RemoteID telemetry decoding, not raw audio capture. |
| `ub_voice` | **Two copies, diverged**: `~/Projects/tts_practice/src/ub_voice` and `~/Projects/CoG/tts_practice_migration/src/ub_voice` | `tts_practice` has no git remote configured; `CoG` copy lives inside the `CoG` repo | See "The `ub_voice` fork" below — `CoG`'s copy (2,200 lines) is canonical per user decision. |
| `ub_audio` | `~/Projects/ofm/ofm/sensor/ub_audio.py` | (part of `ofm` repo) | Single 1,994-line file, never extracted as its own package. |

### The `ub_voice` fork

`ub_voice` was copied into `CoG/tts_practice_migration` to develop
streaming-STT features against a real consumer, `CoG/realtime_transcription`.
The two copies have since diverged:

- `CoG/tts_practice_migration/src/ub_voice` has `stt/vosk.py`,
  `stt/faster_whisper_streaming.py`, `stt/hybrid.py`, and differences in
  `stt/__init__.py`/`stt/base.py` — real streaming-STT work `tts_practice`'s
  copy lacks. Last commit `2026-07-13`.
- `tts_practice/src/ub_voice`'s own README still frames itself around a
  "First Vertical Slice" (batch Whisper + Piper, no streaming) — the
  original starting point. Last commit `2026-07-12`.
- `CoG/realtime_transcription/backend/{main.py,streaming_runtime.py}`
  genuinely `import ub_voice.stt`/`ub_voice.audio.models` and drive the
  streaming engines — a real, live consumer of the `CoG` copy specifically.
  `realtime_transcription` treats its current editable install as
  temporary and expects a versioned wheel once `olab_voice` exists.

**Decision: `CoG/tts_practice_migration/src/ub_voice` is canonical.**
`tts_practice`'s copy is superseded — `tts_practice` becomes an importer of
the finished `olab_voice` package once migration lands, not the source of
truth.

`ub_voice`'s own design is deliberately capture/transport-agnostic: its
`audio/` submodule is just dataclasses (`AudioBlob`, `AudioFrame`) and
`Protocol` interfaces (`AudioFrameSource`, `AudioBlobSource`,
`AudioPlaybackSink`) — it does no device I/O itself, by design.
`AudioSource = Literal["browser", "python_mic", "file", "radio", "unknown"]`
already anticipates a `"radio"` source, though no current package (including
`ub_rf`) produces one yet.

### `ub_audio`'s two blended concerns

`ofm/sensor/ub_audio.py` conflates two distinct responsibilities in one file:

1. **Device I/O** — `Mic`, `Speaker`, `Recording`/`Recording_bytes`/
   `Recording_np`, device enumeration (`get_input_devices`,
   `get_output_devices`, `get_connected_devices`), PulseAudio/PipeWire port
   control (`get_default_source_ports`, `set_default_source_port`,
   `reinit_audio`). This is the only slice OFM's `sensor_node.py` actually
   uses. Deps: `pyaudio`, `pulsectl`. `Mic` also has embedded, effectively
   legacy Whisper transcription hooks (`transcribeStart`/`transcribeStop`) —
   see "`olab_audio` v1 scope" below for their disposition.
2. **DSP/signal-processing + teaching/research toolkit** — `Wave`,
   `Spectrogram`, `Spectrum`, tone/chirp/pitch-map synthesis (`createTone`,
   `createChirp`, `createPitch`, `pitch_map`), `trim`/`normalize`/
   `resample`/spectral helpers, `decorate`/`legend` matplotlib plotting.
   Confirmed by the user: actively used in classes and other research, and
   will be used in OFM later once the project's focus shifts from data
   *collection* to *analysis*. Deps: `librosa`, `soundfile`, `matplotlib`.
   OFM's `pyproject.toml` *does* declare `matplotlib>=3.7` (an earlier pass
   over this plan incorrectly called it undeclared) — but under a generic
   `# Data analysis` comment, not attributed to `ub_audio.py` the way the
   other three audio deps are. It's declared, just untraced back to its
   actual consumer — easy to accidentally drop in a future dependency
   cleanup without realizing `ub_audio.py` needs it.

Both slices are wanted long-term, and the user was explicit that they
should migrate **together, in the same pass** — not staged, with the
analysis/teaching slice deferred to a later milestone. Splitting the work
risks the analysis half being rewritten later with different style or
without the design insight available while doing the core extraction fresh.
See "`olab_audio` v1 scope" below.

### Duplicated/uneven PyAudio capture implementations

Three independent PyAudio wrappers exist today, unequally hardened by real
hardware experience:

1. **`ub_audio.Mic`** — most mature: ALSA-aware enumeration, `recordStart`/
   `recordStop`/`recordPause`/`recordResume`, `db()` level meter,
   callback-based capture (np or bytes).
2. **`sensor_node.py`'s wrapper around `Mic`** — adds real-hardware fixes on
   top: filters ALSA pseudo-devices (e.g. `vdownmix`) that **segfault the
   whole process** if opened; auto-detects each device's real default
   sample rate (works around `Mic`'s hardcoded 44100Hz default failing on a
   USB webcam mic that only supports 32000Hz); works around a `Mic.start()`
   wart where a failed open leaves the object half-initialized and later
   crashes on `.stop()`.
3. **`CoG/realtime_transcription/backend/audio_capture.py`'s `AudioCapture`**
   — a much more naive, independent PyAudio wrapper: **no ALSA filtering at
   all** (the same segfault risk #2 already found and fixed, unfixed here),
   hardcoded sample rate (the same problem #2 already found and fixed,
   unfixed here), print-based error handling.

A fourth overlap: `CoG`'s `audio_processing.py` reimplements dB/RMS-level
calculation and waveform downsampling — the same territory as `Mic.db()`
and `ub_audio.py`'s own level utilities (which have their own internal bug:
two separate `def convert_to_db(...)` definitions, the second silently
shadowing the first).

**A crash risk currently live in `realtime_transcription` specifically
motivates fixing this at the package level, not just extracting as-is**:
its `AudioCapture` is the least-hardened of the three and is exposed today
to the segfault #2 already discovered and fixed. Consolidating onto a
single, hardened `olab_audio.Mic` fixes this in the one place it's
currently a real risk — see "Consumer migration candidates" below.

**A testing-methodology consequence of the segfault finding**: the crash is
a C-level PortAudio/ALSA fault that Python's `try`/`except` cannot catch —
a test process cannot safely "prove" it by deliberately opening a
pseudo-device and asserting an exception. The acceptance criterion has to
be structural (the enumeration/filtering logic never offers a pseudo-device
as selectable in the first place), not a test that intentionally attempts
the crash path. This generalizes: hardware-dependent behaviors in
`olab_audio`'s test suite should be explicit, opt-in, and run against real
devices — not something a generic CI runner attempts blindly.

### `ub_camera`'s existing conventions (the template to follow)

`ub_code`'s `pyproject.toml` already establishes two patterns worth
replicating workspace-wide:

- **Extras split**: `dependencies` stays light; heavy/specialized deps are
  opt-in via `[project.optional-dependencies]` (`yolo`, `ros`, `websocket`,
  `webrtc`, plus an `all` convenience bundle). This should be a stated
  house convention for every `olab_*` package, not decided ad hoc per
  package.
- **API shape**: `Camera` (base class) owns nearly everything —
  `startStream`/`stopStream` with three real streaming server
  implementations (`StreamingServer`/MJPEG, `WebSocketStreamingServer`,
  `WebRTCStreamingServer`), CV feature methods (`addAruco`/`addBarcode`/
  etc.), frame access, local recording (`recordVideoLocal`/
  `stopRecordVideoLocal`), decoration, and device config. Subclasses
  (`CameraPi`, `CameraUSB`, etc.) only implement hardware-specific capture.
  OFM's `camera_services.py` stays thin — mostly NATS-command-to-method
  translation — because `Camera` already does the heavy lifting.
  `Mic` has no equivalent of `Camera`'s *network* streaming capability —
  this remains an open scope question for `olab_audio` v1, not yet
  resolved (see below).

### The deployment constraint

`scripts/gcs/deploy_vehicle.py` currently rsyncs a hardcoded local path
(`LOCAL_UB_CODE = Path.home() / "Projects" / "ub_code"`) to the vehicle's
Raspberry Pi over SSH and runs `pip install -e '.[websocket]'` on-device.
The user wants to move away from this manual rsync+editable-install pattern.

### Naming and collision check

The user decided mid-session to rename the whole package family from
`ub_*` to `olab_*`, and to house them in a new repo (`olab_code`) rather
than extending `ub_code` in place — see "Decisions" below for the
rationale on the new-repo choice.

Before committing to `olab_*`, a collision check was run given `olab_*`
was the exact prefix used by these same packages' `soar_rover`-era
predecessors: `olab_camera.py`, `olab_utils.py`, `olab_mastodrone.py`, and
(implicitly) `olab_audio`. Findings:

- No live `import olab_*` statements exist anywhere in `ofm`, `ub_code`,
  `cuas_practice`, or `tts_practice`. All hits in those repos are
  historical references in docs/comments/TODOs (e.g. `docs/WIP/
  camera_migration_plan.md`'s title "Camera Migration Plan: olab_camera →
  ub_camera"; `deferred_items.md`'s still-open `olab_utils.stitchImages`
  TODO; `mastodrone.py`'s "Port of olab_mastodrone.py" docstring) — plus
  one unrelated `olab-{vehicleID}` hostname convention in `config/
  fleet.yaml` that just happens to share the prefix.
  `soar_rover` itself does have real `olab_camera.py`, `olab_audio.py`,
  and `olab_utils.py` modules with real imports.
  `CoG/realtime_transcription/test_audio/olab_audio_file_transcribe.py` is
  a real, self-importing standalone script (not an installable
  `olab_audio` package) — a same-name ambiguity risk only if it ever ends
  up on the same Python path as a real `olab_audio` package.
- **`soar_rover` is being fully retired** — `ofm` is its complete
  replacement, and the user explicitly does not want to preserve legacy
  `soar_rover` functionality or worry about co-installation with it. This
  makes the naming collision moot: the new `olab_*` packages are a
  deliberate breaking replacement of the old `soar_rover`-era names, not
  aliases that need to coexist with them.

## Decisions

### 1. New repository (`olab_code`), not extending `ub_code` in place

`ub_code` is currently one `ub-code` distribution whose only active CI
workflow bumps `ub_camera/_version.py`'s version on every push to `main` —
a release process that cannot supply independent per-package releases as
built today. Reshaping `ub_code` in place would conflate the package
reorganization with a breaking change to that existing release workflow,
destabilizing current `ub_camera` consumers mid-migration. A new repo
(`optimatorlab/olab_code`) avoids that: it gets the desired
package-per-directory structure without touching `ub_code` while the
migration is in flight; `ub_camera`/`ub_utils` move over in a deliberate,
later cutover (see "Migration sequence" below), and `ub_code` is archived
read-only once consumers are pinned to the new packages.

### 2. Full rename, no compatibility shims

Rename source imports, console commands, docs, test fixtures,
package-data lookups, and user-facing configuration names from `ub_*` to
`olab_*` as each package migrates. Do not ship `ub_*` shim modules or
placeholder distributions — they would retain two public APIs and obscure
incomplete migrations. Archive `ub_code`, and the in-tree `ub_voice`/
`ub_rf` copies in `tts_practice`/`cuas_practice`, read-only once all named
consumers are pinned to released `olab_*` versions — not before, and not
without first inventorying whether any consumers exist outside the
projects already investigated in this plan (see "Open items" below).

Distribution names use hyphens, import names use underscores:
`olab-camera` / `olab_camera`, through `olab-audio` / `olab_audio`.

### 3. Workspace-style monorepo, `packages/` + `src/`-layout

Keep one shared repo for all lab packages — the user likes having lab
packages under one umbrella — but structure it so each package is
independently packaged, versioned, and installable, rather than bundled
into a single distribution. The key mechanism: **give each package its own
`pyproject.toml`** in its own subdirectory, using a proper `src/`-layout
(avoids accidental cwd-import shadowing) grouped under a top-level
`packages/` directory to keep repo-root files (`README.md`,
`CONTRIBUTING.md`, `.github/`) uncluttered. `pip` supports installing
directly from a subdirectory of a git repo
(`pip install git+https://...#subdirectory=packages/<pkg>`), so this gives
both a shared home and independent per-package installs (precedent:
`opentelemetry-python` publishes 30+ independent PyPI packages from one
repo tree this way). There is no umbrella runtime distribution, and no
package depends on every other package.

```
olab_code/                              (repo: github.com/optimatorlab/olab_code)
  README.md            (short catalogue; no installable umbrella package)
  CONTRIBUTING.md       (release and local-development rules)
  packages/
    olab_camera/
      pyproject.toml   → distribution "olab-camera"
      src/olab_camera/
      tests/
      README.md
    olab_utils/
      pyproject.toml   → distribution "olab-utils"
      src/olab_utils/
      tests/
      README.md
    olab_rf/
      pyproject.toml   → distribution "olab-rf"      (migrated from cuas_practice)
      src/olab_rf/
      tests/
      README.md
    olab_voice/
      pyproject.toml   → distribution "olab-voice"   (migrated from CoG/tts_practice_migration)
      src/olab_voice/
      tests/
      README.md
    olab_audio/
      pyproject.toml   → distribution "olab-audio"   (extracted from ofm/ofm/sensor/ub_audio.py)
      src/olab_audio/
      tests/
      README.md
  .github/workflows/
    ci.yml              (path-aware test/build matrix — every push/PR, only changed packages)
    release.yml          (tag-triggered only — exactly one package per release)
```

`olab_camera` and `olab_utils` are split into separate packages (not kept
bundled) — the user confirmed they update at different rates — but are
migrated together operationally in one step (see "Migration sequence"),
released as two independent distributions going forward.

Plain `pip` only — no `uv` workspace tooling, per explicit user preference.

### 4. Docs/examples: self-contained per package

Each package subdirectory owns its own `README.md`/`docs/`/`examples/`/
`tests/` — a self-contained vertical slice, not flat files at the repo
root (today `ub_camera_developer_guide.md`, `spec_CameraPi2.md` sit at
`ub_code`'s root rather than inside `ub_camera/`; move them in as part of
the restructuring). The repo-root `README.md` is a short catalogue/index
only — it must not become a second full manual.

### 5. Dependency extras convention

Every package's `pyproject.toml` should split `dependencies` (core,
lightweight) from `[project.optional-dependencies]` (heavy/specialized),
following `ub_camera`'s already-proven pattern, stated here as an explicit
house rule rather than a per-package ad hoc choice. This is the
finer-grained lever (below the per-package level) for "a student doesn't
need everything installed."

### 6. Install mechanism

- **Now**: `pip install "git+https://github.com/optimatorlab/olab_code.git@<tag>#subdirectory=packages/<pkg>"`
  — replaces the rsync+`pip install -e` pattern immediately, without
  waiting for the release-automation CI (below) to exist. No PyPI.
  Versioned by git tag or commit SHA. Requires the install target to have
  network access to GitHub.
- **Soon**: once the per-package release CI exists (see "Versioning/release
  automation" below), install the wheel attached to a package's specific
  GitHub Release instead — pin the **exact release URL and SHA-256 hash**
  in the consuming project's requirements/deployment configuration. No
  package index, no checkout on the target, no editable install.
- **Deferred**: a real package index (public PyPI or self-hosted
  `pypiserver`/`devpi`). Not needed now — genuine infra investment with no
  current concrete need; revisit only if broader-than-lab distribution
  becomes an actual goal.
- `scripts/gcs/deploy_vehicle.py` gets updated to use the "now" mechanism
  as soon as `olab_camera` exists in the new repo, then switches to the
  "soon" mechanism (with a `pip check` + import verification step) once
  release CI is built — it does not need to wait for the full CI/release
  pipeline before getting *any* relief from the current rsync pattern.

  **Resolved 2026-07-16**: staged, per the "Now"/"Soon" split above — the
  independent parallel review's alternative (skip straight to
  wheel-from-Release, gating any deployment relief behind the full
  CI/release pipeline) was considered and rejected. Earlier relief from the
  current rsync pain is worth briefly maintaining two deployment
  mechanisms.

### 7. CI/release workflow split

Two distinct GitHub Actions workflows, not one:

- `ci.yml` — runs on every push/PR, path-aware (only tests/builds packages
  whose files actually changed). This is the ordinary safety net and is
  independent of releasing anything.
- `release.yml` — tag-triggered only (see below). Never runs as a side
  effect of an ordinary merge to `main`.

### 8. Versioning/release automation — manual bump, explicit tag, no auto-release

Rejected: fully automatic semantic versioning (conventional-commit
parsing) — genuinely complex to maintain for uncertain benefit at this
scale.

Also rejected (an earlier version of this plan's own original proposal):
CI automatically creating a release the moment a version-field change lands
on `main`. That collapses "this code merged" and "this is now an immutable
public release" into the same event — a version bump landing on `main` for
any reason (a rebase, premature merge, exploratory testing) would
immediately and irreversibly become a real release with no second chance
to catch it.

**Adopted**: the developer manually bumps a package's own version number
(PEP 440 semantic versioning, starting at `0.1.0` for each new/renamed
distribution) in its `pyproject.toml` as part of a normal PR — same
discipline as today, just scoped per-package. That merge, by itself, does
**not** trigger a release. Releasing is a separate, deliberate act: a
maintainer creates an immutable, package-namespaced git tag (e.g.
`olab-voice-v0.1.0` — not a bare `v0.1.0`, which would collide across
packages in one repo) only after `ci.yml` has already passed on that
commit. The tag push triggers `release.yml`, which re-verifies the
package's metadata matches the tag, rebuilds it, and attaches the wheel
and source distribution to a GitHub Release with release notes covering
any breaking changes/upgrade actions. Tags/releases are never retargeted.

No commit-message parsing, no auto-computed version numbers — the human
decision ("this is worth a release") is the explicit tag-creation act,
deliberately kept separate from the version-bump PR merge itself.

### 9. `olab_voice` test strategy

Model-free contract tests (message/event schemas, transport adapters, etc.)
run unconditionally in CI. Tests that require an actual STT/TTS model or
real hardware are explicitly opt-in, with documented local model paths —
they should not be something a generic CI runner attempts blindly, the
same principle as the hardware-testing methodology described for
`olab_audio` above.

### 10. Physical migration, and what happens to the source repos

- `cuas_practice` and `tts_practice` **continue to exist** as
  experimentation/research repos — they just become consumers of the
  extracted `olab_rf`/`olab_voice` packages (installed the same way any
  other project would) rather than owning the package source directly.
- `olab_rf`: migrate `cuas_practice/src/ub_rf` → `olab_code/packages/olab_rf/`.
  Keep CUAS notebooks, data, tools, and project documentation in
  `cuas_practice`, outside the package.
- `olab_voice`: migrate `CoG/tts_practice_migration/src/ub_voice`
  (canonical) → `olab_code/packages/olab_voice/`. `tts_practice/src/ub_voice`
  is retired/archived, not migrated. `tts_practice` is updated to
  install/import `olab_voice` and its in-tree competing source removed, so
  it remains a standalone consumer. `CoG/realtime_transcription` is
  updated to consume the released wheel and rename its imports/config.
- `olab_audio`: extract `ofm/ofm/sensor/ub_audio.py` →
  `olab_code/packages/olab_audio/`. OFM becomes a consumer, importing it
  externally like the others.

## `olab_audio` v1 scope

Unlike an earlier draft of this plan, this is **not** staged as a minimal
core-only first release with the analysis/teaching content deferred to a
later milestone. The user was explicit: bring the full current capability
into the new system together, in one migration pass, specifically because
splitting the work risks the analysis/teaching half being rewritten later
with different style or without the design insight available while doing
the core extraction fresh. It is one `olab-audio` distribution — a lean
I/O base plus an `analysis` extra — not two separate distributions.

**Extras split:**

| Extra | Contents | Deps |
|---|---|---|
| *(core, default)* | `Mic`, `Speaker`, `Recording`/`Recording_bytes`/`Recording_np`, device enumeration + PulseAudio port control | `pyaudio`, `pulsectl` |
| `analysis` | `Wave`, `Spectrogram`, `Spectrum`, tone/chirp/pitch synthesis, `trim`/`normalize`/`resample`, `decorate`/`legend` plotting | `librosa`, `soundfile`, `matplotlib` |

**Explicit exclusion**: `Mic`'s embedded Whisper transcription hooks
(`transcribeStart`/`transcribeStop`) are **removed**, not migrated —
transcription conceptually belongs to `olab_voice` now, and perpetuating a
second, `olab_audio`-embedded transcription path would contradict the
`olab_voice`/`olab_audio` ownership split below.

**Confirmed dependency direction**: `olab_voice` depends on `olab_audio`
(optionally, via an adapter), not the reverse. `olab_voice`'s own `audio/`
submodule stays capture-agnostic (`Protocol`-based); an `olab_audio`-backed
`AudioFrameSource` adapter is a natural implementation of that Protocol —
likely living in `olab_voice`'s integration layer (mirroring how
`camera_services.py`'s NATS-specific glue stays in OFM rather than inside
`ub_camera` itself), not inside `olab_audio`. Neither package's base
install depends on the other; a thin optional adapter bridges them once
both APIs are stable, and it must not force speech-model-only consumers to
install PyAudio.

**Known real bugs/gaps to fix as part of this migration** (not just a
repo-move — see "Duplicated/uneven PyAudio capture implementations" above
for full detail):

1. Push the ALSA pseudo-device safety filter into device enumeration
   itself, so every consumer (including `realtime_transcription`, currently
   exposed to a real segfault risk) gets it for free. Do not offer
   pseudo-devices as selectable inputs at all — see the testing-methodology
   note above for why this can't be a "catch the crash" test.
2. Push dynamic default-samplerate detection into `Mic`'s device-open path
   itself — query the selected device's actual supported rate rather than
   assuming 44.1kHz universally.
3. Fix `Mic.start()`'s failure-handling API wart (raise cleanly, or
   guarantee `.stop()`/idempotent-stop is always safe regardless of open
   state) — no more callback-only error reporting or half-open `Mic`
   objects, and no recreating a bare global `PyAudio()` instance at import
   time the way `ub_audio.py` currently does.
4. Consolidate the duplicated dB/level/waveform-analysis logic
   (`CoG/audio_processing.py` vs. `Mic.db()` vs. `ub_audio.py`'s own
   internally-duplicated `convert_to_db()`); fix the internal duplicate
   definition regardless.
5. **Resolved 2026-07-16**: no. `olab_audio` v1 stays local capture/record
   only — no `Camera`-style network-facing streaming capability
   (`startStream`/`stopStream` + `StreamingServer`/`WebSocketStreamingServer`/
   `WebRTCStreamingServer`-equivalents). `realtime_transcription` and any
   future `olab_voice` "python_mic" adapter consume `Mic` in-process
   (same machine/process), not over the network, for now. Network streaming
   is deferred to a v2 item, revisited only if a real cross-machine
   consumer materializes.

**Consumer migration candidates once `olab_audio` v1 lands** (explicit —
this list is not yet present in the independent parallel review's plan and
should be reconciled with it):
- OFM's `sensor_node.py` — drop its local ALSA-filter/samplerate-detection
  workarounds once those live in `olab_audio` itself.
- `CoG/realtime_transcription`'s `AudioCapture`/`audio_processing.py` —
  replace with `olab_audio.Mic` + a thin `olab_voice`-side
  `AudioFrameSource` adapter, eliminating the least-hardened of the three
  current implementations — the one actually exposed to the segfault risk
  today.

## Migration sequence

1. Create `olab_code` with the `packages/` + `src/`-layout, root catalogue,
   `CONTRIBUTING.md`, build tooling, and the `ci.yml`/`release.yml` split.
   Prove each package can build and install in an isolated environment; no
   consumer switches yet.
2. Move `ub_voice` from the `CoG` migration copy (canonical, not
   `tts_practice`'s) into `olab_voice`; apply the rename, retain its
   optional STT/TTS/NATS/web extras, run its existing unit suite with the
   model-free/opt-in-hardware test split. Update `tts_practice` and
   `realtime_transcription` to consume it as an external package.
3. Move `ub_rf` into `olab_rf` with its catalog/static package data and
   tests; rename internal imports/commands/docs; update `cuas_practice` to
   consume it externally.
4. Move `ub_camera` and `ub_utils` together operationally, convert both to
   `src`-layout, but release them as two independent distributions. Rename
   all imports in OFM, and replace `deploy_vehicle.py`'s rsync path with
   the "now" install mechanism (see above), upgrading to pinned
   release-wheel installs once release CI exists.
5. Run the dedicated `olab_audio` migration — the full I/O core +
   `analysis` extra, together, per "`olab_audio` v1 scope" above. Resolve
   the open network-streaming-capability question first. Add API-parity
   tests for the analysis utilities plus lifecycle/PCM/WAV unit tests, then
   run the hardware acceptance matrix (Pi/USB/webcam/PulseAudio devices,
   32kHz webcam selection, failed-open cleanup, stop idempotence, no
   pseudo-device opening). Migrate OFM's `sensor_node.py` and
   `realtime_transcription`'s `AudioCapture` only after this acceptance
   gate; remove the old OFM module and its now-unneeded direct
   dependencies only after both cutovers.
6. Archive `ub_code` and the in-tree `ub_voice`/`ub_rf` copies read-only
   once all named consumers are pinned to released `olab_*` versions — not
   before completing the consumer inventory in "Open items" below.

## Verification and release checklist

- Test each package in a fresh virtual environment with only its declared
  base dependencies, then each supported extra combination actually used
  in production.
- Build both wheel and source distribution; install the wheel in a second
  fresh environment; assert package import, console entry points, package
  data, and `pip check`.
- For `olab_voice`: model-free contract tests run unconditionally;
  model/hardware tests are explicitly opt-in with documented local model
  paths.
- For `olab_camera`/`olab_audio`: run the existing target hardware smoke
  tests before changing a production pin. For audio specifically: device
  enumeration, 32kHz webcam selection, failed-open cleanup, stop
  idempotence, and confirmation that no pseudo-device is ever offered as
  selectable (not "opened and caught").
- In each consumer, search for `ub_` and replace intentional active
  references; retain old-name mentions only in clearly labelled
  migration/archive docs.
- Test OFM deployment on a clean target with no `~/Projects/ub_code` and
  confirm it installs only the declared release artifact and required
  extras.

## Open items / execution checklist

Items 1-3 below were the plan's three explicitly unresolved points; all
three were resolved 2026-07-16 (see "Install mechanism" and `olab_audio`
v1 scope item 5 above for the first two; item 3 below for the third).

1. ~~Reconcile the staged-install-mechanism disagreement~~ — **Resolved**:
   staged (git+subdirectory relief now, wheel-from-Release once release CI
   exists). See "Install mechanism" above.
2. ~~Resolve whether `olab_audio` v1 needs a `Camera`-style network
   streaming capability~~ — **Resolved**: no, deferred to v2. See
   `olab_audio` v1 scope item 5 above.
3. **Resolved**: yes, `realtime_transcription`'s `AudioCapture`/
   `audio_processing.py` migration is in scope. After `olab_audio` v1 lands
   and passes its hardware acceptance gate, replace `AudioCapture` with
   `olab_audio.Mic` + a thin `olab_voice`-side `AudioFrameSource` adapter —
   this closes the live segfault risk that motivated part of the
   consolidation case (see "Consumer migration candidates" above).
4. Decide repository visibility and, if private, configure target-machine
   credentials for GitHub Release downloads without embedding a personal
   token.
5. Reconcile the Python-version-support policy per package — `ub_rf`
   already requires `>=3.11`; don't silently lower it to match `ub_camera`'s
   older `>=3.7` claim without a decision.
6. Confirm whether OFM's deployment should pin release URLs directly, or a
   deployment-owned constraints/requirements file should hold the
   URLs+hashes (the latter is usually cleaner for deployment-only pins).
7. Inventory any consumers outside the projects already investigated in
   this plan before archiving `ub_code`, `tts_practice`'s in-tree package,
   or `cuas_practice`'s in-tree package.
8. Define `olab_audio`'s first concrete API signatures and the supported
   OS/audio-server matrix before extraction begins — the scope above is a
   boundary, not a frozen API.
