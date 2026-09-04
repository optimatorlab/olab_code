# Plan: fix `78105c1`'s internal-segment-join over-correction (olab_code#66)

## Status

Implemented. Written via `/grillme`, then implemented via `/delegate`
(pairwrite/pairreview, `.pairwork/internal-segment-join-revert/`) against
`faster_whisper_streaming.py` and its test file on the `murray` branch —
same rigor as the other three commits on PR #64.

Two refinements were made during implementation, beyond the sketch below:

- `_collapse_malformed_trailing_punctuation()` also `rstrip()`s the
  retained prefix, so a whitespace character sitting immediately before the
  collapsed run (`"Deferred until .."`) doesn't leave a new malformed
  `" ."` shape behind — the concern the pre-#66 code's chained `.strip()`
  handled.
- `_INTERNAL_SEGMENT_TRAILING_PUNCTUATION` was renamed as well as narrowed,
  to `_COLLAPSIBLE_TERMINAL_PUNCTUATION = ".!?"`, since it no longer names
  a strip set scoped to internal (non-last) segments.

Still outstanding: live hardware dictation validation per "Testing
strategy" below, then merge PR #64.

## Background

`78105c1` (part of olab_code#63 / PR #64) added logic to
`FasterWhisperStreamingTranscriber._transcribe()`
(`packages/olab_voice/src/olab_voice/stt/faster_whisper_streaming.py`) that,
when Faster-Whisper's own decoding produces multiple internal `Segment`
objects for one `_transcribe()` call, blanket-strips the full trailing
`.!?…` run from every non-last internal segment and decapitalizes the
leading letter of every non-first surviving segment (guarded against `"I"`
and multi-char acronyms). The stated rationale: "by the time `_transcribe()`
is called, the caller has already decided this whole buffer is one
continuous utterance, so an internal split ... is essentially never a real
sentence boundary."

That rationale is wrong. Confirmed via live dictation on real sayso
hardware, 2026-09-03 (filed as
[olab_code#66](https://github.com/optimatorlab/olab_code/issues/66)): a
natural pause between two real sentences, shorter than
`endpoint_silence_seconds` (so the *outer* segmentation correctly keeps it
as one `_transcribe()` buffer) but long enough for Faster-Whisper's own
decoder to emit a real terminal-punctuated internal split, gets its period
stripped and its next word decapitalized — merging two real sentences into
a run-on. Raw `TranscriptEvent` text logged directly from `olab_voice`
showed `"...affecting all your Cloud Code sessions next, decide
whether..."`; the real spoken boundary was `"...sessions. Next: decide
whether..."`.

## Why a gap/confidence-threshold heuristic was rejected as the fix

The initial fix direction floated (before this design pass) was a
timestamp-gap heuristic: compare `segment[i].end` to `segment[i+1].start`,
treat a large gap as a real boundary and a near-zero gap as a decoder
artifact. Investigated and rejected during this `/grillme` pass:

- **No reliable way to calibrate a threshold.** Doing so properly would
  require a large, diverse sample of real speech across different speaking
  styles/paces — effectively training data — to separate "real short pause"
  gaps from "decoder hiccup" gaps with any confidence. A threshold picked
  from one or two observed data points (the `"FRS, GMRS, Corpus"` artifact
  and the `"sessions. Next:"` real-pause case) is exactly the kind of thing
  that looks fine on the cases we happened to observe and flakes on the
  next one.
- **Faster-Whisper's per-segment confidence signals
  (`avg_logprob`/`no_speech_prob`) don't solve this either** — using them as
  a secondary corroborating signal was considered and rejected as "still a
  guess": no established relationship between those scores and
  real-boundary-vs-artifact for this specific ambiguity, and adding them
  multiplies the tuning/testing surface without evidence they help.
- **No config-level root-cause fix exists either.** Investigated whether
  Faster-Whisper (`faster-whisper==1.2.1`, per
  `packages/olab_voice/pyproject.toml`) exposes a knob that suppresses
  spurious mid-utterance splits while preserving real ones.
  `vad_filter`/`vad_parameters` control a *pre-decode* silence-trimming
  pass over the input audio; they don't gate whether the decoder itself
  emits multiple `Segment` objects for one continuous decode. Multi-segment
  output is Faster-Whisper's own decode-time `<|timestamp|>` boundary
  prediction, which fires for essentially any multi-clause audio and
  usually *does* reflect a real acoustic break — it is not generally a VAD
  artifact. There's no parameter that classifies "meaningful segmentation"
  vs. "decoder noise," because the distinction we're chasing doesn't
  cleanly exist at that layer.

This last point undercuts `78105c1`'s founding assumption directly: internal
segment splits are *usually* meaningful, not "essentially never" a real
boundary. Treating every internal split as noise was the wrong default from
the start, not just an edge case missed by a synthetic test suite.

## Agreed fix: revert to trusting the decoder, keep only boundary-agnostic guards

Default back to joining internal segments with their own punctuation and
capitalization intact — i.e., stop pretending `olab_voice` knows better than
Faster-Whisper's own segmentation about where a sentence ends. On top of
that, keep exactly two guards, chosen because both are true independent of
whether a given internal split is a real sentence boundary or a decoder
artifact — so neither requires classifying the split at all:

1. **Drop pure-punctuation-only segments entirely.** A segment whose text
   contains no alphanumeric character (e.g. a bare `"..."`) contributes
   nothing meaningful to the joined text regardless of position or
   boundary-truth. This is the direct descendant of the existing "drop a
   segment that's empty after stripping" case in `78105c1`, generalized
   slightly: test on alphanumeric-content rather than deriving emptiness
   from the strip step (since the blanket strip step itself is going away).
2. **Collapse a trailing run of 2+ characters drawn from `.!?` down to the
   last character of that run** (e.g. `".."` -> `"."`, `"?!"` -> `"!"`).
   This directly fixes `78105c1`'s own original motivating example
   (`"deferred until.. you're at the bench"` -> `"deferred until."`)
   without any boundary heuristic: a doubled/malformed punctuation run is
   never correct English regardless of whether the split is real or an
   artifact. The Unicode ellipsis `"…"` (a single codepoint, distinct from
   three literal periods — see the existing comment on
   `_INTERNAL_SEGMENT_TRAILING_PUNCTUATION`) is **not** part of this
   collapse; it's already a single legitimate mark and is left untouched
   whether standalone or adjacent to other punctuation.

Both guards apply to **every** internal segment, including the last one —
there's no reason the call's final segment should get a pass on either
check, since neither depends on the segment's position or on
boundary-truth.

Everything else `78105c1` added is removed:

- No more blanket trailing-punctuation strip on non-last segments (beyond
  the 2-guard above).
- No more decapitalization of non-first segments, and therefore no more
  need for `_leading_token_is_guarded()` (the `"I"`/acronym guard) — delete
  it. This also makes the acronym-damage class of bug ("FRS, GMRS" getting
  decapitalized) moot by construction rather than by a guard function, since
  nothing decapitalizes anything anymore.
- The position-based "last segment is special" framing goes away along with
  it; the two remaining guards don't care about position.

## Composition with existing logic (unchanged)

- The overlap word-trim pass (`fe18641`, `interval_overlap_seconds`) runs
  *before* this join logic in `_transcribe()`, exactly as it does today —
  unaffected. It still produces per-segment `texts`/`trimmed_by_overlap`
  lists; this plan only changes how those per-segment texts get joined and
  which ones get dropped.
- `confidence` computation (`probabilities` / `avg_logprob` averaging)
  already excludes only overlap-dropped segments, not segments dropped by
  the two guards above — no change needed there; it stays scoped to what
  the overlap pass dropped, matching current behavior.
- The `#63` cross-segment `initial_prompt` continuity fix and
  `context_reset_silence_multiplier` long-silence reset in `_emit_segment()`
  are untouched — this plan is scoped entirely to `_transcribe()`'s
  internal-segment join.

## Implementation sketch (historical — superseded by the shipped code)

**Superseded: do not copy the code blocks below.** They are the pre-
implementation sketch, retained for the record. In particular the helper
here is missing the retained-prefix `.rstrip()`, which is exactly the
`"Deferred until .."` -> `"Deferred until ."` defect caught during review;
the constant was also renamed rather than only narrowed. See the Status
section above, and read the shipped
`_collapse_malformed_trailing_punctuation()` in
`faster_whisper_streaming.py` for the real implementation.

Replace the current `raw_texts`/`pieces` loop
(`faster_whisper_streaming.py:528-556`) with:

```python
raw_texts = [text.strip() for text in texts if text.strip()]
pieces: list[str] = []
for raw_text in raw_texts:
    piece = _collapse_malformed_trailing_punctuation(raw_text)
    if not any(char.isalnum() for char in piece):
        continue
    pieces.append(piece)
text = " ".join(pieces).strip()
```

with a new module-level helper (replacing
`_leading_token_is_guarded`, which is deleted):

```python
def _collapse_malformed_trailing_punctuation(text: str) -> str:
    """Collapse a trailing run of 2+ ``.!?`` characters to just the last
    one -- malformed regardless of whether this segment is a real sentence
    boundary or a decoder artifact. The Unicode ellipsis is a single
    legitimate codepoint, not part of this run, and is left untouched."""
    stripped_len = len(text) - len(text.rstrip(".!?"))
    if stripped_len < 2:
        return text
    return text[: len(text) - stripped_len] + text[-1]
```

`_INTERNAL_SEGMENT_TRAILING_PUNCTUATION` (currently `".!?…"`) should be
narrowed to just `".!?"` (drop the ellipsis from it) since its only
remaining use is this collapse, which must never touch `"…"` — or replaced
entirely by inlining `".!?"` in the helper above, whichever reads more
clearly once actually written against the real diff.

This is a sketch for the implementing session, not final code — exact
shape, naming, and edge-case handling (e.g. a segment that's *only* `"…"`,
which the alnum guard already drops correctly since `…` isn't alnum) should
go through the normal `/delegate` pairwrite/pairreview cycle.

## Testing strategy

Synthetic fake-segment fixtures couldn't catch the original over-correction
bug, because they construct segments directly rather than exercising real
decoder output with real pause timing — the ambiguity that broke `78105c1`
only exists in real acoustic data. That doesn't mean synthetic tests are
useless here, though: this fix's two guards are boundary-agnostic by
design, so they don't depend on real pause timing to verify — a synthetic
fixture can assert them directly:

- Two internal segments with real single terminal punctuation
  (`"...sessions."`, `"Next: decide..."`) join with punctuation and
  capitalization **untouched** — this is the direct regression test for the
  bug this fix resolves. Assert the join no longer strips the period or
  lowercases `"Next"`.
- A segment ending in doubled punctuation (`"deferred until.."`) collapses
  to a single mark (`"deferred until."`) — regression test for `78105c1`'s
  original motivating bug, now fixed by the boundary-agnostic guard instead
  of the reverted blanket rule.
- A bare punctuation-only segment (`"..."`, and separately `"…"`) in first,
  middle, and last position is dropped from the joined output.
- An acronym/pronoun-leading segment (`"FRS, GMRS, Corpus"`, `"I'll be
  there"`) passes through with its original capitalization completely
  unmodified — now trivially true since nothing decapitalizes anything, but
  worth a regression test anyway so a future change to this code can't
  silently reintroduce decapitalization without a test failing.
- Existing `interval_overlap_seconds` word-trim tests
  (`tests/test_faster_whisper_streaming.py`) must keep passing unmodified —
  confirms the two changes compose correctly.

Given synthetic tests can't validate the real-world ambiguity this bug
actually turned on, **live hardware dictation validation stays the
acceptance bar** before merging PR #64, same as the other three commits —
specifically, re-run the exact repro that surfaced this bug (a short natural
pause between two sentences within one `endpoint_silence_seconds` window)
and confirm the period/capitalization survive intact, plus a spot-check of
ordinary continuous speech to confirm no new mid-utterance garbling from
removing the blanket rule.

## Next steps

1. `/delegate` (pairwrite/pairreview) to implement this plan against
   `faster_whisper_streaming.py` and its test file, on the `murray` branch,
   same rigor as the other three PR #64 commits.
2. Validate on real sayso hardware per the testing strategy above.
3. Only then merge PR #64 (per the already-agreed sequencing: validate →
   merge → pick up olab_code#65 as its own separate `/grillme`+`/delegate`
   cycle).
