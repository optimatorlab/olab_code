from __future__ import annotations

import json
from pathlib import Path

import pytest

from olab_rf.decoders.sigmf import (
    read_sigmf_iq,
    sigmf_paths,
    truncate_to_iq_pairs,
    write_sigmf_meta,
)


def _synthetic_cu8(count: int, *, tone_offset_hz: int = 1_000, sample_rate_hz: int = 8_000) -> bytes:
    np = pytest.importorskip("numpy")
    time = np.arange(count) / sample_rate_hz
    tone = np.exp(2j * np.pi * tone_offset_hz * time)
    i = np.clip(np.round(tone.real * 40 + 127.5), 0, 255).astype(np.uint8)
    q = np.clip(np.round(tone.imag * 40 + 127.5), 0, 255).astype(np.uint8)
    interleaved = np.empty(2 * count, dtype=np.uint8)
    interleaved[0::2] = i
    interleaved[1::2] = q
    return interleaved.tobytes()


def _write_recording(base_path, *, count=64, sample_rate_hz=8_000, frequency_hz=462_712_500):
    meta_path, data_path = sigmf_paths(str(base_path))
    data_path.write_bytes(_synthetic_cu8(count, sample_rate_hz=sample_rate_hz))
    write_sigmf_meta(
        meta_path,
        sample_rate_hz=sample_rate_hz,
        frequency_hz=frequency_hz,
        datetime_iso="2026-09-01T00:00:00+00:00",
    )
    return meta_path, data_path


def test_sigmf_round_trip_matches_written_samples_and_on_disk_layout(tmp_path):
    np = pytest.importorskip("numpy")
    meta_path, data_path = _write_recording(tmp_path / "capture", count=128)

    on_disk = json.loads(meta_path.read_text(encoding="utf-8"))
    assert set(on_disk.keys()) == {"global", "captures", "annotations"}
    assert on_disk["global"]["core:datatype"] == "cu8"
    assert on_disk["global"]["core:sample_rate"] == 8_000
    assert on_disk["global"]["core:num_channels"] == 1
    assert len(on_disk["captures"]) == 1
    capture = on_disk["captures"][0]
    assert capture["core:sample_start"] == 0
    assert capture["core:frequency"] == 462_712_500
    assert capture["core:datetime"] == "2026-09-01T00:00:00+00:00"
    assert on_disk["annotations"] == []

    recording = read_sigmf_iq(str(tmp_path / "capture"))

    assert recording.frequency_hz == 462_712_500
    assert recording.sample_rate_hz == 8_000
    assert recording.datatype == "cu8"
    assert recording.total_sample_count == 128
    assert recording.samples.size == 128
    assert recording.samples.dtype == np.complex64

    raw = data_path.read_bytes()
    from olab_rf.receivers.rtlsdr_iq import iq_samples_from_u8

    expected = iq_samples_from_u8(raw)
    assert np.allclose(recording.samples, expected)


def test_sigmf_reader_rejects_non_cu8_datatype(tmp_path):
    meta_path, data_path = sigmf_paths(str(tmp_path / "capture"))
    data_path.write_bytes(_synthetic_cu8(8))
    write_sigmf_meta(
        meta_path,
        sample_rate_hz=8_000,
        frequency_hz=462_712_500,
        datetime_iso="2026-09-01T00:00:00+00:00",
    )
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["global"]["core:datatype"] = "cf32_le"
    meta_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported SigMF datatype"):
        read_sigmf_iq(str(tmp_path / "capture"))


def test_sigmf_reader_tolerates_odd_length_data_file(tmp_path):
    meta_path, data_path = _write_recording(tmp_path / "capture", count=32)
    data_path.write_bytes(data_path.read_bytes() + b"\x00")  # dangling odd byte

    recording = read_sigmf_iq(str(tmp_path / "capture"))

    assert recording.total_sample_count == 32
    assert recording.samples.size == 32


def test_truncate_to_iq_pairs_fixes_odd_length_file(tmp_path):
    _, data_path = _write_recording(tmp_path / "capture", count=10)
    data_path.write_bytes(data_path.read_bytes() + b"\x00")
    assert data_path.stat().st_size == 21

    size = truncate_to_iq_pairs(data_path)

    assert size == 20
    assert data_path.stat().st_size == 20


def test_truncate_to_iq_pairs_tolerates_missing_file(tmp_path):
    missing = tmp_path / "missing.sigmf-data"

    assert truncate_to_iq_pairs(missing) == 0


def test_sigmf_reader_bounds_read_with_sample_start_and_max_samples(tmp_path):
    _write_recording(tmp_path / "capture", count=100)

    recording = read_sigmf_iq(str(tmp_path / "capture"), sample_start=10, max_samples=20)

    assert recording.samples.size == 20
    assert recording.total_sample_count == 100


