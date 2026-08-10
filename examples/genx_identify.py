#!/usr/bin/env python3
"""Minimal, bounded 'is this OpenMV board alive?' check.

Connects to the board and reads its cached version/system identity only -- it
does NOT read any custom channel (`readChannel`), so it cannot trip the known
post-`exec` channel-read hang (see
`docs/investigations/openmv_hang_investigation.md`). Every call is bounded by
the device timeout.

`CONNECTED OK` with `firmware_version (5, 0, 0)` means the board is on the
current firmware and our client can talk to it. If this hangs/times out, the
board is almost certainly on old (pre-v5.0.0) firmware whose serial protocol
the pip `openmv` client can't speak -- upgrade it via the OpenMV IDE.

Usage:
    python genx_identify.py [/dev/ttyACM0]
"""
import sys

from olab_camera import OpenMVDevice

PORT = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM0'

dev = OpenMVDevice(PORT, timeout=2.0)
print(f'connecting to {PORT} ...')
try:
    dev.connect()
    print('CONNECTED OK')
    print('versionInfo :', dev.versionInfo)   # protocol/bootloader/firmware versions
    print('systemInfo  :', dev.systemInfo)     # unique device_id, feature flags, etc.
finally:
    dev.disconnect()
    print('disconnected')
