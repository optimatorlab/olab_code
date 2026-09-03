from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from olab_voice.audio.models import AudioFrame
from olab_voice.stt.faster_whisper_streaming import (
    FasterWhisperStreamingTranscriber,
    StreamingBackpressureError,
)


def _transcriber_with_segments(tmp_path, texts):
    """A transcriber whose _transcribe() will see one internal segment per
    text in `texts`, without going through start()/the async pipeline --
    _transcribe() is a plain sync method once _model is set."""
    transcriber = FasterWhisperStreamingTranscriber(tmp_path / "model")
    segments = [SimpleNamespace(text=text, avg_logprob=-0.1) for text in texts]
    transcriber._model = SimpleNamespace(
        transcribe=lambda samples, **kwargs: (iter(segments), SimpleNamespace())
    )
    return transcriber


class _FakeSegment:
    text = " corrected text "
    avg_logprob = -0.25


class _FakeModel:
    def transcribe(self, samples, **kwargs):
        assert len(samples)
        assert kwargs["vad_filter"] is True
        return iter([_FakeSegment()]), SimpleNamespace()


class _RecordingModel:
    """Fake model that echoes each call's initial_prompt into the transcript."""

    def __init__(self):
        self.prompts: list[str | None] = []

    def transcribe(self, samples, **kwargs):
        assert len(samples)
        prompt = kwargs.get("initial_prompt")
        self.prompts.append(prompt)
        text = f"[{prompt}] said the word" if prompt else "said the word"
        segment = SimpleNamespace(text=text, avg_logprob=-0.1)
        return iter([segment]), SimpleNamespace()


def _install_fake_faster_whisper(monkeypatch, model=None):
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: model or _FakeModel()),
    )


class _FakeWord:
    """Minimal stand-in for a Faster-Whisper ``Word``. ``.word`` carries its
    own leading space, matching the actually-installed 1.2.1 tokenizer
    convention confirmed empirically for this task (see
    docs/plan_streaming_interval_overlap.md)."""

    def __init__(self, word: str, end: float, start: float | None = None):
        self.word = word
        self.start = start if start is not None else end
        self.end = end


class _FakeWordSegment:
    def __init__(self, text, words=None, avg_logprob=-0.1):
        self.text = text
        self.words = words
        self.avg_logprob = avg_logprob


class _RecordingWordModel:
    """Fake model returning a fixed list of (possibly word-timestamped)
    segments, recording every call's kwargs for direct-call `_transcribe()`
    tests of the overlap word-trim preprocessing pass."""

    def __init__(self, segments):
        self.segments = segments
        self.calls: list[dict] = []

    def transcribe(self, samples, **kwargs):
        assert len(samples)
        self.calls.append(kwargs)
        return iter(self.segments), SimpleNamespace()


class _OverlapAwareRecordingModel:
    """Like `_RecordingModel`, but also records `word_timestamps` and
    always returns a segment with `words=None` (whole-text-kept path) --
    usable in full-pipeline tests that enable `interval_overlap_seconds`
    without needing to exercise the word-trim algorithm itself (covered
    separately by the direct `_transcribe()` tests above)."""

    def __init__(self):
        self.prompts: list[str | None] = []
        self.word_timestamps_flags: list[bool] = []

    def transcribe(self, samples, **kwargs):
        assert len(samples)
        prompt = kwargs.get("initial_prompt")
        self.prompts.append(prompt)
        self.word_timestamps_flags.append(bool(kwargs.get("word_timestamps")))
        text = f"[{prompt}] said the word" if prompt else "said the word"
        segment = SimpleNamespace(text=text, avg_logprob=-0.1, words=None)
        return iter([segment]), SimpleNamespace()


def test_faster_whisper_streaming_emits_interval_final(monkeypatch, tmp_path):
    _install_fake_faster_whisper(monkeypatch)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=0.005,
            endpoint_silence_seconds=1.0,
        )
        await transcriber.start()
        stream = transcriber.events()
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        event = await anext(stream)
        await transcriber.stop()
        return event

    event = asyncio.run(run())

    assert event.text == "corrected text"
    assert event.type == "transcript.interval_final"
    assert event.engine == "faster_whisper"
    assert event.segment_id == "service:1"
    assert event.capture_start_time == 10.0
    assert event.capture_end_time == pytest.approx(10.01)
    assert event.confidence == -0.25


def test_faster_whisper_streaming_reports_queue_backpressure(monkeypatch, tmp_path):
    _install_fake_faster_whisper(monkeypatch)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(model_path, max_queued_frames=1)
        await transcriber.start()
        await transcriber.submit_frame(AudioFrame(data=b"\x00\x00" * 160, seq=1))
        with pytest.raises(StreamingBackpressureError):
            await transcriber.submit_frame(AudioFrame(data=b"\x00\x00" * 160, seq=2))
        await transcriber.stop(flush=False)

    asyncio.run(run())


