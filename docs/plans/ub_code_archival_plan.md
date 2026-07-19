# `ub_code` repo archival plan

Step 2 of the post-migration rollout (see `olab_packages_reorg_plan.md`'s
"Decisions" §2 and "Open items" #7 — archival was always explicitly gated on
completing a full consumer inventory first, not just the three projects
already investigated in that plan). Sequenced after step 1, hardware-testing
`CoG/realtime_transcription`'s `olab_audio` migration, per the user's
explicit ordering (2026-07-17): don't archive `ub_code` until the last known
consumer relying on similar patterns is confirmed safe.

## Status: ARCHIVED (2026-07-19)

`ub_code` was archived on GitHub (Settings → Danger Zone → Archive this
repository) on 2026-07-19, confirmed read-only. Two consumer PRs were left
open at archive time — `ofm#38` and `warehouse_drone#28` (both `murray`→
`main`, migration code already done, just unmerged) — the user is handling
those separately; archiving `ub_code` doesn't block either from merging
later, since archiving only freezes `ub_code` itself (see "What archiving
`ub_code` actually does" below), not the consumer repos.

This document is kept for historical reference; nothing further to do here.

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
| `ofm` | ✅ Migration itself confirmed clean on `murray` (re-verified 2026-07-19) — all `ub_camera`/`ub_utils`/`ub_audio` imports/usage are gone, remaining string matches are comments/historical references only. **PR #38 (`murray`→`main`) is still open**, not merged — `main` itself still has stale references (e.g. `install.md`'s `ub_camera` mention) that won't resolve until #38 merges. Per the user (2026-07-19), OFM's broader migration/feature work is "very far from complete" — `deferred_items.md` alone has 1100+ lines of unrelated outstanding work; that backlog is out of scope for the `ub_code` archival effort specifically. |
| `cuas_practice` | ✅ Migrated to `olab_rf` (PR #2) |
| `tts_practice` | ✅ Migrated to `olab_voice` |
| `CoG/realtime_transcription` | ✅ Migrated to `olab_voice` + `olab_audio` — code committed/pushed, **hardware validation in progress (step 1)** |
| `ub_racer` | ✅ Migrated and merged (PR #3, 2026-07-19). Pinned to `olab_code@main` (not a SHA — this repo sits untouched between sessions, so floating `@main` was chosen over a SHA that'd go stale before the repo is touched again). |
| `arbotix_private` | ✅ Migrated and merged (PR #15, 2026-07-19). The suspected local `ub_camera.py` fork (custom `set_allowlist`/`add_to_allowlist`/`remove_from_allowlist`) turned out to already be resolved — it was deleted in commit `a0d4b0c` (2026-02-23), well before this session, in favor of installing the real package. `olab_camera` already has those allowlist methods natively, so this ended up being a straight rename, not a reconciliation. Pinned to `olab_code@main`, same reasoning as `ub_racer`. |
| `warehouse_drone` | ✅ Migrated, PR #28 **still open** as of 2026-07-19 — confirm merged before archiving. Confirmed still active (last pushed 2026-05-13, not archived) before cloning. Only one consumer file (`Research Upload/aruco_localize_v2.py`, `CameraPi2` usage only, no `ub_utils`/`checkVersion`); no requirements/Dockerfile pinning `ub_code` in this repo to update. |
| `IE-482-582/spring2026` | **Not migrated — explicit decision to leave it be** (user instruction, 2026-07-19): "We are not going to touch the spring2026 repo." Same treatment as `spring2025` — accepted as a project that keeps depending on a now-frozen `ub_code` indefinitely, per the "What archiving actually does" section below. Superseded by `IE-482-582/spring2027` (`murray` branch, PR #2, merged) for ongoing classroom use. |
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

1. ✅ **Done.** `ub_racer` (PR #3) and `arbotix_private` (PR #15) are migrated
   and merged into `main` (2026-07-19). `warehouse_drone` (PR #28) is
   migrated, still open as of 2026-07-19 — confirm it's merged before the
   final archive step. `IE-482-582/spring2026` has an explicit, informed
   decision to leave it depending on `ub_code` indefinitely (user
   instruction, 2026-07-19), same treatment as `spring2025`.
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
3. ✅ **Done** (2026-07-19). Triaged all 6 open issues:
   - `#5` Sample Jupyter Notebooks → migrated to `olab_code#10`, closed in
     `ub_code` with a pointer. Cross-referenced `IE-482-582/spring2027#1`
     (same notebooks-never-ported gap on the classroom side).
   - `#7` Installation Issues → left open in `ub_code` as-is, per user
     instruction (2026-07-19) — not migrated, not closed.
   - `#11` Adding Alternate Streaming Options → **already implemented**
     in `olab_camera` (verified: `startStream(protocol='mjpeg'|'websocket'
     |'webrtc', force=, signalingMode=)`, `WebSocketStreamingServer`,
     `CameraVideoTrack`, `WebRTCStreamingServer` all present). Closed in
     `ub_code` as not-planned, not migrated.
   - `#12` refactor ub_camera → still a legit, unaddressed improvement
     (`olab_camera/__init__.py` is now 5,682 lines, up from ~4,500,
     still fully monolithic). Migrated to `olab_code#12` with updates
     reflecting what's changed since, closed in `ub_code` with a pointer.
   - `#13` CameraUSB Refactor Plan (explicit capture thread pattern) →
     **already implemented** in `olab_camera` (verified:
     `_startCaptureThread()`/`_stopCaptureThread()`/`_captureLoop()`,
     `frameProcessor` hook, `_thread_capture()` no longer exists). Closed
     in `ub_code` as not-planned, not migrated.
   - `#15` RPi Camera Module 2 → migrated to `olab_code#11` (exact copy),
     closed in `ub_code` with a pointer.
4. ✅ **Done** (2026-07-19). Added a banner to the top of `ub_code`'s
   `README.md` pointing at `olab_code`/`olab_camera`/`olab_utils`, with
   install instructions and the `checkVersion()` gap called out. Left the
   1000+ lines of tutorial content below it as historical reference rather
   than rewriting the whole doc. PR: `ub_code#21` (not yet merged).
5. ✅ **Done** (2026-07-19). `murray` is exactly 1 commit ahead of `main`
   (`9ec7dea`, "Add recordVideoLocal/stopRecordVideoLocal and lastFrameAge
   to Camera", 2026-07-15). Verified all three — `recordVideoLocal(path=,
   filename=, fps=15, colorOption=, resOption=)`, `stopRecordVideoLocal
   (timeout=5.0)`, `lastFrameAge()` — already exist in `olab_camera` with
   identical signatures. Nothing uniquely valuable left on `murray`.
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
