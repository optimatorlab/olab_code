from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from olab_rf.receivers.rtlsdr_iq import iq_samples_from_u8

SIGMF_DATATYPE = "cu8"
SIGMF_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class SigMFIqRecording:
    """A bounded read of a recorded SigMF IQ capture.

    ``samples`` covers only the ``[sample_start, sample_start + len(samples))``
    slice actually requested — see ``read_sigmf_iq``. ``total_sample_count``
    is the number of I/Q pairs in the whole (truncated-to-even) data file, for
    callers deciding whether to read more.
    """

    samples: Any
    frequency_hz: int
    sample_rate_hz: int
    datatype: str
    total_sample_count: int


def sigmf_paths(path: str) -> tuple[Path, Path]:
    """Resolve a caller-supplied base path to the SigMF meta/data pair.

    A trailing ``.sigmf-meta``/``.sigmf-data`` suffix is stripped; any other
    suffix is kept as part of the base.
    """
    text = str(path)
    if text.endswith(".sigmf-meta"):
        base = text[: -len(".sigmf-meta")]
    elif text.endswith(".sigmf-data"):
        base = text[: -len(".sigmf-data")]
    else:
        base = text
    return Path(base + ".sigmf-meta"), Path(base + ".sigmf-data")


def write_sigmf_meta(
    meta_path: Path,
    *,
    sample_rate_hz: int,
    frequency_hz: int,
    datetime_iso: str,
    num_channels: int = 1,
    sample_count: int | None = None,
) -> None:
    """Write (or overwrite) a single-segment, single-channel SigMF sidecar.

    ``sample_count``, when given, is recorded as a single SigMF annotation
    covering the whole capture (``core:sample_start: 0``, ``core:sample_count``
    — both standard SigMF v1.0.0 annotation fields; there is no core
    "duration" field, so a duration is always derivable as
    ``sample_count / sample_rate_hz`` rather than stored redundantly).
    Left ``None`` for the initial sidecar written right after a recording
    starts, before a final count is known; ``_finalize_recording`` passes the
    true final count so the finalized sidecar differs from the initial one.
    """
    payload = {
        "global": {
            "core:datatype": SIGMF_DATATYPE,
            "core:version": SIGMF_VERSION,
            "core:sample_rate": sample_rate_hz,
            "core:num_channels": num_channels,
        },
        "captures": [
            {
                "core:sample_start": 0,
                "core:frequency": frequency_hz,
                "core:datetime": datetime_iso,
            }
        ],
        "annotations": (
            [{"core:sample_start": 0, "core:sample_count": sample_count}]
            if sample_count is not None
            else []
        ),
    }
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def truncate_to_iq_pairs(data_path: Path) -> int:
    """Truncate a ``.sigmf-data`` file to a whole number of I/Q byte pairs.

    Returns the (possibly truncated) file size in bytes. A missing file is
    treated as size ``0`` rather than an error — the expected state in the
    window between the capture process starting and it opening its output
    file.
    """
    if not data_path.exists():
        return 0
    size = data_path.stat().st_size
    even_size = size - (size % 2)
    if even_size != size:
        with data_path.open("r+b") as handle:
            handle.truncate(even_size)
    return even_size


def read_sigmf_iq(
    path: str,
    *,
    sample_start: int = 0,
    max_samples: int | None = None,
) -> SigMFIqRecording:
    """Read a recorded SigMF IQ capture back into normalized complex samples.

    ``path`` is resolved the same way ``sigmf_paths`` resolves a recording's
    base path. Only ``core:datatype == "cu8"`` is supported; any other value
    raises ``ValueError`` before touching sample data. The read is bounded by
    ``sample_start``/``max_samples`` (in I/Q pairs, not bytes): only the
    requested byte range is ever read off disk (via a seek + bounded
    ``read()``, not a whole-file load), so a caller never has to pull an
    entire multi-hundred-MB corpus file into memory to answer one question
    about it. A missing ``.sigmf-data`` file is treated as zero samples,
    matching ``truncate_to_iq_pairs``'s convention for the same window.

    ``global``/``core:datatype``/``core:sample_rate`` and the first capture
    segment's ``core:frequency`` are required by this reader even though
    SigMF v1.0.0 itself makes some of them optional (notably
    ``core:frequency``) -- a file this reader can't place on the spectrum
    isn't usable as a replay source, so a missing field raises ``ValueError``
    naming the file and the field rather than a bare ``KeyError``.
    """
    if sample_start < 0:
        raise ValueError("sample_start must be non-negative")
    if max_samples is not None and max_samples <= 0:
        raise ValueError("max_samples must be greater than zero")

    meta_path, data_path = sigmf_paths(path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    global_ = meta.get("global")
    if not isinstance(global_, dict):
        raise ValueError(f"SigMF file is missing 'global': {meta_path}")
    captures = meta.get("captures")
    if not captures:
        raise ValueError(f"SigMF file has no capture segments: {meta_path}")

    if "core:datatype" not in global_:
        raise ValueError(f"SigMF file is missing 'global'/'core:datatype': {meta_path}")
    datatype = global_["core:datatype"]
    if datatype != SIGMF_DATATYPE:
        raise ValueError(f"unsupported SigMF datatype: {datatype!r} (expected {SIGMF_DATATYPE!r})")

    if "core:sample_rate" not in global_:
        raise ValueError(f"SigMF file is missing 'global'/'core:sample_rate': {meta_path}")
    sample_rate_hz = int(global_["core:sample_rate"])

    capture = captures[0]
    if not isinstance(capture, dict) or "core:frequency" not in capture:
        raise ValueError(
            f"SigMF file's capture segment is missing 'core:frequency': {meta_path}"
        )
    frequency_hz = int(capture["core:frequency"])

    total_size = data_path.stat().st_size if data_path.exists() else 0
    total_size -= total_size % 2  # tolerate a dangling odd byte, same as truncate_to_iq_pairs
    total_sample_count = total_size // 2

    byte_start = min(sample_start, total_sample_count) * 2
    byte_end = total_size if max_samples is None else min(total_size, byte_start + max_samples * 2)
    read_length = byte_end - byte_start

    if read_length > 0:
        with data_path.open("rb") as handle:
            handle.seek(byte_start)
            sliced = handle.read(read_length)
    else:
        sliced = b""

    samples = iq_samples_from_u8(sliced) if sliced else _empty_complex()

    return SigMFIqRecording(
        samples=samples,
        frequency_hz=frequency_hz,
        sample_rate_hz=sample_rate_hz,
        datatype=datatype,
        total_sample_count=total_sample_count,
    )


def _empty_complex() -> Any:
    import numpy as np

    return np.zeros(0, dtype=np.complex64)