def test_interval_final_carries_prior_text_as_initial_prompt(monkeypatch, tmp_path):
    """A mid-utterance interval split should give Faster-Whisper the tail of the
    previous chunk as context, since that's what prevents it from re-decoding
    (and stuttering) the first word of the new chunk in isolation. This is a
    regression check: interval_final carried context before olab_code#63's
    fix too, and must continue to under the "always carry" default."""
    model = _RecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=0.005,
            endpoint_silence_seconds=100.0,
        )
        await transcriber.start()
        stream = transcriber.events()
        for _ in range(2):
            await transcriber.submit_frame(
                AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
            )
        events = [await anext(stream), await anext(stream)]
        await transcriber.stop(flush=False)
        return events

    events = asyncio.run(run())

    assert model.prompts[0] is None
    assert model.prompts[1] == "said the word"
    assert events[0].text == "said the word"
    assert events[0].type == "transcript.interval_final"
    assert events[1].text == "[said the word] said the word"
    assert events[1].type == "transcript.interval_final"


def test_segment_final_carries_context_across_a_short_pause(monkeypatch, tmp_path):
    """olab_code#63: a segment_final split (a short, natural inter-clause
    breath at typical endpoint_silence_seconds) should still carry context
    into the next segment by default — this is the change from the
    now-rejected f6b375a policy, which never carried across segment_final."""
    model = _RecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=100.0,
            endpoint_silence_seconds=0.005,
            context_reset_silence_multiplier=4.0,
        )
        await transcriber.start()
        stream = transcriber.events()
        # First utterance, ended by a short endpoint-silence gap.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=10.01)
        )
        first = await anext(stream)
        # Second utterance, well within the reset threshold
        # (0.005 * 4.0 = 0.02s) of the first segment's last speech. Three
        # consecutive speech frames (not just one) so this segment has real
        # duration — this is what makes the gap computation's anchor matter:
        # using this segment's *first*-speech time (10.015, correct) keeps
        # the gap at 0.005 and carries; using its *last*-speech time
        # (10.045, the round-1 finding-1(b) bug) would inflate the gap to
        # 0.035 and wrongly reset.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.015)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.025)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.035)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=10.045)
        )
        second = await anext(stream)
        await transcriber.stop(flush=False)
        return first, second

    first, second = asyncio.run(run())

    assert first.type == "transcript.segment_final"
    assert second.type == "transcript.segment_final"
    assert model.prompts == [None, "said the word"]
    assert second.text == "[said the word] said the word"


def test_segment_final_resets_context_after_a_long_silence(monkeypatch, tmp_path):
    """A gap much longer than endpoint_silence_seconds (scaled by
    context_reset_silence_multiplier) should discard the carried context
    rather than bias an unrelated new utterance. This also covers the
    empty-transcription interaction found in review (round-3 finding 1): a
    speech-gated segment that transcribes to empty text (a cough/click right
    after the long pause) must not resurrect the stale pre-gap context for
    the segment after it — the reset has to clear the stored context
    outright, not just suppress it for the one call it fires on."""

    class _RealThenEmptyThenRealModel:
        """First call returns real text; second call (the post-gap
        cough/click) returns empty text; third call returns real text
        again, echoing whatever prompt it was given."""

        def __init__(self):
            self.calls = 0
            self.prompts: list[str | None] = []

        def transcribe(self, samples, **kwargs):
            prompt = kwargs.get("initial_prompt")
            self.prompts.append(prompt)
            self.calls += 1
            if self.calls == 2:
                return iter([]), SimpleNamespace()
            text = f"[{prompt}] said the word" if prompt else "said the word"
            segment = SimpleNamespace(text=text, avg_logprob=-0.1)
            return iter([segment]), SimpleNamespace()

    model = _RealThenEmptyThenRealModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=100.0,
            endpoint_silence_seconds=0.005,
            context_reset_silence_multiplier=4.0,
        )
        await transcriber.start()
        stream = transcriber.events()
        # First utterance, ended by a short endpoint-silence gap.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=10.01)
        )
        first = await anext(stream)
        # A cough/click well past the reset threshold (0.02s) that clears
        # the dB gate but transcribes to empty text (no event is emitted for
        # it, since _emit_segment returns before publishing on empty text).
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=25.0)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=25.01)
        )
        # Real speech shortly after the cough — should NOT inherit the
        # pre-gap context, since the reset already discarded it outright.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=25.015)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=25.025)
        )
        second = await anext(stream)
        await transcriber.stop(flush=False)
        return first, second

    first, second = asyncio.run(run())

    assert first.type == "transcript.segment_final"
    assert second.type == "transcript.segment_final"
    # Calls: 1st segment (no prompt), 2nd/cough segment (reset -> no
    # prompt), 3rd segment (reset already cleared _next_initial_prompt, so
    # still no prompt — this is what a broken suppress-only reset would get
    # wrong, since it would hand the 3rd call "said the word").
    assert model.prompts == [None, None, None]
    assert second.text == "said the word"


