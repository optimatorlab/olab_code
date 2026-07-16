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
