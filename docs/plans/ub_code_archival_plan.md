# `ub_code` repo archival plan

Step 2 of the post-migration rollout (see `olab_packages_reorg_plan.md`'s
"Decisions" §2 and "Open items" #7 — archival was always explicitly gated on
completing a full consumer inventory first, not just the three projects
already investigated in that plan). Sequenced after step 1, hardware-testing
`CoG/realtime_transcription`'s `olab_audio` migration, per the user's
explicit ordering (2026-07-17): don't archive `ub_code` until the last known
consumer relying on similar patterns is confirmed safe.

## Status: NOT ready to archive yet

**Methodology note, learned the hard way**: an initial GitHub code search
across the `optimatorlab` org (`gh search code "ub_camera"/"ub_utils"
--owner optimatorlab`) found two consumers, but **missed a third
(`ub_racer`) that lives in the very same org** — `gh search code`'s index is
not reliable enough to trust on its own for this inventory. The corrected
method: enumerate every local checkout under `~/Projects` (`find ~/Projects
-maxdepth 3 -name .git`) and grep each directly. That's still not
exhaustive — it only covers repos someone has actually cloned onto this
machine, not every repo that might exist anywhere — but it's materially more
reliable than the GitHub search API turned out to be, and is what actually
surfaced the full picture below.

Combining both methods found **four previously-unknown, real, active
consumers** that were never part of this reorg effort and have not been
migrated:

| Repo | Org / visibility | Last pushed | Evidence |
|---|---|---|---|
| `arbotix_private` | `optimatorlab`, private | 2026-03-12 | `client/client/scripts/client.py:10`: `import ub_camera, ub_utils   # Visit https://github.com/optimatorlab/ub_code`, uses `ub_camera.CameraUSB`/`checkVersion`, `ub_utils.findOpenPort`. `client/client/html/server_secure.py:30` also `import ub_utils`, uses `findOpenPort`. **Also appears to carry its own separately-modified copy** of camera code (`host/host/scripts/ub_camera.py`, per `spec.md`/`tmp.md` — custom `set_allowlist`/`add_to_allowlist`/IP-allowlist methods not present in `olab_camera`) — this may be a second, undocumented fork situation like the `ub_voice` one this whole reorg effort already had to reconcile once. Needs its own investigation before migrating, not a blind import rename. |
| `warehouse_drone` | `optimatorlab`, private | 2026-05-13 | `Research Upload/aruco_localize_v2.py:6`: `import ub_camera`, uses `ub_camera.CameraPi2` (two instances). Found via `gh search code`, not checked out locally — worth confirming it's not stale/abandoned before investing migration effort, since it wasn't recently touched on this machine. |
| `ub_racer` | `optimatorlab`, (visibility unconfirmed) | not yet checked | **Missed entirely by the GitHub code search** — found only via the local checkout at `~/Projects/ub_racer`. Extensive, real usage across 4 files: `car/main.py`, `car/python/car_main.py` (`ub_camera.CameraPi2`), `client/python/controller.py` (`import ub_camera, ub_utils`, `CameraUSB`, `checkVersion`, `findOpenPort`, multiple `ub_utils.SEVERITY_*` constants), `client/python/lib/gz_sim.py`. This looks like the heaviest real dependency of the four. |
| `IE-482-582/spring2026` | **different org entirely** (`IE-482-582`, not `optimatorlab`) | not yet checked | The classroom teaching repo — a different GitHub org my org-scoped search could never have found regardless of its reliability. 4 files across multiple student-facing project subdirectories (`Projects/gazebo_demo/`, `Projects/socket_demo/`, `Projects/ub_racer/` — a course template), plus `docker/Dockerfile` (a course-wide Docker image build installs `ub_camera`/`ub_utils`) and two committed venvs (`venvs/cam`, `venvs/cambk`) with it installed. Given this session's earlier classroom-CA discussion (`olab_code#6`), this is almost certainly *the* classroom repo referenced there — worth connecting those two threads when this is picked up. `IE-482-582/spring2025` also has one match but is explicitly **out of scope by user instruction** (2026-07-18) — don't investigate it. |

None of these are long-dead, safe-to-ignore projects — three were pushed to
within the last several months, and the fourth is current/recent classroom
material. **Archiving `ub_code` today would leave all four consumers only
able to keep using a now-frozen, no-longer-fixable dependency** (see "What
archiving actually does" below — it's not an immediate breakage, but it is
a one-way door on ever fixing/updating `ub_camera`/`ub_utils` again through
that channel).