def test_empty_transcription_preserves_carried_context(monkeypatch, tmp_path):
    """A segment that clears the dB gate but transcribes to empty text (a
    breath/click/cough) must not wipe out the context carried from an
    earlier real segment, within a short (non-reset-triggering) gap."""

    class _EmptyThenRecordingModel:
        def __init__(self):
            self.calls = 0
            self.prompts: list[str | None] = []

        def transcribe(self, samples, **kwargs):
            prompt = kwargs.get("initial_prompt")
            self.prompts.append(prompt)
            self.calls += 1
            if self.calls == 2:
                # The middle segment transcribes to nothing.
                return iter([]), SimpleNamespace()
            text = f"[{prompt}] said the word" if prompt else "said the word"
            segment = SimpleNamespace(text=text, avg_logprob=-0.1)
            return iter([segment]), SimpleNamespace()

    model = _EmptyThenRecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=100.0,
            endpoint_silence_seconds=0.005,
            context_reset_silence_multiplier=4.0,
        )
        await transcriber.start()
        stream = transcriber.events()
        # First (real) segment.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=10.01)
        )
        first = await anext(stream)
        # Second segment: speech-gated but transcribes to empty text.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.015)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=10.025)
        )
        # Third (real) segment, still within the reset threshold of the
        # *second* segment's own last-speech time (10.025 + 0.02 = 0.045),
        # but far enough past the *first* segment's last-speech time
        # (10.01 + 0.02 = 0.03) to distinguish a correctly-rolled-forward
        # anchor from a stale one: if `_last_speech_heard_time` were only
        # updated on non-empty text (round-1 finding-2b bug), the second
        # (empty-text) segment would never have advanced it past 10.01, and
        # this segment's gap would wrongly exceed the reset threshold.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.04)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=10.05)
        )
        third = await anext(stream)
        await transcriber.stop(flush=False)
        return first, third

    first, third = asyncio.run(run())

    assert model.prompts == [None, "said the word", "said the word"]
    assert first.text == "said the word"
    assert third.text == "[said the word] said the word"


def test_update_tuning_rejects_bad_values_and_leaves_state_unmodified(monkeypatch, tmp_path):
    _install_fake_faster_whisper(monkeypatch)
    model_path = tmp_path / "model"
    model_path.mkdir()
    transcriber = FasterWhisperStreamingTranscriber(model_path)

    for kwargs in (
        {"target_interval_seconds": 0},
        {"target_interval_seconds": -1.0},
        {"target_interval_seconds": float("nan")},
        {"target_interval_seconds": float("inf")},
        {"endpoint_silence_seconds": 0},
        {"endpoint_silence_seconds": -1.0},
        {"endpoint_silence_seconds": float("nan")},
        {"endpoint_silence_seconds": float("inf")},
        {"context_reset_silence_multiplier": 0},
        {"context_reset_silence_multiplier": -1.0},
        {"context_reset_silence_multiplier": float("nan")},
        {"context_reset_silence_multiplier": float("inf")},
    ):
        with pytest.raises(ValueError):
            transcriber.update_tuning(**kwargs)

    # Atomicity: a call mixing one good and one bad value must not partially
    # apply the good one.
    with pytest.raises(ValueError):
        transcriber.update_tuning(target_interval_seconds=5.0, endpoint_silence_seconds=-1)
    assert transcriber.target_interval_seconds == 4.0
    assert transcriber.endpoint_silence_seconds == 0.8
    assert transcriber.context_reset_silence_multiplier == 4.0


def test_update_tuning_takes_effect_on_the_next_frame(monkeypatch, tmp_path):
    """A live update should change chunking behavior without reconstructing
    the transcriber."""
    _install_fake_faster_whisper(monkeypatch)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=100.0,
            endpoint_silence_seconds=100.0,
        )
        await transcriber.start()
        stream = transcriber.events()
        # With the current (loose) tuning, this frame should not trigger a
        # segment on its own.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        transcriber.update_tuning(endpoint_silence_seconds=0.005)
        # A silence frame should now trigger transcript.segment_final under
        # the updated (tight) endpoint_silence_seconds.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=10.01)
        )
        event = await anext(stream)
        await transcriber.stop(flush=False)
        return event

    event = asyncio.run(run())
    assert event.type == "transcript.segment_final"


def test_transcribe_strips_internal_segment_join_artifacts(tmp_path):
    """olab_code: Faster-Whisper's own internal VAD can split one
    _transcribe() call's buffer into multiple internal segments (common with
    a longer buffer). Each gets independent capitalization/terminal
    punctuation from the decoder, which a plain join bakes in as spurious
    mid-utterance artifacts -- e.g. real sayso examples "deferred until..
    you're at the bench" and "...FRS, GMRS, Corpus mentioned...". Every
    segment except the last should have its full trailing .!?... run
    stripped (not just one char -- a single-char strip does not fully
    resolve "deferred until.."), and every segment except the first should
    have its leading letter decapitalized -- but the last segment's own
    trailing punctuation, and the first segment's own leading capital, are
    real and must survive untouched."""
    transcriber = _transcriber_with_segments(
        tmp_path,
        [
            # Interior trailing whitespace after the punctuation run --
            # rstrip() alone would leave "Deferred until " (regression check
            # for the re-.strip() step, not just the punctuation strip;
            # verified against the reported bug's exact "deferred until.."
            # example, plus a leading space before the punctuation run).
            "Deferred until ..",
            # Unicode ellipsis (Whisper emits this codepoint, not "...").
            "you're at the bench…",
            # Acronym list -- the guard must leave this alone.
            "ADS-B, AIS, FRS, GMRS,",
            # The last segment: not stripped (no trailing punctuation here
            # to strip), but still decapitalized like any non-first
            # segment -- this is the plan's other reported bug example.
            "Corpus mentioned in the issue",
        ],
    )
    text, _confidence = transcriber._transcribe(b"\x00\x10" * 160, initial_prompt=None)
    assert (
        text
        == "Deferred until you're at the bench ADS-B, AIS, FRS, GMRS, corpus mentioned in the issue"
    )
    assert "  " not in text


