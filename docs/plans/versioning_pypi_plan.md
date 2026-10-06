# Versioning and PyPI publishing plan (issue #32)

Status: **agreed plan, not yet implemented** (grill session 2026-10-06).

## Goal

Give every `olab_code` package real version numbers and publish them to
public PyPI, so students install with plain `pip install olab-camera`
(and upgrade with `pip install -U olab-camera`) instead of being told to
grab whatever is on `main`.

This **reverses** the reorg plan's "Deferred: a real package index"
decision (`docs/plans/olab_packages_reorg_plan.md`, install-mechanism
section). The trigger it named — broader-than-lab distribution — now
applies: students are the audience. That doc and `CONTRIBUTING.md` must be
updated to match (see step 6).

## Current state (verified 2026-10-06)

- Six packages, all with real migrated source (not scaffolds), all at
  `version = "0.1.0"`, all `hatchling`, `src/`-layout:
  `olab-utils`, `olab-camera`, `olab-audio`, `olab-voice`, `olab-rf`,
  `olab-playground`.
- All six names (plus `olab`, `olab-code`) are **unclaimed** on PyPI.
- Repo `optimatorlab/olab_code` is public, MIT-licensed.
- `release.yml` already exists: triggered by tags `olab-<pkg>-v<ver>`,
  verifies the tag matches `pyproject.toml`, builds wheel + sdist, attaches
  them to a GitHub Release with `generate_release_notes: true`. **Never
  run** — the repo has no tags.
- `ci.yml` tests every package on Python **3.11 only**.
- `__version__` is already derived from `importlib.metadata` in every
  package **except `olab_playground`**.
- Workspace-internal deps are unbounded names: `olab-camera` →
  `olab-utils`; `olab-camera[av]` → `olab-audio`; `olab-playground` →
  `olab-camera`.
- `olab-camera`'s `ros` extra (`rospy`, `cv-bridge`, `sensor-msgs`) is
  unresolvable from PyPI (`rospy`/`sensor-msgs` don't exist there;
  `cv-bridge` is an unofficial ROS1-era upload), and because `all`
  includes `ros`, `pip install olab-camera[all]` fails today.

## Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Index | Public PyPI. |
| 2 | Version scheme | **Independent per-package** versions (PEP 440 / SemVer-ish), keeping the existing package-namespaced tag design. No lockstep. |
| 3 | Scope | **All six** packages in the first wave. |
| 4 | First version | **`0.1.0` everywhere.** 0.x = API may still change. Breaking change → bump minor (`0.1.0`→`0.2.0`); fix/feature-compatible → bump patch. |
| 5 | Internal deps | **Lower bounds**, e.g. `olab-utils>=0.1.0`; raise the bound only when the dependent starts needing a newer feature. |
| 6 | `ros` extra | **Drop it** from `olab-camera` (and from `all`). Metadata-only change: the guarded import in `camera.py` and `tests/test_ros_absent_import.py` are untouched; README row reworded to "install ROS via apt/rosdep and source your ROS environment". Anyone still requesting `[ros]` gets a harmless pip warning. |
| 7 | Publishing auth | **PyPI trusted publishing** (GitHub OIDC; no API token stored anywhere), bound to `release.yml` and a GitHub Environment `pypi` that **requires manual approval** by the maintainer before upload. |
| 8 | PyPI ownership | Projects owned by the maintainer's **personal PyPI account** (2FA on, recovery codes stored safely). Add a lab co-owner, or transfer to a PyPI org, **before stepping back** from the lab. Day-to-day releases don't depend on the owner — only project admin (yank, publisher config, maintainers) does. |
| 9 | Dry run | **TestPyPI rehearsal once**, end-to-end, before the first real publish. Rehearsal path kept available for future use, but tag pushes always target real PyPI. |
| 10 | `requires-python` | **Keep as-is**: `>=3.10` everywhere except `olab-rf` `>=3.11`. Remove the "TODO(open item #5)" comments — decided. |
| 11 | CI floor | **Add 3.10 to the `ci.yml` matrix** so each package is tested on its declared floor (3.10, or 3.11 for `olab-rf`) and on 3.11. |
| 12 | Cadence | **On demand**: release a package whenever something student-visible lands. Version bump happens in an ordinary PR; releasing is still a separate, deliberate tag. No auto-release on merge (existing rule stands). |
| 13 | Change notes | **Per-package `CHANGELOG.md`**, updated in the version-bump PR. Release body comes from that file's section for the version; drop `generate_release_notes` (it diffs against the previous release of *any* package, producing cross-package noise). |
| 14 | Pre-upload check | **Smoke test** in the release workflow: install the built wheel into a clean venv (internal deps resolved from PyPI), `import` it, assert `__version__` equals the tag version. |
| 15 | Lab consumers | **Migrate to PyPI pins as a follow-up**, not in this plan's PRs. Each consumer keeps its existing pin policy: `ofm` → exact `==X.Y.Z`; `ub_racer`/`classroom`-style `@main` consumers → lower bound. `deploy_vehicle.py` switches likewise. |

