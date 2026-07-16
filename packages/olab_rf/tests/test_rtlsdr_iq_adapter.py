from __future__ import annotations

import pytest

from olab_rf.receivers import rtlsdr_iq
from olab_rf.receivers.rtlsdr_iq import (
    capture_iq_samples,
    capture_iq_samples_with_rtl_sdr,
    iq_samples_from_u8,
    rtl_sdr_iq_command,
)


class FakeSdr:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.sample_rate = None
        self.center_freq = None
        self.freq_correction = None
        self.gain = None
        self.closed = False
        FakeSdr.instances.append(self)

    def read_samples(self, sample_count):
        self.sample_count = sample_count
        return [1 + 0j] * sample_count

    def close(self):
        self.closed = True


def test_capture_iq_samples_configures_and_closes_sdr():
    FakeSdr.instances = []

    samples = capture_iq_samples(
        center_frequency_hz=462_712_500,
        sample_rate_hz=240_000,
        sample_count=1024,
        serial="00000001",
        gain_db=28.0,
        ppm=2,
        sdr_class=FakeSdr,
    )

    sdr = FakeSdr.instances[0]
    assert samples == [1 + 0j] * 1024
    assert sdr.kwargs == {"serial_number": "00000001"}
    assert sdr.sample_rate == 240_000
    assert sdr.center_freq == 462_712_500
    assert sdr.freq_correction == 2
    assert sdr.gain == 28.0
    assert sdr.sample_count == 1024
    assert sdr.closed is True


def test_capture_iq_samples_uses_device_index_without_serial():
    FakeSdr.instances = []

    capture_iq_samples(
        center_frequency_hz=462_712_500,
        sample_rate_hz=240_000,
        sample_count=1,
        device_index=2,
        sdr_class=FakeSdr,
    )

    assert FakeSdr.instances[0].kwargs == {"device_index": 2}


def test_rtl_sdr_iq_command_builds_stdout_capture_command():
    assert rtl_sdr_iq_command(
        path="/usr/bin/rtl_sdr",
        center_frequency_hz=462_712_500,
        sample_rate_hz=240_000,
        sample_count=1024,
        device_index=1,
        gain_db=28.0,
        ppm=2,
    ) == [
        "/usr/bin/rtl_sdr",
        "-f",
        "462712500",
        "-s",
        "240000",
        "-d",
        "1",
        "-p",
        "2",
        "-n",
        "1024",
        "-S",
        "-g",
        "28",
        "-",
    ]


def test_iq_samples_from_u8_converts_interleaved_iq_bytes():
    np = pytest.importorskip("numpy")

    samples = iq_samples_from_u8(bytes([255, 127, 127, 255]), sample_count=2)

    assert samples.dtype == np.complex64 or samples.dtype == np.complex128
    assert samples[0].real == pytest.approx(1.0, abs=0.01)
    assert samples[0].imag == pytest.approx(-0.004, abs=0.01)
    assert samples[1].real == pytest.approx(-0.004, abs=0.01)
    assert samples[1].imag == pytest.approx(1.0, abs=0.01)


def test_capture_iq_samples_with_rtl_sdr_runs_command(monkeypatch):
    class _Result:
        returncode = 0
        stdout = bytes([255, 127, 127, 255])
        stderr = b""

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _Result()

    monkeypatch.setattr("olab_rf.receivers.rtlsdr_iq.subprocess.run", fake_run)

    samples = capture_iq_samples_with_rtl_sdr(
        path="/usr/bin/rtl_sdr",
        center_frequency_hz=462_712_500,
        sample_rate_hz=240_000,
        sample_count=2,
        ppm=2,
    )

    assert len(samples) == 2
    assert calls[0][0][:2] == ["/usr/bin/rtl_sdr", "-f"]
    assert calls[0][1]["capture_output"] is True


def test_capture_iq_samples_with_rtl_sdr_reports_stderr(monkeypatch):
    class _Result:
        returncode = 1
        stdout = b""
        stderr = b"Failed to open rtlsdr device #0."

    monkeypatch.setattr(
        "olab_rf.receivers.rtlsdr_iq.subprocess.run",
        lambda *args, **kwargs: _Result(),
    )

    with pytest.raises(RuntimeError, match="Failed to open"):
        capture_iq_samples_with_rtl_sdr(
            center_frequency_hz=462_712_500,
            sample_rate_hz=240_000,
            sample_count=2,
        )


def test_capture_iq_samples_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="center_frequency_hz"):
        capture_iq_samples(center_frequency_hz=0, sample_rate_hz=1, sample_count=1)

    with pytest.raises(ValueError, match="sample_rate_hz"):
        capture_iq_samples(center_frequency_hz=1, sample_rate_hz=0, sample_count=1)

    with pytest.raises(ValueError, match="sample_count"):
        capture_iq_samples(center_frequency_hz=1, sample_rate_hz=1, sample_count=0)


def test_capture_iq_samples_missing_dependency_message(monkeypatch):
    def missing_rtlsdr():
        raise RuntimeError("Install the pyrtlsdr extra")

    monkeypatch.setattr(rtlsdr_iq, "_rtlsdr_class", missing_rtlsdr)

    with pytest.raises(RuntimeError, match="Install the pyrtlsdr extra"):
        capture_iq_samples(
            center_frequency_hz=462_712_500,
            sample_rate_hz=240_000,
            sample_count=1024,
        )


def test_rtlsdr_class_reports_library_mismatch(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "rtlsdr":
            raise AttributeError("undefined symbol: rtlsdr_set_dithering")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(RuntimeError, match="librtlsdr compatibility"):
        rtlsdr_iq._rtlsdr_class()
