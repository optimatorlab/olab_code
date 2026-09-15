#!/usr/bin/env python3
"""Manual hardware bring-up test for CameraBosonThermal (RHP-BOS-USBC-IF,
FLIR Boson+ thermal core over USB-C).

Unlike CameraBosonDual, this board needs no external capture dongle and no
prior board-side configuration -- it enumerates directly as a genuine FLIR
VID:PID (09cb:4007) and its own default V4L2 negotiation is already this
class's target (640x512 YU12). See CameraBosonThermal's docstring and
.pairwork/camera-boson-thermal.md for the full hardware writeup.

By default this script does not pass --device at all, so it exercises
discover_boson_thermal() the same way CameraBosonThermal() does with no
device= given -- confirming discovery finds exactly one board and identifies
the correct (frame-producing) node, not the metadata-only sibling node.
Pass --device explicitly to bypass discovery (e.g. more than one board
attached).

Examples
--------
    # See available candidate video nodes first (does not run discovery).
    python camera_boson_thermal_hardware_test.py --list

    # Auto-discover the board and print a running summary for 8s.
    python camera_boson_thermal_hardware_test.py

    # Bypass discovery, with the browser stream on, for 15 seconds.
    python camera_boson_thermal_hardware_test.py --device /dev/video4 \\
        --stream --seconds 15

Stop early any time with Ctrl-C; the camera is still shut down cleanly.
"""

import argparse
import glob
import os
import time

import olab_utils

from olab_camera import CameraBosonThermal

# Per lab convention, point streaming at the shared leaf certs rather than the
# auto-generated self-signed cert. Only used when --stream is passed.
DEFAULT_SSL_PATH = os.path.expanduser('~/Projects/ca-vault/leaf-certs/local')


def list_devices():
    print('--- candidate video nodes ---')
    for node in sorted(glob.glob('/dev/video*')):
        print(f'  {node}')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list', action='store_true',
                    help='List candidate /dev/video* nodes, then exit.')
    ap.add_argument('--device', default=None,
                    help='Capture node (e.g. /dev/video4). Default: None, '
                         'which runs discover_boson_thermal() to find the '
                         'board automatically.')
    ap.add_argument('--seconds', type=float, default=8.0,
                    help='How long to run the capture (default: 8).')
    ap.add_argument('--stream', action='store_true',
                    help='Also start the MJPEG browser stream on --port-out.')
    ap.add_argument('--port-out', type=int, default=8000,
                    help='Preferred streaming server port when --stream is set '
                         '(default: 8000). If already in use, the next free port '
                         'in olab_utils.findOpenPort()\'s default range is used instead.')
    ap.add_argument('--ssl-path', default=DEFAULT_SSL_PATH,
                    help='Leaf-cert dir used for the stream (default: %(default)s).')
    args = ap.parse_args()

    if args.list:
        list_devices()
        return

    if args.device is None:
        print('No --device given -- running discover_boson_thermal()...')
    else:
        print(f'Opening {args.device!r} (discovery bypassed) for {args.seconds:g}s...')

    try:
        cam = CameraBosonThermal(device=args.device,
                                 sslPath=args.ssl_path if args.stream else None)
    except RuntimeError as e:
        print(f'  ERROR: discovery failed: {e}')
        print('  Check `v4l2-ctl --list-devices` and `lsusb` for the board '
              "(VID:PID 09cb:4007, card name 'Boson: FLIR Video'), or pass "
              '--device explicitly.')
        return

    print(f'  using device: {cam.device!r}')

    port_out = args.port_out
    if args.stream:
        found = olab_utils.findOpenPort(port_out)
        if found is None:
            print(f'  ERROR: no open port found (preferred={port_out}, '
                  f'and nothing free in findOpenPort()\'s default range).')
            return
        if found != port_out:
            print(f'  port {port_out} in use -- streaming on {found} instead.')
        port_out = found

    last_frame_shape = None
    try:
        cam.start(startStream=args.stream, port=port_out)

        # CameraUSB.start() never raises on a hardware/open failure -- it logs
        # and leaves camOn False. Check for that explicitly rather than
        # falling into the capture loop against an empty frameDeque.
        if not cam.camOn:
            print('  ERROR: start() did not bring the camera up (camOn is False). '
                  'See the LOGGER line above for the reason.')
            return

        if args.stream:
            print(f'  streaming: https://<this-host>:{port_out}/  (leaf cert)')

        print(f'  requested : res_rows={cam.res_rows} res_cols={cam.res_cols} '
              f'fps_target={cam.fps_target}')

        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(1.0)
            try:
                frame = cam.getFrameCopy()
            except IndexError:
                print('  ...no frame yet')
                continue
            last_frame_shape = frame.shape
            # cam.fps['capture'] is a _make_fps_dict object, not a float --
            # read its .actual field (recalculates every ~5s).
            fps_actual = getattr(cam.fps.get('capture'), 'actual', 0.0)
            elapsed = args.seconds - (deadline - time.monotonic())
            print(f'  t+{elapsed:4.1f}s  frame={last_frame_shape} fps~{fps_actual:.1f}')

    except KeyboardInterrupt:
        print('\nInterrupted -- shutting down.')
    finally:
        cam.shutdown()

    # ---- summary ----
    print('\n=== summary ===')
    print(f'device used      : {cam.device}')
    print(f'requested config : res_rows={cam.res_rows} res_cols={cam.res_cols} '
          f'fps_target={cam.fps_target}')
    print(f'last frame shape : {last_frame_shape}')
    if last_frame_shape is None:
        print('WARNING: no frames were ever received. Check that the board '
              'is powered/enumerated (`lsusb`, `v4l2-ctl --list-devices`), '
              'and dmesg for USB video enumeration.')
    else:
        actual_rows, actual_cols = last_frame_shape[:2]
        if (actual_rows, actual_cols) != (cam.res_rows, cam.res_cols):
            print(f'WARNING: last frame shape ({actual_rows}x{actual_cols}) does not '
                  f'match the configured res_rows/res_cols ({cam.res_rows}x{cam.res_cols}). '
                  'This class has no FOURCC-after-framesize hazard (fourcc stays None), '
                  "so check the board/driver's own format negotiation instead.")


if __name__ == '__main__':
    main()