def test_transcribe_preserves_a_last_segment_that_strips_to_empty_raw_text(tmp_path):
    """A raw trailing segment that strips to empty text (e.g. Faster-Whisper
    emitting a blank/whitespace-only segment right at the end of a call)
    must not be mistaken for "the real last segment" -- the real last
    non-empty segment must still keep its own trailing punctuation."""
    transcriber = _transcriber_with_segments(
        tmp_path, ["Copy that.", "we can proceed.", "   "]
    )
    text, _confidence = transcriber._transcribe(b"\x00\x10" * 160, initial_prompt=None)
    assert text == "Copy that we can proceed."


def test_transcribe_decap_guard_does_not_crash_on_non_alpha_leading_char(tmp_path):
    """A non-alphabetic leading character (a digit or an opening quote) must
    not crash the decapitalization guard."""
    transcriber = _transcriber_with_segments(
        tmp_path, ["First,", '"42 degrees, hold."', "9 o'clock now"]
    )
    text, _confidence = transcriber._transcribe(b"\x00\x10" * 160, initial_prompt=None)
    assert text == 'First, "42 degrees, hold." 9 o\'clock now'


def test_transcribe_drops_a_punctuation_only_internal_segment(tmp_path):
    """A middle internal segment that is only punctuation (Faster-Whisper
    emits bare "..." segments for near-silence under vad_filter=True -- the
    exact regime this fix targets) must be dropped entirely, not crash on an
    empty string during decapitalization and not leave a stray double space
    in the join."""
    transcriber = _transcriber_with_segments(tmp_path, ["Okay", "...", "we can start"])
    text, _confidence = transcriber._transcribe(b"\x00\x10" * 160, initial_prompt=None)
    assert text == "Okay we can start"
    assert "  " not in text


def test_transcribe_decap_guard_skips_pronoun_i_and_acronyms(tmp_path):
    """The decapitalization guard must not lowercase a leading "I"/"I'll"
    pronoun or a multi-char all-uppercase acronym token -- both are
    legitimately capitalized mid-utterance, and blindly decapitalizing them
    would replace one artifact with a worse one (this is exactly the
    plan's own motivating acronym-list example: "ADS-B, AIS, FRS, GMRS,
    ..."). An ordinary lowercase-eligible continuation in the same call
    still gets decapitalized, confirming the guard is scoped rather than a
    blanket skip."""
    transcriber = _transcriber_with_segments(
        tmp_path,
        ["Copy that.", "I'll check the feed.", "ADS-B, AIS,", "FRS, GMRS", "Corpus mentioned it"],
    )
    text, _confidence = transcriber._transcribe(b"\x00\x10" * 160, initial_prompt=None)
    assert text == "Copy that I'll check the feed ADS-B, AIS, FRS, GMRS corpus mentioned it"


# --- olab_code: audio continuity across interval_final cuts (overlap) ------


def test_reset_segment_retains_trailing_frames_on_interval_final(tmp_path):
    """Frame retention is whole-frame granularity: walk backward
    accumulating frame durations until the running total is >=
    interval_overlap_seconds, keep that suffix. The next segment's buffer
    (`self._frames`) must include exactly those retained frames, and the
    *actual* retained duration (which may exceed the configured value) is
    tracked precisely in `_pending_overlap_seconds`."""
    transcriber = FasterWhisperStreamingTranscriber(
        tmp_path / "model", interval_overlap_seconds=0.02
    )
    frames = [
        AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0 + i * 0.01)
        for i in range(5)
    ]
    transcriber._frames = list(frames)

    transcriber._reset_segment(retain_overlap=True)

    # Each frame is 0.01s; 0.02s configured overlap needs 2 whole frames.
    assert transcriber._frames == frames[-2:]
    assert transcriber._pending_overlap_seconds == pytest.approx(0.02)
    # Neither is replay-seeded -- only set by a genuinely new frame later.
    assert transcriber._new_content_start_time is None
    assert transcriber._segment_first_speech_time is None


