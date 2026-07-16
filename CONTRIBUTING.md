# Contributing to olab_code

`olab_code` is a workspace-style monorepo: each package under `packages/`
is its own independently packaged, versioned, and installable distribution
with its own `pyproject.toml`, `src/<package>/` layout, `tests/`, and
`README.md`. There is no umbrella runtime distribution, and no package
depends on every other package. Plain `pip` only — no `uv` workspace
tooling.

Full design rationale lives in
[`docs/plans/olab_packages_reorg_plan.md`](docs/plans/olab_packages_reorg_plan.md).
Read it before making structural changes to the workspace.

## House conventions

- **`src/`-layout**: source under `packages/<pkg>/src/<pkg>/`, never flat
  at the package root — avoids accidental cwd-import shadowing.
- **Extras split**: keep `[project].dependencies` light; put
  heavy/specialized dependencies behind
  `[project.optional-dependencies]`, plus an `all` convenience bundle.
  Follow `olab_camera`'s pattern (`yolo`, `ros`, `websocket`, `webrtc`,
  `all`).
- **Self-contained docs**: each package owns its own `README.md`/`docs/`/
  `examples/`/`tests/`. The repo-root `README.md` is a short catalogue
  only — it must not become a second full manual.
- **Naming**: distribution names use hyphens (`olab-camera`), import names
  use underscores (`olab_camera`).
- **No compatibility shims**: rename fully (imports, console commands,
  docs, fixtures, config) as each package migrates. Do not ship `ub_*`
  shim modules or placeholder distributions.

## Versioning and releases

- Bump a package's own version (PEP 440, starting at `0.1.0` for each
  new/renamed distribution) in its `pyproject.toml` as part of a normal
  PR. This does **not** trigger a release by itself.
- Releasing is a separate, deliberate act: after `ci.yml` has passed on a
  commit, a maintainer creates an immutable, package-namespaced git tag —
  e.g. `olab-voice-v0.1.0` (never a bare `v0.1.0`, which would collide
  across packages in this repo). The tag push triggers `release.yml`,
  which rebuilds the package and attaches the wheel + sdist to a GitHub
  Release.
- No commit-message parsing, no auto-computed version numbers, no
  CI-triggered auto-release on merge to `main`.

**Do not tag/release any package still at its scaffold `0.1.0`** (no
migrated source yet — see each package's `README.md` for status). The
release workflow does not check for this; tagging a scaffold package would
publish an empty distribution.

## Installing a package

Until per-package release CI produces tagged GitHub Releases, install
directly from a subdirectory of this repo:

```
pip install "git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/<pkg>"
```

Once a package has a tagged release, prefer pinning the release wheel's
exact URL and SHA-256 hash instead.

## Testing

- Test each package in a fresh virtual environment with only its declared
  base dependencies, then each supported extra combination actually used
  in production.
- Hardware- or model-dependent tests (e.g. `olab_audio` device I/O,
  `olab_voice` STT/TTS models) must be explicit and opt-in with documented
  local paths/devices — never something a generic CI runner attempts
  blindly. See the plan doc's testing-methodology notes for why (some
  failure modes, like ALSA pseudo-device opens, are C-level crashes that
  `try`/`except` cannot catch or safely provoke in a test).

## Pre-commit checklist

Every commit in this repo goes through an outside reviewer before it
lands — use this checklist to catch what the reviewer would catch, before
proposing the commit, so review rounds spend time on real judgment calls
instead of avoidable misses:

1. **Build and install each touched package fresh.** For every package
   whose `pyproject.toml` or source changed: `python -m build` the wheel,
   `pip install` it into a clean virtualenv (base dependencies only —
   don't default to `[all]`), and run its `tests/`. This alone catches
   most packaging mistakes (missing deps, broken `src/`-layout mapping,
   version drift, uncollectible test dirs).
2. **Re-read CI/release YAML against this document's stated rules.**
   Specifically: does `ci.yml` actually install base-only dependencies
   where a package's own README/pyproject says something is core vs. an
   opt-in extra (e.g. `olab_audio`'s `pyaudio` is core, not an extra —
   CI needs PortAudio headers for it, not a skip)? Does anything install
   `[all]` universally? Do inline comments describe what the workflow
   actually does, not a stale or aspirational version of it?
3. **Check paths and claims against the real filesystem, not the plan
   doc's assumptions.** If a README/pyproject TODO cites a source path
   (e.g. "migrate from `~/Projects/ub_code/...`"), verify that path
   actually exists and has the layout claimed (`src/`-layout or not)
   before writing it down.
4. **No duplicated sources of truth.** Things like a version number
   should live in exactly one place (`pyproject.toml`); derive the rest
   (e.g. `__version__`) from installed package metadata rather than
   hand-copying a value that can drift.
5. **Clean the working tree of build/test artifacts** (`__pycache__/`,
   `*.egg-info/`, `dist/`) before staging — check `.gitignore` covers
   them so they don't need to be caught by hand every time.
6. **Draft the commit message, then stop.** Per the review workflow, do
   not run `git commit` — present the message and diff and wait for the
   reviewer and user to sign off. If pushing back on reviewer feedback,
   give a detailed rebuttal (the reasoning, not just a restated
   conclusion), not silent compliance or silent disagreement.
