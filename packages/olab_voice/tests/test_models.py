from olab_voice.audio.models import AudioBlob, AudioFrame
from olab_voice.stt.base import TranscriptEvent, TranscriptSegment
from olab_voice.tts.base import TtsAudio, TtsRequest


def test_audio_blob_defaults():
    blob = AudioBlob(data=b"abc", format="audio/wav", source="browser", user_id=7, asset_id=107)

    assert blob.data == b"abc"
    assert blob.format == "audio/wav"
    assert blob.source == "browser"
    assert blob.user_id == 7
    assert blob.asset_id == 107
    assert blob.session_id


def test_audio_blob_round_trip():
    blob = AudioBlob(
        data=b"abc",
        format="audio/webm;codecs=opus",
        source="browser",
        user_id=7,
        asset_id=107,
        sample_rate=48000,
        channels=1,
    )

    restored = AudioBlob.from_dict(blob.to_dict())

    assert restored == blob


def test_audio_frame_defaults():
    frame = AudioFrame(data=b"\x00\x00", seq=1)

    assert frame.format == "pcm_s16le"
    assert frame.sample_rate == 16000
    assert frame.channels == 1
    assert frame.seq == 1


def test_audio_frame_round_trip():
    frame = AudioFrame(data=b"\x00\x00", seq=5, source="python_mic")

    restored = AudioFrame.from_dict(frame.to_dict())

    assert restored == frame


def test_transcript_event_defaults():
    event = TranscriptEvent(text="hey 107 take off", user_id=7, asset_id=107)

    assert event.type == "transcript.segment_final"
    assert event.text == "hey 107 take off"
    assert event.segments == []


def test_transcript_event_round_trip():
    event = TranscriptEvent(
        text="hey 107 take off",
        session_id="session-1",
        user_id=7,
        asset_id=107,
        confidence=-0.2,
        start_time=0.1,
        end_time=1.5,
    )

    restored = TranscriptEvent.from_dict(event.to_dict())

    assert restored == event


def test_transcript_event_streaming_fields_round_trip():
    event = TranscriptEvent(
        text="corrected transcript",
        type="transcript.segment_final",
        segment_id="service-1:42",
        revision=2,
        engine="faster_whisper",
        is_fallback=False,
        capture_start_time=100.25,
        capture_end_time=103.75,
        metadata={"preset": "balanced", "replaces_engine": "vosk"},
    )

    restored = TranscriptEvent.from_dict(event.to_dict())

    assert restored == event


def test_transcript_segment_round_trip():
    segment = TranscriptSegment(text="hey 107", start_time=0.0, end_time=0.8)

    restored = TranscriptSegment.from_dict(segment.to_dict())

    assert restored == segment
    assert restored.confidence is None


def test_transcript_event_segments_round_trip():
    event = TranscriptEvent(
        text="hey 107 take off",
        segments=[
            TranscriptSegment(text="hey 107", start_time=0.0, end_time=0.8, confidence=-0.1),
            TranscriptSegment(text="take off", start_time=0.8, end_time=1.5, confidence=-0.2),
        ],
    )

    restored = TranscriptEvent.from_dict(event.to_dict())

    assert restored == event


def test_transcript_event_from_dict_defaults_segments_when_absent():
    payload = {"text": "hey 107 take off", "session_id": "session-1"}

    restored = TranscriptEvent.from_dict(payload)

    assert restored.segments == []


def test_transcript_event_positional_construction_preserves_timestamp():
    event = TranscriptEvent(
        "hey 107 take off",  # text
        "transcript.segment_final",  # type
        "session-1",  # session_id
        7,  # user_id
        107,  # asset_id
        -0.2,  # confidence
        0.1,  # start_time
        1.5,  # end_time
        "service-1:1",  # segment_id
        0,  # revision
        "faster_whisper",  # engine
        False,  # is_fallback
        None,  # capture_start_time
        None,  # capture_end_time
        {},  # metadata
        123.456,  # timestamp
    )

    assert event.timestamp == 123.456
    assert event.segments == []


def test_transcript_segment_public_imports():
    from olab_voice import TranscriptSegment as TopLevelTranscriptSegment
    from olab_voice.stt import TranscriptSegment as SttTranscriptSegment

    assert TopLevelTranscriptSegment is TranscriptSegment
    assert SttTranscriptSegment is TranscriptSegment


def test_tts_request_defaults():
    request = TtsRequest(text="107 is listening")

    assert request.output == "browser_playback"
    assert request.format == "wav"
    assert request.preempt is False


def test_tts_request_round_trip():
    request = TtsRequest(text="107 is listening", session_id="session-1", user_id=7, asset_id=107)

    restored = TtsRequest.from_dict(request.to_dict())

    assert restored == request


def test_tts_request_preempt_round_trip():
    request = TtsRequest(text="alert: obstacle ahead", preempt=True)

    restored = TtsRequest.from_dict(request.to_dict())

    assert restored == request
    assert restored.preempt is True


def test_tts_request_from_dict_defaults_preempt_false():
    restored = TtsRequest.from_dict({"text": "107 is listening"})

    assert restored.preempt is False


def test_tts_audio_round_trip():
    audio = TtsAudio(data=b"RIFF", format="audio/wav", sample_rate=22050, channels=1)

    restored = TtsAudio.from_dict(audio.to_dict())

    assert restored == audio
