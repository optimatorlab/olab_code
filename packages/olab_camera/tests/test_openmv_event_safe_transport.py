"""Regression test for `_EventSafeTransport` (olab_camera.openmv_device).

Reproduces, without hardware, the upstream `openmv.transport.Transport` bug
that combined with a real-hardware sequence-desync to produce the infinite
hang investigated in docs/investigations/openmv_hang_investigation.md:
`recv_packet()` unconditionally renews its response-wait deadline on every
incoming EVENT packet (e.g. the firmware's periodic stdout "tick"
notifications), so a request that never gets a real response can never time
out either. This test drives `_EventSafeTransport` with a fake serial port
that floods it with EVENT packets and never sends a real response, and
asserts it still raises `TimeoutException` within a small bounded interval
-- i.e. the fix does the opposite of masking a stuck request: it makes one
diagnosable instead of hanging forever.
"""

import struct
import time

import pytest

openmv = pytest.importorskip('openmv')

from openmv.constants import Flags, Opcode, Protocol
from openmv.exceptions import TimeoutException

from olab_camera.openmv_device import _EventSafeTransport


def _event_packet(chan=2):
    """A single, minimal, valid EVENT packet (no payload, header CRC
    irrelevant since the transport below is constructed with crc=False)."""
    return struct.pack('<HBBBBHH', Protocol.SYNC_WORD, 0, chan,
                        Flags.EVENT, Opcode.CHANNEL_EVENT, 0, 0)


class _EventFloodSerial:
    """Fake serial port that emits a fresh EVENT packet every `interval`
    seconds, forever, and never sends any other response."""

    def __init__(self, interval=0.02):
        self.is_open = True
        self._packet = _event_packet()
        self._interval = interval
        self._last_emit = time.monotonic()
        self._buf = bytearray()

    @property
    def in_waiting(self):
        now = time.monotonic()
        while now - self._last_emit >= self._interval:
            self._buf.extend(self._packet)
            self._last_emit += self._interval
        return len(self._buf)

    def read(self, n):
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def write(self, data):
        pass


def test_event_flood_times_out_within_bounded_interval():
    serial = _EventFloodSerial(interval=0.02)
    events_seen = []
    transport = _EventSafeTransport(
        serial, crc=False, seq=True, max_payload=64, timeout=0.3,
        event_callback=lambda chan, event: events_seen.append((chan, event)))

    start = time.monotonic()
    with pytest.raises(TimeoutException):
        transport.recv_packet()
    elapsed = time.monotonic() - start

    # Bounded: must time out at roughly `timeout`, not hang waiting for a
    # response that will never arrive just because EVENT packets keep
    # arriving in the meantime.
    assert elapsed < 2.0
    # Sanity: the flood was actually happening throughout the wait, not a
    # coincidental empty buffer -- the bug (and the fix) both hinge on
    # EVENT packets genuinely being processed during the wait.
    assert len(events_seen) >= 5
