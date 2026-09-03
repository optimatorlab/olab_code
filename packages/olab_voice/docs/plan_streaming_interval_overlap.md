# Plan: audio continuity across interval_final cuts (word-truncation fix)

## Status

**Implemented and shipped.** This document is the original design plan,
written before implementation, and is kept as a design record (not deleted,
per its own last line) rather than because it is still literally accurate
line-by-line. Three pairreview rounds during implementation found that
several passages below describe `_consume_frame`/`_reset_segment`/
`_emit_segment` against a misremembered version of the pre-existing code, or
a resolution that a later round found was unsound and replaced. Those
passages are corrected in place below (marked "as shipped"), rather than
left to silently contradict the actual code — see
`packages/olab_voice/src/olab_voice/stt/faster_whisper_streaming.py` itself
for the authoritative, current behavior; this doc is the design rationale,
not a substitute for reading the code.

Follow-up to PR #64 (internal-VAD-segment join fix). Independent of that
fix — this addresses a deeper problem: zero audio overlap between
consecutive `_transcribe()` call buffers, which can split a word's audio
exactly at a hard cut.

## Background

`_reset_segment()` (`faster_whisper_streaming.py:397-405`) fully discards
`self._frames` at every segment boundary. A word whose audio spans the
exact cut instant gets split across two independently-decoded buffers —
each half too incomplete to recognize correctly, either garbled or dropped
outright by `vad_filter=True` as non-speech.

Concrete live repro (sayso): reference `"...at the bench (this session can
produce one file per test session..."`, actual output `"...at the bench
tuce one file per test session..."` — `"this session can pro-"` vanished
entirely (likely VAD-filtered as non-speech noise) and `"duce"` was
misrecognized as `"tuce"` (no acoustic context for what precedes it),
landing exactly at a `target_interval_seconds` cut in the daemon logs
(a `15.008s` segment immediately followed by a `9.312s` segment).