def test_reset_segment_full_discard_when_not_retaining(tmp_path):
    """`segment_final` and `stop(flush=False)` always fully discard,
    regardless of `interval_overlap_seconds` -- the default `retain_overlap
    =False` behavior, unchanged from today."""
    transcriber = FasterWhisperStreamingTranscriber(
        tmp_path / "model", interval_overlap_seconds=0.02
    )
    transcriber._frames = [
        AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
    ]
    transcriber._last_speech_end_time = 10.01

    transcriber._reset_segment()

    assert transcriber._frames == []
    assert transcriber._pending_overlap_seconds == 0.0
    assert transcriber._last_speech_end_time is None


def test_overlap_replay_does_not_contaminate_segment_first_speech_time(tmp_path):
    """#63's long-silence context reset computes
    `gap = segment_first_speech_time - previous_speech_heard_time`. If
    overlap replay set `_segment_first_speech_time` from the retained
    tail's own (already-accounted-for) speech, that gap would come out at
    or near zero on every overlap-seeded segment regardless of how long a
    real silence follows -- silently defeating the #63 reset. Confirm the
    retained tail's speech seeds `_last_speech_end_time` (needed for
    endpoint-silence detection) but leaves `_segment_first_speech_time`
    untouched."""
    transcriber = FasterWhisperStreamingTranscriber(
        tmp_path / "model", interval_overlap_seconds=0.01
    )
    speech_frame = AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
    transcriber._frames = [speech_frame]

    transcriber._reset_segment(retain_overlap=True)

    assert transcriber._frames == [speech_frame]
    assert transcriber._last_speech_end_time == pytest.approx(10.01)
    assert transcriber._segment_first_speech_time is None
    assert transcriber._new_content_start_time is None


def test_transcribe_word_trim_drops_overlap_and_keeps_boundary_word(tmp_path):
    """Word-level trim: a word entirely before the cutoff is dropped; a
    word straddling or past it is kept (keep-bias -- risk a visible,
    correctable duplicate over a silent, invisible loss); a segment with no
    word-level breakdown at all falls back to keeping its whole text
    (`segment.words` falsy); and confidence excludes only a segment the
    overlap trim dropped entirely, while a pre-existing punctuation-only
    segment (PR #64) still contributes to it exactly as before."""
    transcriber = FasterWhisperStreamingTranscriber(tmp_path / "model")
    segments = [
        # Entirely before cutoff (1.0s) -- dropped whole. A wildly bad
        # avg_logprob here would badly skew confidence if wrongly included.
        _FakeWordSegment(
            "Session can",
            words=[_FakeWord(" Session", end=0.4), _FakeWord(" can", end=0.8)],
            avg_logprob=-9.0,
        ),
        # Straddles the cutoff -- the boundary word (" produce") is kept.
        _FakeWordSegment(
            "produce one file",
            words=[
                _FakeWord(" produce", end=1.2),
                _FakeWord(" one", end=1.5),
                _FakeWord(" file", end=1.8),
            ],
            avg_logprob=-0.2,
        ),
        # Pre-existing punctuation-only *internal* segment (PR #64's
        # existing handling, unrelated to overlap -- must be placed before
        # the real final segment, or it becomes "the last segment" whose
        # trailing punctuation PR #64 legitimately preserves instead of
        # dropping). Must still contribute to confidence exactly as it does
        # today.
        _FakeWordSegment("...", words=[], avg_logprob=-5.0),
        # No word-level breakdown (words=None) -- keep-bias applies to the
        # whole segment rather than defaulting to drop. This is the real
        # final segment of the call.
        _FakeWordSegment("per test session.", words=None, avg_logprob=-0.3),
    ]
    model = _RecordingWordModel(segments)
    transcriber._model = model

    text, confidence = transcriber._transcribe(
        b"\x00\x10" * 160, initial_prompt=None, overlap_cutoff_seconds=1.0
    )

    assert model.calls[-1]["word_timestamps"] is True
    assert text == "produce one file per test session."
    # -9.0 excluded (dropped by the overlap trim); -5.0 (punctuation-only,
    # PR #64's pre-existing, unrelated handling) still included, unchanged.
    assert confidence == pytest.approx((-0.2 + -0.3 + -5.0) / 3)


def test_transcribe_word_trim_boundary_is_strict_less_or_equal(tmp_path):
    """`word.end <= cutoff` drops (already fully covered by the retained
    overlap); `word.end > cutoff` keeps, even by a hair -- implements the
    keep-bias consistently at the exact boundary, and exercises the
    partial-segment reconstruction join (`"".join(...)`, not
    `" ".join(...)` -- each `Word.word` carries its own leading space)."""
    transcriber = FasterWhisperStreamingTranscriber(tmp_path / "model")
    segment = _FakeWordSegment(
        "one two",
        words=[_FakeWord(" one", end=1.0), _FakeWord(" two", end=1.01)],
    )
    model = _RecordingWordModel([segment])
    transcriber._model = model

    text, _confidence = transcriber._transcribe(
        b"\x00\x10" * 160, initial_prompt=None, overlap_cutoff_seconds=1.0
    )
    assert text == "two"