This is exactly the risk the reorg plan's open item #7 anticipated
("Inventory any consumers outside the projects already investigated in this
plan before archiving `ub_code`") — the plan's original investigation only
covered `ofm`, `tts_practice`, and `cuas_practice`; all four of these were
outside that scope entirely and were never on anyone's radar until this
search.

## Consumer inventory (full picture)

| Consumer | Status |
|---|---|
| `ofm` | ✅ Migrated to `olab_camera`/`olab_utils`/`olab_audio` (PR #38). Two remaining string matches for "ub_camera" are confirmed false positives — historical comments referencing the migration by name, not live code (`ofm/sensor/sensor_node.py:91`, `examples/camera_offline_consumer.py:96`). |
| `cuas_practice` | ✅ Migrated to `olab_rf` (PR #2) |
| `tts_practice` | ✅ Migrated to `olab_voice` |
| `CoG/realtime_transcription` | ✅ Migrated to `olab_voice` + `olab_audio` — code committed/pushed, **hardware validation in progress (step 1)** |
| `arbotix_private` | ❌ **Not migrated. Not previously known about.** Possible local camera-code fork, needs investigation. |
| `warehouse_drone` | ❌ **Not migrated. Not previously known about.** Not checked out locally — confirm still active before investing effort. |
| `ub_racer` | ❌ **Not migrated. Not previously known about. Missed by GitHub code search entirely.** Heaviest usage of the four. |
| `IE-482-582/spring2026` | ❌ **Not migrated. Not previously known about. Different GitHub org.** Classroom repo — likely connects to the open classroom-CA question (`olab_code#6`). |
| `IE-482-582/spring2025` | Out of scope — explicitly excluded by user instruction (2026-07-18), not investigated. |

## What "released" means for the archival gate — also unresolved

The reorg plan's own archival condition says consumers must be "pinned to
released `olab_*` versions." Taken literally, this isn't true for *any*
consumer yet — every one of them (`ofm`, `cuas_practice`, `tts_practice`,
`CoG`) is pinned via a Git commit SHA (`git+https://.../olab_code.git@<sha>`),
not an actual tagged GitHub Release/wheel, because `olab_code`'s release CI
doesn't exist yet (a separate, still-open reorg-plan item). Before archiving
`ub_code`, decide explicitly: is a full-SHA git pin "good enough" to call a
consumer migrated for archival purposes, or does archival actually wait on
real release CI + tags existing first? This document assumes the former
(git-SHA pins count) unless told otherwise, since that's the pattern already
accepted for every migration done so far this reorg.

## What archiving `ub_code` actually does (and doesn't do)

GitHub's native repo archival makes a repo **read-only, not deleted or
unpublished**. `git clone`/`pip install git+https://.../ub_code.git@<ref>`
keep working exactly as before — archiving only blocks new pushes, PRs,
issues, and issue comments. So this is not an urgent "everyone breaks
tomorrow" situation for the four newly-found consumers. What actually
changes: `ub_code` becomes permanently frozen — nobody can fix a bug, patch
a security issue, or make any further change to `ub_camera`/`ub_utils`
through that repo ever again. Anyone still depending on it afterward is
depending on code that can only get worse relative to the actively-maintained
`olab_camera`/`olab_utils`, never better. That's the actual argument for
migrating these four rather than leaving them be — not an imminent breakage.

## Plan, once actually ready

1. **Resolve all four new consumers** (`arbotix_private`, `warehouse_drone`,
   `ub_racer`, `IE-482-582/spring2026`). For each, either:
   - Migrate them the same way the other four consumers were handled (rename
     `ub_camera`/`ub_utils` imports to `olab_camera`/`olab_utils`, pin via
     git+commit-SHA, verify in a fresh venv) — `arbotix_private` needs extra
     care given its apparent local `ub_camera.py` fork/modification, investigate
     that before assuming a straight rename works. `IE-482-582/spring2026`
     also has a Dockerfile and two committed venvs to account for, not just
     Python source. `ub_racer` is the largest/most central of the four —
     probably do this one first, or with the most care; or
   - Get an explicit, informed decision that it's acceptable for that project
     to keep depending on a now-frozen `ub_code` indefinitely (see "What
     archiving actually does" above — this is a real option, not just a
     stopgap, for something genuinely done/frozen, e.g. `IE-482-582/spring2025`,
     already ruled out of scope entirely).
2. **Re-check via the corrected methodology** before actually archiving:
   sweep every local checkout under `~/Projects` (`find ~/Projects -maxdepth
   3 -name .git`, grep each for `ub_camera`/`ub_utils`/`ub_code`) rather than
   trusting `gh search code` alone — it missed `ub_racer` despite it being in
   the same org already searched. Also re-run the org-wide GitHub search as a
   supplement (`gh search code "ub_camera"/"ub_utils" --owner optimatorlab`,
   and consider `"ub_code"` for references to the repo/package name itself,
   e.g. in install docs), and check the `IE-482-582` org too now that it's
   known to matter. Neither method is exhaustive — nothing covers a repo that
   exists only on someone else's machine or in an org not yet considered —
   so also just ask around before the final archive step.
3. **Triage `ub_code`'s 6 open issues** (`#5` Sample Jupyter Notebooks, `#7`
   Installation Issues, `#11` Alternate Streaming Options, `#12` refactor
   ub_camera, `#13` CameraUSB Refactor Plan, `#15` RPi Camera Module 2)
   before archiving — GitHub issues on an archived repo become effectively
   frozen (visible, but you can't be sure new comments/notifications work the
   same way). For each: close with a pointer to the equivalent `olab_code`
   issue/tracker if the work is still relevant, or close as superseded/
   won't-fix if not. Don't archive with live, actionable issues just sitting
   there unaddressed.
4. **Update `ub_code`'s `README.md`** to point at `olab_code` as the
   successor before archiving (a reader landing on an archived repo cold
   should immediately see where the real thing lives now).
5. **Confirm the `murray` branch** (in addition to `main`) has nothing
   uniquely valuable not already reflected in `olab_code` — it's a leftover
   working branch from `ub_code`'s own prior development.
6. **Archive via GitHub's native repo archival** (Settings → Danger Zone →
   Archive this repository) — makes it fully read-only (no pushes, no new
   issues/PRs/comments) but keeps all history, issues, and content visible.
   This is the "archived read-only" state the reorg plan describes — not a
   deletion.

## Explicitly not doing

- Not deleting `ub_code` — archiving only.
- Not touching `arbotix_private`, `warehouse_drone`, `ub_racer`, or
  `IE-482-582/spring2026`'s code as part of *this* document — that's
  follow-up work once step 1 (`CoG` hardware validation) is done and this
  plan is picked back up.
- Not investigating `IE-482-582/spring2025` — explicitly out of scope by
  user instruction (2026-07-18).