## Implementation steps

Each step is a normal PR to `main` (via `murray`) unless noted. Steps 1–5
can land in any order; 7–8 depend on all of them.

### 1. Package metadata cleanup (all six `pyproject.toml`s)

- Internal deps get lower bounds: `olab-utils>=0.1.0` (camera),
  `olab-audio>=0.1.0` (camera `[av]`), `olab-camera>=0.1.0` (playground).
- Drop `olab-camera`'s `ros` extra and remove it from `all`; reword the
  README extras-table row (decision 6). No `.py` changes.
- Remove the `TODO(open item #5)` comments on `requires-python`.
- Add PyPI-facing metadata: `license = "MIT"` (+ `license-files`),
  `authors`, `[project.urls]` (Homepage/Source → repo subdirectory,
  Issues → repo issues, Changelog → the package's `CHANGELOG.md`), and a
  few `classifiers` (Python versions, OS). Package README already serves
  as the PyPI long description — check each renders sensibly there (no
  repo-relative links that break on PyPI; make them absolute GitHub URLs).
- Versions stay `0.1.0`.

### 2. `olab_playground.__version__`

Add the same `importlib.metadata` `__version__` pattern the other five
packages use, so the smoke test (step 4) works uniformly.

### 3. CI: Python 3.10 floor

Extend `ci.yml`'s matrix to run each package on its declared floor and on
3.11 (`olab-rf`: 3.11 only). Fix anything 3.10 breaks before first
release — the published `requires-python` must be true.

### 4. `release.yml` rewrite

Split into jobs with least privilege:

1. **build** (no special permissions): derive pkg/version from tag
   (existing logic), verify `pyproject.toml` version matches (existing),
   verify `packages/<pkg>/CHANGELOG.md` has a section for this version
   (fail if missing), `python -m build`, upload `dist/` as a workflow
   artifact.
2. **smoke-test**: fresh venv on the package's floor Python, install the
   system deps the package needs (reuse `ci.yml`'s PortAudio / camera
   apt steps), `pip install dist/*.whl` (internal deps from PyPI),
   `python -c "import <pkg>; assert <pkg>.__version__ == '<ver>'"`.
3. **publish-pypi**: `environment: pypi` (manual approval), `permissions:
   id-token: write`, `pypa/gh-action-pypi-publish`. No token.
4. **github-release**: `permissions: contents: write`, attach `dist/*`,
   body = the version's `CHANGELOG.md` section.

Plus a `workflow_dispatch` path (inputs: package, git ref) that runs
build → smoke-test → publish to **TestPyPI** (`environment: testpypi`,
`repository-url: https://test.pypi.org/legacy/`) and skips the GitHub
Release. Note: in the TestPyPI smoke test, install with
`--index-url https://test.pypi.org/simple/ --extra-index-url
https://pypi.org/simple/` since numpy/opencv etc. aren't on TestPyPI.

### 5. Per-package `CHANGELOG.md`

Create one in each package with an initial `## [0.1.0] - <date>` entry
("First PyPI release" + one-line summary of what the package provides).
Keep the format minimal (Keep-a-Changelog-style headings).

### 6. Docs reconciliation

- `CONTRIBUTING.md`: replace "Installing a package" (git+subdirectory →
  `pip install olab-<pkg>`; git install kept as the dev/pre-release
  option), rewrite the "Workspace-internal dependencies" policy (now
  resolved by name from PyPI with lower bounds), add the CHANGELOG
  requirement and the release checklist (below), and replace the
  "do not tag a scaffold 0.1.0" warning with "PyPI versions are permanent
  — a version number can never be re-uploaded; yank, don't delete".
- `docs/plans/olab_packages_reorg_plan.md`: mark the "Deferred: package
  index" item as resolved by this plan (link here), same for open item #5.
- Root `README.md`: fix the stale "scaffold only, not yet migrated"
  status column; add `pip install olab-<pkg>` instructions.
- Each package README: install line becomes `pip install olab-<pkg>`
  (with extras examples).

### 7. One-time PyPI/TestPyPI setup (manual, maintainer)

- PyPI and TestPyPI accounts with 2FA; save recovery codes.
- For each of the six names, on **both** TestPyPI and PyPI, register a
  **pending trusted publisher**: owner `optimatorlab`, repo `olab_code`,
  workflow `release.yml`, environment `testpypi` / `pypi` respectively.
  (Pending publishers reserve the name until the first upload creates the
  project.)
- Create GitHub Environments `testpypi` and `pypi` in the repo; `pypi`
  gets the maintainer as required reviewer and is restricted to tags
  matching `olab-*-v*`.

### 8. Rehearsal, then first release

Publish order respects internal deps (a package's smoke test resolves its
olab deps from the index, so they must exist first):

1. `olab-utils`, `olab-audio`, `olab-voice`, `olab-rf` (no internal deps)
2. `olab-camera` (needs utils; `[av]` needs audio)
3. `olab-playground` (needs camera)

- **Rehearsal**: dispatch the TestPyPI path for all six in that order;
  then, in a clean venv on a lab machine, `pip install` each from
  TestPyPI (with PyPI as extra index) and import it. Fix anything found;
  since TestPyPI versions are also permanent, re-rehearse with a
  `0.1.0.devN`-style version if needed (never re-use a number).
- **Real release**: on a `main` commit where `ci.yml` passed, push tags
  `olab-utils-v0.1.0` … `olab-playground-v0.1.0` in the order above,
  approving each `pypi` environment gate. Verify
  `pip install olab-camera` in a fresh venv on a student-like machine.
- Close issue #32.

## Ongoing release checklist (goes into CONTRIBUTING.md)

1. PR: bump `version` in `packages/<pkg>/pyproject.toml` and add the
   matching `CHANGELOG.md` section. If the change needs a newer sibling
   package, raise that lower bound — and release the sibling first.
2. Merge; confirm `ci.yml` is green on that `main` commit.
3. `git tag olab-<pkg>-v<ver> <sha> && git push origin olab-<pkg>-v<ver>`.
4. Approve the `pypi` environment gate once build + smoke-test pass.
5. Broken release? **Yank** it on PyPI and release a new patch version;
   never try to re-upload the same number.

## Follow-ups (out of scope here)

- Migrate lab consumers to PyPI pins per their existing pin policies:
  `ofm` (exact `==`), `ub_racer` / `arbotix_private` / `warehouse_drone` /
  `classroom` (lower bound), `scripts/gcs/deploy_vehicle.py`.
- Add a lab co-owner (or move to a PyPI organization) on all six projects
  before the maintainer steps back.
- Student-facing install note (course materials / lab wiki):
  `python -m venv venv && venv/bin/pip install olab-camera`.