def test_transcribe_no_word_timestamps_when_call_has_no_overlap(tmp_path):
    """`word_timestamps` is a per-call runtime check
    (`overlap_cutoff_seconds > 0` for *that* call), not a static
    `self.interval_overlap_seconds` config check -- the first segment after
    `start()` and any segment after a `segment_final` must pay zero extra
    decode cost even when overlap is configured on."""
    transcriber = FasterWhisperStreamingTranscriber(tmp_path / "model")
    model = _RecordingWordModel([_FakeWordSegment("Copy that.", words=None)])
    transcriber._model = model

    text, _confidence = transcriber._transcribe(b"\x00\x10" * 160, initial_prompt=None)

    assert model.calls[-1]["word_timestamps"] is False
    assert text == "Copy that."


def test_capture_times_stay_contiguous_across_overlap_carrying_boundary(monkeypatch, tmp_path):
    """Consecutive events' capture_start_time/capture_end_time must stay
    contiguous (non-overlapping) across an overlap-carrying boundary --
    `capture_start_time` uses `_new_content_start_time` (new content only),
    not the physical buffer start, which would double-count the retained
    overlap window across two events."""
    model = _OverlapAwareRecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            # Frame duration (1000 samples / 16000 Hz = 0.0625s = 1/16) and
            # the tuning knobs below are all exact powers of two, so summed
            # timestamps stay bit-exact -- avoiding float-drift false
            # negatives/positives on the `>=` trigger comparisons.
            target_interval_seconds=0.125,
            endpoint_silence_seconds=100.0,
            interval_overlap_seconds=0.0625,
        )
        await transcriber.start()
        stream = transcriber.events()
        for i in range(4):
            await transcriber.submit_frame(
                AudioFrame(
                    data=b"\x00\x10" * 1000,
                    session_id="service",
                    timestamp=10.0 + i * 0.0625,
                )
            )
        events = [await anext(stream), await anext(stream)]
        await transcriber.stop(flush=False)
        return events

    first, second = asyncio.run(run())

    assert first.type == "transcript.interval_final"
    assert second.type == "transcript.interval_final"
    assert second.capture_start_time == pytest.approx(first.capture_end_time)


def test_initial_prompt_suppressed_on_overlap_carrying_calls(monkeypatch, tmp_path):
    """An overlap-carrying call must not receive #63's carried
    `initial_prompt`, even though `_next_initial_prompt` has a value ready
    -- the old segment's own (possibly garbled) trailing text must not bias
    re-decoding of the same audio. A non-overlap call is unaffected
    (regression check against #63's own tests)."""
    model = _OverlapAwareRecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=0.005,
            endpoint_silence_seconds=100.0,
            interval_overlap_seconds=0.001,
        )
        await transcriber.start()
        stream = transcriber.events()
        for _ in range(2):
            await transcriber.submit_frame(
                AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
            )
        events = [await anext(stream), await anext(stream)]
        await transcriber.stop(flush=False)
        return events

    events = asyncio.run(run())

    assert events[0].type == "transcript.interval_final"
    assert events[1].type == "transcript.interval_final"
    # First call carries no overlap (fresh start) -- #63's normal carry
    # applies (nothing carried yet, so None either way here). Second call
    # carries overlap from the first -- initial_prompt is force-suppressed
    # even though _next_initial_prompt has "said the word" ready.
    assert model.prompts == [None, None]


def test_interval_overlap_seconds_validation(tmp_path):
    model_path = tmp_path / "model"

    with pytest.raises(ValueError):
        FasterWhisperStreamingTranscriber(model_path, interval_overlap_seconds=-0.1)
    with pytest.raises(ValueError):
        FasterWhisperStreamingTranscriber(model_path, interval_overlap_seconds=float("nan"))
    with pytest.raises(ValueError):
        FasterWhisperStreamingTranscriber(model_path, interval_overlap_seconds=float("inf"))
    # Cross-field: overlap must be strictly less than target_interval_seconds.
    with pytest.raises(ValueError):
        FasterWhisperStreamingTranscriber(
            model_path, target_interval_seconds=1.0, interval_overlap_seconds=1.0
        )
    with pytest.raises(ValueError):
        FasterWhisperStreamingTranscriber(
            model_path, target_interval_seconds=1.0, interval_overlap_seconds=1.5
        )
    # 0.0 (the disabled sentinel) is valid regardless of target_interval_seconds.
    transcriber = FasterWhisperStreamingTranscriber(model_path, interval_overlap_seconds=0.0)
    assert transcriber.interval_overlap_seconds == 0.0


