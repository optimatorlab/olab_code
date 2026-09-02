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
