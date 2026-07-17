from olab_voice.audio.models import AudioBlob, AudioFrame
from olab_voice.stt.base import TranscriptEvent
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


def test_tts_request_defaults():
    request = TtsRequest(text="107 is listening")

    assert request.output == "browser_playback"
    assert request.format == "wav"


def test_tts_request_round_trip():
    request = TtsRequest(text="107 is listening", session_id="session-1", user_id=7, asset_id=107)

    restored = TtsRequest.from_dict(request.to_dict())

    assert restored == request


def test_tts_audio_round_trip():
    audio = TtsAudio(data=b"RIFF", format="audio/wav", sample_rate=22050, channels=1)

    restored = TtsAudio.from_dict(audio.to_dict())

    assert restored == audio