def test_update_tuning_interval_overlap_cross_field_validation(tmp_path):
    """`interval_overlap_seconds < target_interval_seconds` is a
    cross-field rule: if a caller updates only one of the two fields,
    validation must check the *resulting combination* (new value where
    provided, existing field value otherwise), not just the field being
    changed in isolation."""
    model_path = tmp_path / "model"
    transcriber = FasterWhisperStreamingTranscriber(
        model_path, target_interval_seconds=1.0, interval_overlap_seconds=0.5
    )

    # 0.1 alone passes target_interval_seconds's own `> 0` check, but must
    # still be rejected against the *existing* interval_overlap_seconds.
    with pytest.raises(ValueError):
        transcriber.update_tuning(target_interval_seconds=0.1)
    assert transcriber.target_interval_seconds == 1.0
    assert transcriber.interval_overlap_seconds == 0.5

    # Raising interval_overlap_seconds alone must be rejected against the
    # existing target_interval_seconds.
    with pytest.raises(ValueError):
        transcriber.update_tuning(interval_overlap_seconds=1.0)
    assert transcriber.interval_overlap_seconds == 0.5

    with pytest.raises(ValueError):
        transcriber.update_tuning(interval_overlap_seconds=-1.0)
    with pytest.raises(ValueError):
        transcriber.update_tuning(interval_overlap_seconds=float("nan"))

    # A valid combination of both fields together succeeds.
    transcriber.update_tuning(target_interval_seconds=2.0, interval_overlap_seconds=1.0)
    assert transcriber.target_interval_seconds == 2.0
    assert transcriber.interval_overlap_seconds == 1.0


def test_segment_final_never_retains_overlap(monkeypatch, tmp_path):
    """The interval_overlap_seconds mechanism is scoped to interval_final
    boundaries only -- a segment_final (real silence detected) must always
    fully discard, never retain, regardless of configuration. Regression
    check that the scope restriction actually holds in code."""
    model = _OverlapAwareRecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=100.0,
            endpoint_silence_seconds=0.005,
            interval_overlap_seconds=0.01,
        )
        await transcriber.start()
        stream = transcriber.events()
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=10.01)
        )
        event = await anext(stream)
        await transcriber.stop(flush=False)
        return transcriber, event

    transcriber, event = asyncio.run(run())

    assert event.type == "transcript.segment_final"
    assert model.word_timestamps_flags == [False]
    assert transcriber._frames == []
    assert transcriber._pending_overlap_seconds == 0.0


def test_overlap_seeded_segment_with_prior_speech_does_not_crash(monkeypatch, tmp_path):
    """An overlap-seeded segment whose retained tail had speech, followed
    by genuinely new speech, must reach `_emit_segment` without raising --
    under the pre-fix implementation (gating `_segment_first_speech_time`
    on `_last_speech_end_time is None`, which overlap replay had already
    made False) this is a `TypeError` in the #63 gap computation that kills
    the whole worker."""
    model = _OverlapAwareRecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            # A comfortable margin between frame duration (0.0625s) and
            # target_interval_seconds (0.05s), rather than an exact-equality
            # boundary, keeps the trigger immune to float rounding.
            target_interval_seconds=0.05,
            endpoint_silence_seconds=100.0,
            interval_overlap_seconds=0.03,
        )
        await transcriber.start()
        stream = transcriber.events()
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 1000, session_id="service", timestamp=10.0)
        )
        first = await anext(stream)
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 1000, session_id="service", timestamp=10.0625)
        )
        second = await anext(stream)
        await transcriber.stop(flush=False)
        return [first, second]

    events = asyncio.run(asyncio.wait_for(run(), timeout=5.0))

    assert [event.type for event in events] == [
        "transcript.interval_final",
        "transcript.interval_final",
    ]


def test_interval_final_then_stop_flush_true_emits_no_duplicate(monkeypatch, tmp_path):
    """`stop(flush=True)` immediately after an `interval_final`, with zero
    new frames submitted in between, must not re-emit the retained overlap
    as a duplicate event -- the buffer holds nothing but never-consumed
    retained overlap at that point."""
    model = _OverlapAwareRecordingModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=0.005,
            endpoint_silence_seconds=100.0,
            interval_overlap_seconds=0.001,
        )
        await transcriber.start()
        stream = transcriber.events()
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        first = await anext(stream)
        await transcriber.stop(flush=True)
        remaining = [event async for event in stream]
        return first, remaining

    first, remaining = asyncio.run(asyncio.wait_for(run(), timeout=5.0))

    assert first.type == "transcript.interval_final"
    assert remaining == []
    assert len(model.prompts) == 1