def test_sigmf_reader_only_reads_the_requested_byte_window_off_disk(tmp_path, monkeypatch):
    """Pin the bound at the disk-read level, not just the returned slice.

    A prior implementation loaded the whole ``.sigmf-data`` file with
    ``read_bytes()`` and sliced the in-memory result afterward, so
    ``max_samples`` bounded the returned array but not the actual I/O or
    memory use -- a multi-hundred-MB corpus file would still be pulled fully
    resident just to answer a small bounded question about it. This spies on
    the file handle's own ``read()`` call to prove only the requested byte
    range is ever read.
    """
    total_samples = 2_000_000  # far larger than the small window requested below
    _write_recording(tmp_path / "capture", count=total_samples)
    _, data_path = sigmf_paths(str(tmp_path / "capture"))

    read_lengths: list[int] = []
    real_open = Path.open

    def spy_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self == data_path:
            real_read = handle.read

            def spy_read(n=-1):
                read_lengths.append(n)
                return real_read(n)

            handle.read = spy_read
        return handle

    monkeypatch.setattr(Path, "open", spy_open)

    recording = read_sigmf_iq(str(tmp_path / "capture"), max_samples=1_000)

    assert recording.samples.size == 1_000
    assert recording.total_sample_count == total_samples
    assert read_lengths == [1_000 * 2]  # only the requested byte window, not the whole file


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"sample_start": -1}, "sample_start"),
        ({"max_samples": 0}, "max_samples"),
        ({"max_samples": -5}, "max_samples"),
    ],
)
def test_sigmf_reader_rejects_invalid_bounds(tmp_path, kwargs, match):
    _write_recording(tmp_path / "capture", count=8)

    with pytest.raises(ValueError, match=match):
        read_sigmf_iq(str(tmp_path / "capture"), **kwargs)


def test_sigmf_reader_rejects_empty_capture_segments(tmp_path):
    meta_path, data_path = sigmf_paths(str(tmp_path / "capture"))
    data_path.write_bytes(_synthetic_cu8(8))
    meta_path.write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cu8",
                    "core:version": "1.0.0",
                    "core:sample_rate": 8_000,
                    "core:num_channels": 1,
                },
                "captures": [],
                "annotations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no capture segments"):
        read_sigmf_iq(str(tmp_path / "capture"))


def _write_raw_meta(meta_path, payload):
    meta_path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    "payload, match",
    [
        (
            {"captures": [{"core:frequency": 462_712_500}], "annotations": []},
            "missing 'global'",
        ),
        (
            {
                "global": {"core:sample_rate": 8_000},
                "captures": [{"core:frequency": 462_712_500}],
                "annotations": [],
            },
            "core:datatype",
        ),
        (
            {
                "global": {"core:datatype": "cu8"},
                "captures": [{"core:frequency": 462_712_500}],
                "annotations": [],
            },
            "core:sample_rate",
        ),
        (
            {
                "global": {"core:datatype": "cu8", "core:sample_rate": 8_000},
                "captures": [{"core:sample_start": 0}],  # core:frequency omitted --
                # spec-legal per SigMF v1.0.0, but this reader requires it
                "annotations": [],
            },
            "core:frequency",
        ),
    ],
)
def test_sigmf_reader_rejects_spec_legal_files_missing_required_fields(tmp_path, payload, match):
    """A field this reader requires (e.g. core:frequency) can legally be

    omitted by other SigMF tooling (sigmf-python, inspectrum). A file
    missing one of them must raise a named ValueError, not a bare KeyError.
    """
    meta_path, data_path = sigmf_paths(str(tmp_path / "capture"))
    data_path.write_bytes(_synthetic_cu8(8))
    _write_raw_meta(meta_path, payload)

    with pytest.raises(ValueError, match=match):
        read_sigmf_iq(str(tmp_path / "capture"))


def test_write_sigmf_meta_finalized_sample_count_annotation(tmp_path):
    meta_path, _ = sigmf_paths(str(tmp_path / "capture"))

    write_sigmf_meta(
        meta_path,
        sample_rate_hz=8_000,
        frequency_hz=462_712_500,
        datetime_iso="2026-09-01T00:00:00+00:00",
        sample_count=128,
    )

    on_disk = json.loads(meta_path.read_text(encoding="utf-8"))
    assert on_disk["annotations"] == [{"core:sample_start": 0, "core:sample_count": 128}]


def test_sigmf_paths_strip_known_suffixes(tmp_path):
    base = str(tmp_path / "capture")

    assert sigmf_paths(base) == sigmf_paths(base + ".sigmf-meta") == sigmf_paths(base + ".sigmf-data")
