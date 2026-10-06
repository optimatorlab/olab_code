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
  Follow `olab_camera`'s pattern (`yolo`, `websocket`, `webrtc`, `all`).
  An extra must actually be installable via plain `pip` from PyPI — if a
  dependency isn't published there (e.g. `olab_camera`'s old `ros` extra,
  dropped per `docs/plans/versioning_pypi_plan.md` decision 6), it isn't
  an extra; document the real install path (system package manager,
  vendor instructions) in the package's own README instead.
- **Self-contained docs**: each package owns its own `README.md`/`docs/`/
  `examples/`/`tests/`. The repo-root `README.md` is a short catalogue
  only — it must not become a second full manual.
- **Naming**: distribution names use hyphens (`olab-camera`), import names
  use underscores (`olab_camera`).
- **No compatibility shims**: rename fully (imports, console commands,
  docs, fixtures, config) as each package migrates. Do not ship `ub_*`
  shim modules or placeholder distributions.

## Versioning and releases

Packages are published to public PyPI — see
[`docs/plans/versioning_pypi_plan.md`](docs/plans/versioning_pypi_plan.md)
for the full design (this reverses the reorg plan's earlier "no package
index" decision; broader-than-lab distribution, students installing with
plain `pip`, is now the actual goal).

- Bump a package's own version (PEP 440, starting at `0.1.0` for each
  new/renamed distribution) in its `pyproject.toml` as part of a normal
  PR, **and add a matching section to that package's own
  `CHANGELOG.md`** (Keep-a-Changelog-style — see any package's
  `CHANGELOG.md` for the format). This does **not** trigger a release by
  itself. If the change needs a newer sibling package, raise that
  sibling's lower bound in `dependencies`/`optional-dependencies` — and
  release the sibling first.
- Releasing is a separate, deliberate act: after `ci.yml` has passed on a
  commit, a maintainer creates an immutable, package-namespaced git tag —
  e.g. `olab-voice-v0.1.0` (never a bare `v0.1.0`, which would collide
  across packages in this repo). The tag push triggers `release.yml`,
  which builds the package, smoke-tests the wheel in a clean venv, then
  **publishes to PyPI behind a manual-approval `pypi` environment
  gate**, and only then creates a GitHub Release (wheel + sdist attached,
  release notes taken from the CHANGELOG section for that version).
- No commit-message parsing, no auto-computed version numbers, no
  CI-triggered auto-release on merge to `main`.

**PyPI versions are permanent** — once a version number is uploaded to
PyPI, it can never be re-uploaded (even after deletion), even if the
release turns out to be broken. If a release is bad, **yank it** on PyPI
(which keeps it resolvable for anyone already pinned to it, but hides it
from new installs) and ship a new patch version — don't delete and reuse
the number; PyPI permits deleting a release, but the freed-up filename
still can't be re-uploaded. `release.yml`'s CHANGELOG-section check still fails a tag push
that forgot to bump the version or add a changelog section, but nothing
stops a working-but-unwanted release once it's live; yanking is the
recovery path, not prevention.

### Ongoing release checklist

1. PR: bump `version` in `packages/<pkg>/pyproject.toml` and add the
   matching `CHANGELOG.md` section.
2. Merge; confirm `ci.yml` is green on that `main` commit.
3. `git tag olab-<pkg>-v<ver> <sha> && git push origin olab-<pkg>-v<ver>`.
4. Approve the `pypi` environment gate once build + smoke-test pass.
5. Broken release? **Yank** it on PyPI and release a new patch version;
   never try to re-upload the same number.

`release.yml` also has a `workflow_dispatch` path (inputs: package, git
ref) for rehearsing a release against TestPyPI instead of real PyPI,
skipping the GitHub Release step — useful before a package's first real
publish. A TestPyPI rehearsal at a non-`0.1.0` version (e.g. a
`0.1.0.devN`-style version, since TestPyPI versions are just as permanent
as PyPI's — never reuse one either) needs its own matching `##
[0.1.0.devN]` `CHANGELOG.md` section on the dispatched ref, same as a real
tag push.

## Installing a package

```
pip install olab-<pkg>
```

For a pre-release/dev checkout instead, install directly from a
subdirectory of this repo:

```
pip install "git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/<pkg>"
```

### Workspace-internal dependencies (e.g. `olab_camera` → `olab_utils`)

Workspace-internal dependencies (e.g. `olab_camera` depending on
`olab_utils`) are declared as ordinary PyPI dependencies with a **lower
bound**, e.g. `olab-utils>=0.1.0` — once both packages are published,
`pip install olab-camera` resolves `olab-utils` from PyPI by name on its
own, the same as any other dependency. Raise the lower bound only when
the dependent package starts needing a newer sibling feature, and release
the sibling first (see the release checklist above).

**CI implication**: a package's path filter in `ci.yml` must also include
its workspace-internal dependencies' paths — see the `olab_camera` filter
entry, which also watches `packages/olab_utils/**`, and the
`olab_playground` entry, which watches both `packages/olab_camera/**` and
`packages/olab_utils/**` — so a dependency-only change still exercises
the dependent package's install/test/build job.

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