def test_consecutive_interval_final_boundaries_use_each_calls_own_cutoff(monkeypatch, tmp_path):
    """Two *consecutive* interval_final boundaries (overlap-on-overlap --
    the production steady state): each call's word-trim cutoff must
    reflect that call's own retention, not a value left over from the
    other boundary. `_pending_overlap_seconds` is captured into a local
    before `_reset_segment()` runs again and overwrites it for the *next*
    segment -- an ordering bug reading the post-reset value would show up
    only on the second boundary, while a single-boundary test would still
    pass."""

    class _CutoffProbeModel:
        """For any word_timestamps=True call, always returns one segment
        with a discriminator word (end=0.8s) and an anchor word (end=2.0s,
        always kept regardless of cutoff) -- so which words survive the
        trim directly reveals which cutoff value _transcribe() actually
        used for that call. The two boundaries below produce genuinely
        different actual cutoffs (0.6s, then 1.0s), each with a comfortable
        margin either side of the discriminator's 0.8s -- deliberately not
        an exact-equality boundary, to keep this test's own trigger/cutoff
        math immune to float rounding."""

        def __init__(self):
            self.calls = 0

        def transcribe(self, samples, **kwargs):
            self.calls += 1
            if not kwargs.get("word_timestamps"):
                segment = SimpleNamespace(text="seed", avg_logprob=-0.1, words=None)
            else:
                words = [_FakeWord(" boundary", end=0.8), _FakeWord(" anchor", end=2.0)]
                segment = SimpleNamespace(text=" boundary anchor", avg_logprob=-0.1, words=words)
            return iter([segment]), SimpleNamespace()

    model = _CutoffProbeModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=0.5,
            endpoint_silence_seconds=1000.0,
            interval_overlap_seconds=0.1,
        )
        await transcriber.start()
        stream = transcriber.events()
        # Segment 1: a single 0.6s frame -> interval_final (comfortably
        # over the 0.5s target), no incoming overlap (fresh start). Retains
        # the *whole* frame (0.6s -- whole-frame granularity can't split
        # below the configured 0.1s), so segment 2's own incoming overlap
        # is 0.6s, not the configured 0.1s.
        await transcriber.submit_frame(
            AudioFrame(
                data=b"\x00\x10" * (16000 * 6 // 10),
                session_id="service",
                timestamp=10.0,
            )
        )
        first = await anext(stream)
        # Segment 2: one new 1.0s frame -> interval_final. Its own call
        # must use the 0.6s cutoff carried from segment 1's retention --
        # not the 1.0s segment 2 is *about* to retain for segment 3.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 16000, session_id="service", timestamp=10.6)
        )
        second = await anext(stream)
        # Segment 3: one new 1.0s frame -> interval_final. Its own call
        # must use the 1.0s cutoff segment 2 just retained -- not a stale
        # 0.6s left over from the first boundary.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 16000, session_id="service", timestamp=11.6)
        )
        third = await anext(stream)
        await transcriber.stop(flush=False)
        return first, second, third

    first, second, third = asyncio.run(asyncio.wait_for(run(), timeout=5.0))

    assert first.text == "seed"
    # cutoff=0.6 (carried from segment 1's full-frame retention): the
    # discriminator word ending at 0.8 is > cutoff -> kept, so both survive.
    assert second.text == "boundary anchor"
    # cutoff=1.0 (segment 2's own retention): the same discriminator word
    # now ends *before* cutoff -> dropped. A B3-style ordering bug (reading
    # the post-reset value, i.e. reusing 0.6) would wrongly keep it and
    # produce "boundary anchor" here too instead of just "anchor".
    assert third.text == "anchor"


def test_overlap_carrying_segment_with_zero_new_speech_publishes_no_event(monkeypatch, tmp_path):
    """An overlap-carrying segment with zero genuinely-new speech (the
    retained tail replay-seeds `_last_speech_end_time`, triggering
    `segment_final` via the endpoint-silence check, but no new frame in
    this segment is itself speech) must publish no event at all -- even
    though the fake model, if it were ever called, would return a word
    that survives the keep-bias trim. This distinguishes the `has_speech`
    fix from the earlier ("the trim reduces it to empty text anyway")
    argument that round 3 review showed was unsound: a test that only
    checked "text came back empty" would pass under both, so this asserts
    on whether the model was ever called at all."""

    class _WouldKeepWordModel:
        def __init__(self):
            self.calls: list[dict] = []

        def transcribe(self, samples, **kwargs):
            self.calls.append(kwargs)
            word = _FakeWord(" duplicate", end=999.0)
            segment = SimpleNamespace(text=" duplicate", avg_logprob=-0.1, words=[word])
            return iter([segment]), SimpleNamespace()

    model = _WouldKeepWordModel()
    _install_fake_faster_whisper(monkeypatch, model=model)
    model_path = tmp_path / "model"
    model_path.mkdir()

    async def run():
        transcriber = FasterWhisperStreamingTranscriber(
            model_path,
            target_interval_seconds=0.005,
            endpoint_silence_seconds=5.0,
            interval_overlap_seconds=0.001,
        )
        await transcriber.start()
        stream = transcriber.events()
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x10" * 160, session_id="service", timestamp=10.0)
        )
        first = await anext(stream)
        # Widen the interval timer so segment 2 can only end via the
        # endpoint-silence path below, isolating that trigger.
        transcriber.update_tuning(target_interval_seconds=100.0)
        # A lone new silence frame, far enough past the retained tail's
        # replayed _last_speech_end_time to trip the endpoint-silence
        # check -- this segment observes zero genuinely new speech.
        await transcriber.submit_frame(
            AudioFrame(data=b"\x00\x00" * 160, session_id="service", timestamp=25.0)
        )
        await transcriber.stop(flush=False)
        remaining = [event async for event in stream]
        return first, remaining

    first, remaining = asyncio.run(asyncio.wait_for(run(), timeout=5.0))

    assert first.type == "transcript.interval_final"
    assert remaining == []
    # The zero-new-speech segment never reached _transcribe() at all.
    assert len(model.calls) == 1
