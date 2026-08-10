#!/usr/bin/env python3
"""Confirm an attached OpenMV board actually has a GENX320 event sensor.

Uploads a tiny script that only constructs the GENX320 CSI object and prints a
result, then reads STDOUT. It deliberately does NOT read any custom channel
(`readChannel`) -- the specific operation the post-`exec` hang lives on (see
`docs/investigations/openmv_hang_investigation.md`) -- and every blocking call
is bounded by the device timeout, so worst case is a bounded timeout, not an
indefinite hang.

Prints `GENX320_OK` (plus the firmware banner naming the board model) if the
sensor constructs, or `GENX320_FAIL <error>` if a different sensor is attached
(e.g. the RT1062's stock OV5640).

Usage:
    python genx_sensor_probe.py [/dev/ttyACM0]
"""
import sys
import time

from olab_camera import OpenMVDevice

PORT = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM0'

PROBE = '''
import csi
try:
    c = csi.CSI(cid=csi.GENX320)
    print("GENX320_OK")
except Exception as e:
    print("GENX320_FAIL %r" % (e,))
'''

dev = OpenMVDevice(PORT, timeout=2.0)
print(f'connecting to {PORT} ...')
dev.connect()
try:
    dev.stopScript()                 # clear anything already running
    dev.runSource(PROBE)             # exec on device (no channel read)
    out = ''
    deadline = time.time() + 6
    while time.time() < deadline:
        chunk = dev.readStdout()
        if chunk:
            out += chunk
            if 'GENX320_OK' in out or 'GENX320_FAIL' in out:
                break
        time.sleep(0.2)
    print('--- device stdout ---')
    print(out.strip() or '(no stdout received before timeout)')
finally:
    try:
        dev.stopScript()
    except Exception:
        pass
    dev.disconnect()
    print('disconnected')