**Real tension already on record, not newly discovered:** `vad_filter=False`
avoided this truncation-drop but caused Faster-Whisper to hallucinate
invented words over silence (issue #65, `"...is still out there outstanding
work"`). `vad_filter=True` (current default) reopened this issue. Tuning
`target_interval_seconds`/`endpoint_silence_seconds`/`silence_threshold_db`
alone cannot satisfy both properties simultaneously — this needs real audio
continuity across the cut, not a parameter adjustment.

**Scope restriction, load-bearing:** this mechanism applies **only** to
`transcript.interval_final` boundaries. A `transcript.segment_final` fires
because real silence (`endpoint_silence_seconds`) was just detected — by
definition nothing can be mid-word at that instant. Carrying overlap there
is pure overhead with no benefit, and avoiding it sidesteps a real
interaction risk with the `context_reset_silence_multiplier` long-gap-reset
logic (see the "found during design" section below for the specific
interaction this restriction does *not* fully avoid on its own).

## Agreed mechanism, decision by decision

### 1. Overlap retention

Instead of `_reset_segment()` fully discarding frames on an
`interval_final` emit, retain the trailing audio and seed it into the next
segment's `_frames`. Retention is **whole-frame granularity** — walk
backward from the end of the old segment's frame list, accumulating frame
durations, until the accumulated duration is `>= interval_overlap_seconds`;
keep that suffix. No frame-splitting (mic frame chunks are small — tens of
milliseconds — so overshooting the configured overlap by less than one
frame's duration is a negligible, acceptable approximation). The **actual**
retained duration (which may exceed the configured
`interval_overlap_seconds` due to this rounding) must be tracked precisely
per-transition — it is the real cutoff used for word-level trimming later,
not the configured knob value.

This only happens when `event_type == "transcript.interval_final"` and
`self.interval_overlap_seconds > 0`. A `transcript.segment_final` emit (and
`stop(flush=False)`'s reset) always does the existing full discard.

### 2. Two separate timestamp trackers per segment, not one

Today, `_segment_start_time` serves two roles: (a) physical buffer start,
used for `_segment_duration()`'s `target_interval_seconds` trigger, and (b)
the emitted event's `capture_start_time`. Seeding a buffer with retained
overlap frames means the physical buffer starts earlier than where the
*new* content actually begins — conflating these two roles would be wrong
for both:

- **`_segment_duration()` / the interval trigger** must measure only new
  content. Add `_new_content_start_time: float | None`, cleared by
  `_reset_segment()` (and left `None` immediately after an overlap-seed —
  not set until the first genuinely new frame arrives). `_consume_frame`
  sets it the same way `_segment_start_time` is set today (`if
  self._new_content_start_time is None: self._new_content_start_time =
  frame.timestamp`), so for the common no-overlap case the two fields are
  set at the identical moment and behave exactly as today.
  `_segment_duration()`'s interval-trigger check uses
  `_new_content_start_time`, not `_segment_start_time` — each interval
  segment still represents `~target_interval_seconds` of genuinely new
  audio; the retained overlap is "free" extra context on top, not counted
  against the timer.

  **Correction, as shipped (round-1 review B1(a)/B1(b)):** the "same way
  `_segment_start_time` is set today" claim above is wrong — the actual
  pre-existing setter was `if not self._frames: self._segment_start_time =
  frame.timestamp`, gated on the frame list's emptiness, not on the
  field's own `None`-ness. Under overlap seeding, `self._frames` is
  *already non-empty* when the first new frame arrives, so that gate would
  never fire and `_segment_start_time` (if kept) would stay `None` for the
  whole segment. The shipped fix instead gates
  `_new_content_start_time` purely on its own `is None` check, which is
  correct regardless of seeding because `_consume_frame` only ever
  processes genuinely new frames (retained frames are injected directly
  into `self._frames` by `_reset_segment`, never routed through
  `_consume_frame` — see the corrected §3 below). `_segment_start_time`
  itself is **removed** in the shipped code (see the next bullet).
- **`capture_start_time`** on the emitted `TranscriptEvent` uses
  `_new_content_start_time` too (captured before `_reset_segment()`, same
  pattern as the existing `segment_start_time`/`segment_end_time` locals in
  `_emit_segment`) — so consecutive events' `capture_start_time`/
  `capture_end_time` stay contiguous and non-overlapping, matching today's
  semantics exactly, rather than double-counting the overlap window's time
  across two events.
- ~~`_segment_start_time` keeps its current meaning (physical buffer start,
  used for `_segment_end_time`/frame bookkeeping) — unchanged.~~
  **Correction, as shipped (round-1 review B1(b)):** this claim was wrong
  — once `_segment_duration()` and `capture_start_time` both move to
  `_new_content_start_time` as described above, nothing in the class reads
  `_segment_start_time` any more. Carrying a field forward under a claim
  that it is still load-bearing, when it is not, would be actively
  misleading to a future reader. The shipped code removes
  `_segment_start_time` outright; `_segment_end_time` is kept (still used
  by `_segment_duration()` as the "end" side of the new-content-only
  duration, and by frame bookkeeping generally).

### 3. Bookkeeping replay over retained frames — split by purpose

Overlap-seeding must replay `_is_speech()`/timestamp bookkeeping over the
retained frames so endpoint-silence detection (`_last_speech_end_time`)
stays accurate for the *new* segment. But — found while tracing this
through, not assumed — **this must not also feed
`_segment_first_speech_time`**, which is a different field with a different
consumer: #63's `context_reset_silence_multiplier` long-silence-reset check
computes `gap = segment_first_speech_time - previous_speech_heard_time`.
The retained overlap tail is audio the *old* segment already accounted for
in its own `_last_speech_heard_time` before it reset — so if overlap replay
also sets `_segment_first_speech_time` from that same (already-counted)
speech, the computed gap comes out at or near zero on every overlap-seeded
segment, regardless of how long a real silence follows. That would silently
defeat the #63 long-silence context reset specifically on segments that
follow an interval_final, which is exactly backwards (interval_final
segments, by construction, never had a real pause — there's nothing to
protect against there in the first place, but the field must not be left in
a state that miscalculates for whatever follows).

Resolution: keep two separate roles distinct in the replay step —
- `_last_speech_end_time` (endpoint-silence purposes): **is** replay-seeded
  from the retained overlap's own speech content. Legitimate — it needs to
  know "how long since any speech, including the tail we just carried
  forward" to correctly measure the next real silence gap.
- `_segment_first_speech_time` (#63's gap-check purposes): **is not**
  touched by overlap replay. It is left `None` through the replay step and
  only set by a genuinely new frame — same trigger condition as
  `_new_content_start_time` above (first-new-frame, not first-retained-frame).

  **Correction, as shipped (round-1 review, "silently changes what #63's
  gap measures"):** "same trigger condition as `_new_content_start_time`"
  is ambiguous in a way that matters and, read literally, is wrong.
  `_new_content_start_time` is set on the first genuinely-new *frame*
  (speech or not); `_segment_first_speech_time` must be set on the first
  genuinely-new *speech* frame specifically — a different, narrower
  condition. The shipped fix gates the assignment in `_consume_frame` on
  `_segment_first_speech_time is None` directly (rather than on
  `_last_speech_end_time is None`, the pre-existing predicate, which
  overlap replay can legitimately make `False` before any new speech has
  arrived — see below). Gating on the field's own `None`-ness is what
  makes this correct under overlap replay: the field is always `None`
  entering a segment's first genuinely-new speech frame (never
  replay-seeded), so this fires exactly once, on the right frame,
  regardless of whether replay pre-populated `_last_speech_end_time`.

### 3a. `has_speech` redefinition — found during implementation review (round 3), not in the original design

Not anticipated when this plan was first written. `_emit_segment` has a
pre-existing `has_speech = self._last_speech_end_time is not None` check
that gates whether the segment is transcribed/published at all. Once §3
above makes it legitimate for `_last_speech_end_time` to be replay-seeded
from the *previous* segment's audio, that check stops meaning "this segment
had speech" — it can be `True` with zero genuinely-new speech observed in
the current segment. An earlier resolution attempt argued this was harmless
because the word-trim step (§5) would reduce such a segment's text to
empty anyway, so the pre-existing empty-text early return would prevent any
event being published. Round 3 review showed this was unsound: §5's
keep-bias specifically keeps a word whose `word.end > cutoff`, which is
exactly the straddling boundary word this whole feature exists to
preserve — so a segment with zero new speech but a straddling word whose
alignment timestamp lands past the cutoff (e.g. because Faster-Whisper's
own VAD picked up audio that `silence_threshold_db` gated out of
`_is_speech()` — the same gate/VAD disagreement described in Background
above) could still decode and publish that word again as spurious "new"
text: a duplicate-content event.

Shipped fix: `has_speech` is redefined to `segment_first_speech_time is not
None`. Since `_segment_first_speech_time` is never replay-seeded (§3), this
correctly means "this segment observed genuinely new speech," and the
early return fires *before* `_transcribe()` is ever called for a
zero-new-speech segment — the trim step never gets a chance to matter for
this case, closing the gap at its root rather than downstream. Byte-identical
when overlap is disabled: with no replay, `_consume_frame` sets both fields
on the same frame, so the two predicates coincide.

### 4. `initial_prompt` suppressed on overlap-carrying calls

When a call's buffer carries a retained overlap prefix
(`pending_overlap_seconds > 0` for that specific `_transcribe()` call), do
not pass `initial_prompt` into that call, even if #63's `_next_initial_prompt`
machinery has a value ready. Rationale: the old segment's own (possibly
garbled — that is the entire premise of this bug) trailing text would
become a text-continuation hint for the *same audio* this call is about to
re-decode acoustically; the model could anchor on the wrong hint instead of
correctly re-decoding from clean acoustic evidence. This is a local
override at the call site only — the surrounding #63 bookkeeping (storing
this segment's own resulting trailing text into `_next_initial_prompt` for
whatever segment comes *after* this one) is untouched.

### 5. Word-level trim, composed with PR #64's segment-join logic

`word_timestamps=True` is requested on a `_transcribe()` call **only** when
that specific call's `pending_overlap_seconds > 0` — not whenever
`self.interval_overlap_seconds > 0` as a static config check. This means:
the very first segment after transcriber start, and any segment following a
`segment_final` (which never retains overlap), pay zero extra decode cost
even when overlap-carry is configured on. `word_timestamps` is never
independently exposed as a consumer-facing field — see the API section
below.

Trim algorithm, composed as a preprocessing pass *before* PR #64's existing
segment-join algorithm (which operates on a list of whole-segment text
strings and is otherwise completely unchanged):

1. cutoff = the actual retained overlap duration for this call (§1),
   expressed in this call's own buffer-relative time coordinate (0 =
   start of the retained prefix).
2. For each Faster-Whisper segment in `collected`, inspect its `.words`
   (populated because `word_timestamps=True`):
   - A word is **dropped** only if `word.end <= cutoff` — entirely within
     the already-covered overlap window, i.e., already correctly emitted
     by the old segment.
   - A word is **kept** if `word.end > cutoff` — this includes any word
     that starts before the cutoff but extends past it (the boundary word
     itself). This directly implements the already-agreed bias: an
     ambiguous boundary word is kept (risk an occasional visible, correctable
     duplicate) rather than dropped (risk a silent, invisible loss).
3. If a segment's words are entirely kept or entirely dropped, use its
   original `segment.text` unchanged or drop the segment entirely — fast
   path, no reconstruction needed.
4. If a segment is partially trimmed (some leading words dropped, some
   kept), reconstruct that segment's contributing text from only the
   surviving words' `.word` strings, joined — **verify Faster-Whisper's
   actual `Word.word` whitespace convention empirically before implementing
   the join** (don't assume; different Whisper wrapper versions have
   differed on whether `.word` includes a leading space).
5. Feed the resulting list of (possibly trimmed/reconstructed) per-segment
   texts into PR #64's existing `raw_texts` → strip-trailing-punctuation →
   decapitalize → join pipeline unchanged. A segment that trims down to
   empty is dropped by that pipeline's existing punctuation-only-segment
   handling, which already tolerates empty entries.

**Applies regardless of the receiving segment's own resulting event type.**
An interval_final segment retains overlap for whatever comes next — that
"next" segment gets the trim applied to its own buffer whether *it* itself
ends via `interval_final` or `segment_final`. Trimming is driven by "did
this call's buffer receive a retained prefix," not by this call's own
`event_type`.

### 6. API shape: one consumer-facing field

`interval_overlap_seconds: float = 0.0`. Default preserves today's exact
behavior for every current consumer (sayso, CoG) — zero overlap, zero
`word_timestamps` cost, byte-identical code path when disabled.
`word_timestamps` is never independently exposed (see §5 — footgun
avoidance, derived per-call from internal state, not a public toggle).

**Validation** (`__post_init__` and `update_tuning()`, matching the existing
pattern for the other tuning fields):
`interval_overlap_seconds >= 0` (0.0 is the valid "disabled" sentinel — note
this is `>= 0`, not the `> 0` pattern the other tuning fields use, since
those have no meaningful zero state and this one does), `isfinite`, and
`interval_overlap_seconds < target_interval_seconds` (an overlap
approaching or exceeding the whole buffer duration is degenerate — most or
all of each buffer would be repeated content).

**`update_tuning()` cross-field validation gotcha:** the existing
atomic-validate-then-assign pattern validates each provided field against
its own static rule. `interval_overlap_seconds < target_interval_seconds`
is a *cross-field* rule — if a caller updates only one of the two fields,
validation must check the rule against the **resulting combination** (new
value where provided, existing field value otherwise), not just the field
being changed in isolation. E.g. `update_tuning(target_interval_seconds=0.1)`
while `interval_overlap_seconds` is currently `0.5` from construction must
be rejected, even though `target_interval_seconds` alone still passes its
own `> 0` check.

## Edge cases (from the original request, walked through concretely)

- **Overlap window that's pure silence**: the retention/replay mechanism
  doesn't inspect content, only duration — a silent overlap window is
  retained, replayed (finding no speech, so `_last_speech_end_time` isn't
  updated by it), and word-trimmed (finding no words in that time range, so
  nothing to drop) exactly like any other case. No special-case code
  needed; this degrades to a no-op through the same general mechanism,
  confirmed by tracing through §1/§3/§5 rather than assumed.
- **A word timestamp landing exactly on the boundary**: resolved by §5's
  `word.end <= cutoff` (strict `<=` drops, everything else keeps) —
  consistently implements the keep-bias from the original design; no
  separate off-by-one handling needed.
- **Interaction with `context_reset_silence_multiplier`**: real interaction
  found and resolved in §3 (the `_segment_first_speech_time` contamination
  risk) — this was not avoidable purely by restricting scope to
  `interval_final`, as originally assumed; it required the two-tracker
  split. A second, related interaction (a duplicate-content event from a
  zero-new-speech segment, not a context-reset miscalculation) was found
  one review round later and required the §3a `has_speech` redefinition on
  top of the two-tracker split — the two-tracker split alone was necessary
  but not sufficient.

## Verification requirements (must be satisfied empirically before shipping)

- **Latency**: `word_timestamps=True` has real decode overhead in
  faster-whisper. Given the standing live complaint this session about a
  `~10s` wait before text appeared (from an unrelated `target_interval_seconds`
  increase), this must be measured on sayso's actual GPU/`beam_size=5`
  setup, not a synthetic benchmark, before this can be considered
  acceptable to ship.

  **Disposition, as agreed before implementation started (task-contract
  override #2, recorded in the pairwrite task's `plan.md`/`log.md`):** this
  specific item could not be satisfied inside the sandboxed implementation
  session (no GPU, not deployed to sayso's hardware) and was explicitly
  descoped as a known, deliberately-deferred **post-merge manual
  acceptance item** — not attempted with a synthetic in-repo benchmark, and
  not a gate on this task's plan or code approval. It remains sayso's/the
  user's responsibility to verify separately after merge.
- **`word_timestamps=True` doesn't change segmentation**: confirm empirically
  (fixed sample audio, compare `segment.text`/segment boundaries with and
  without `word_timestamps=True`) that enabling it only adds `.words` to
  each segment and does not otherwise alter Faster-Whisper's own
  segmentation or `segment.text` output — this is assumed based on general
  Faster-Whisper behavior but not yet confirmed against the actual installed
  version.

  **Confirmed, with a bound (implementation session, faster-whisper 1.2.1 +
  a real `tiny.en` model):** `segment.text` was byte-identical with and
  without `word_timestamps=True` on real audio. However, the unbounded form
  of this claim is not quite right: `segment.start`/`segment.end` **do**
  change (`restore_speech_timestamps` overwrites them from the word list
  when words exist), which is expected and harmless since this code never
  reads those fields. The claim is also only confirmed for buffers that fit
  inside Faster-Whisper's internal ~30s decode window — true for every
  current consumer's realistic `target_interval_seconds +
  interval_overlap_seconds` (sayso/CoG defaults are single-digit seconds),
  not verified beyond it (`word_timestamps=True` also affects internal
  `seek` recomputation and interacts with `hallucination_silence_threshold`
  for longer buffers, per the installed version's source).
- **`Word.word` whitespace convention** (§5, point 4): confirm empirically
  before implementing the partial-segment reconstruction join.

  **Confirmed** (implementation session, and independently re-confirmed
  during code review with a separately-recorded real-audio run): each
  `Word.word` carries its own leading space (e.g. `' produce'`, `' one'`).
  The join is `"".join(word.word for word in kept).strip()`, not
  `" ".join(...)` — the latter was demonstrated to double every inter-word
  space.
- **The actual repro** (`"...produce..."`/`"tuce"` or an equivalent
  boundary-word case) should be reproduced and confirmed fixed, not just
  covered by synthetic unit tests with fake segment/word objects.

  **Confirmed**, twice independently (once during implementation, once
  during code review, on two different synthetically-generated real-speech
  buffers, through the real shipped `_transcribe()` method, not a mock):
  a word split across a hard interval cut is garbled/misrecognized in the
  unpatched (no-overlap) baseline, and correctly recovered — with the
  already-emitted overlap text correctly trimmed away, not duplicated —
  through the shipped `interval_overlap_seconds` mechanism. Full commands
  and output for both runs are in the pairwrite task's `log.md`
  (`.pairwork/streaming-interval-overlap/log.md`, not committed to the
  repo).

## Acceptance criteria / tests

Right-sized per standing test-scope preference — cover the mechanisms
above, not an exhaustive timestamp/threshold matrix:

- `interval_overlap_seconds=0.0` (default): byte-identical behavior to
  today — existing test suite passes unmodified, no `word_timestamps`
  requested, no overlap retained.
- An `interval_final` segment with overlap enabled retains the correct
  trailing frames (whole-frame granularity) and the following segment's
  buffer includes them.
- Word-level trim: a fake segment/word setup where a boundary word's
  `.end` is on each side of cutoff (dropped when `<=`, kept when `>`),
  including the partial-segment reconstruction path (§5 point 4).
- `_new_content_start_time` / `capture_start_time` continuity: consecutive
  events' `capture_start_time`/`capture_end_time` remain contiguous
  (non-overlapping) across an overlap-carrying boundary.
- `_segment_first_speech_time` is not contaminated by overlap replay: a
  case that would have produced a near-zero #63 gap under the naive
  (unsplit) implementation, confirming the two-tracker fix.
- `initial_prompt` is suppressed specifically on overlap-carrying calls,
  and unaffected on all others (regression check against #63's tests).
- `interval_overlap_seconds`/`target_interval_seconds` cross-field
  validation, both at construction and via `update_tuning()` with only one
  field provided.
- `segment_final` never retains or receives overlap (regression check —
  the scope restriction actually holds in code, not just in the design
  doc).

## Out of scope

- `faster_whisper.py` (batch backend) — not touched, same reasoning as PR
  #64 (no equivalent "caller already committed to one utterance"
  precondition for a whole-file transcription).
- Issue #65 (shared repetition/hallucination filter) — separate, tracked
  issue; this task's keep-bias policy adds a second motivating case for it
  (already noted there) but does not implement it.

## Next steps

Given the scope (frame retention, two new timestamp trackers with a subtle
cross-field interaction already found and fixed once during design, a new
word-level filtering step composed with existing segment-join logic, and
empirical performance/behavior verification requirements), this warrants at
least the same `/delegate` rigor as PR #64, likely more given the larger
new failure surface.

**Done.** All three steps below completed via `.pairwork/streaming-interval-overlap/`
(pairwrite + pairreview): plan review ran 3 rounds (one blocking finding per
round, each a real bug caught before implementation — see the "as shipped"
corrections inlined above); code review found no blocking issues in the
implementation itself, one `important` finding (this doc being stale
relative to the shipped code — the corrections in this document are that
finding's resolution).

1. ~~`/pairwrite`: implement this plan...~~ Implemented in
   `packages/olab_voice/src/olab_voice/stt/faster_whisper_streaming.py` and
   `packages/olab_voice/tests/test_faster_whisper_streaming.py` (14 new
   tests). The empirical verification requirements above were run against
   the real installed `faster-whisper==1.2.1` package and a real model, not
   just asserted — results are inlined into the Verification requirements
   section above and fully detailed in `log.md`.
2. ~~`/pairreview` before push...~~ Done — see disposition above.
3. This plan doc is being **kept** (writer's call, exercising the option
   this line always offered), corrected in place to match what shipped
   rather than left to silently describe an earlier, superseded design.
