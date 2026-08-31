#!/usr/bin/env python3
"""Manual hardware bring-up test for CameraBosonDual (RHP-BOS-DS-IF, via an
HDMI-to-USB capture dongle).

Before running this: the board's HD Window Mode (Full-Thermal, Full-Visible,
Split-IR/Visible, PiP-...) and resolution must already be set the way you
want, via the Windows-only "RHP Boson Camera Controller GUI" (or SBUS/PWM) --
this class is video-only and cannot see or change that setting. See
CameraBosonDual's docstring and .pairwork/camera-boson-dual.md for the full
hardware writeup.

The main thing this script checks is the open question from that writeup:
whether the requested `--resolution` preset actually took, or got silently
overridden -- either because the board itself is in a different mode, or
because of CameraUSB's known FOURCC-after-resolution V4L2 ordering hazard.
Both would show up the same way here: `res_rows`/`res_cols`/`fps_target`
not matching what was requested.

Examples
--------
    # See available capture-dongle video nodes first.
    python camera_boson_dual_hardware_test.py --list

    # Open at the default 720p60 preset, print a running summary for 8s.
    python camera_boson_dual_hardware_test.py --device /dev/video2

    # 1080p60, with the browser stream on, for 15 seconds.
    python camera_boson_dual_hardware_test.py --device /dev/video2 \\
        --resolution 1080p60 --stream --seconds 15

Stop early any time with Ctrl-C; the camera is still shut down cleanly.
"""

import argparse
import glob
import os
import time

from olab_camera import CameraBosonDual

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
    ap.add_argument('--device', default='/dev/video0',
                    help='Capture dongle video node (default: /dev/video0). '
                         'Run --list first -- there is no auto-discovery.')
    ap.add_argument('--resolution', choices=['720p60', '1080p60'], default='720p60',
                    help='Requested preset -- must match what the board is '
                         'actually configured to output (default: 720p60).')
    ap.add_argument('--seconds', type=float, default=8.0,
                    help='How long to run the capture (default: 8).')
    ap.add_argument('--stream', action='store_true',
                    help='Also start the MJPEG browser stream on --port-out.')
    ap.add_argument('--port-out', type=int, default=8000,
                    help='Streaming server port when --stream is set (default: 8000).')
    ap.add_argument('--ssl-path', default=DEFAULT_SSL_PATH,
                    help='Leaf-cert dir used for the stream (default: %(default)s).')
    args = ap.parse_args()

    if args.list:
        list_devices()
        return

    print(f'Opening {args.device!r} requesting {args.resolution!r} '
          f'for {args.seconds:g}s...')

    cam = CameraBosonDual(resolution=args.resolution, device=args.device,
                          sslPath=args.ssl_path if args.stream else None)

    last_frame_shape = None
    try:
        cam.start(startStream=args.stream, port=args.port_out)

        # CameraUSB.start() never raises on a hardware/open failure -- it logs
        # and leaves camOn False. Check for that explicitly rather than
        # falling into the capture loop against an empty frameDeque.
        if not cam.camOn:
            print('  ERROR: start() did not bring the camera up (camOn is False). '
                  'See the LOGGER line above for the reason (commonly: wrong '
                  '--device, or the capture dongle isn\'t enumerated yet -- check '
                  '`v4l2-ctl --list-devices` and `lsusb`).')
            return

        if args.stream:
            print(f'  streaming: https://<this-host>:{args.port_out}/  (leaf cert)')

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
    print(f'requested preset : {args.resolution} '
          f'-> res_rows={cam.res_rows} res_cols={cam.res_cols} fps_target={cam.fps_target}')
    print(f'last frame shape : {last_frame_shape}')
    if last_frame_shape is None:
        print('WARNING: no frames were ever received. Check --device, that the '
              'board is powered and its HDMI is connected to the capture dongle, '
              'and dmesg for USB video enumeration.')
    else:
        actual_rows, actual_cols = last_frame_shape[:2]
        if (actual_rows, actual_cols) != (cam.res_rows, cam.res_cols):
            print(f'WARNING: last frame shape ({actual_rows}x{actual_cols}) does not '
                  f'match the configured res_rows/res_cols ({cam.res_rows}x{cam.res_cols}). '
                  'See CameraBosonDual\'s docstring -- this is exactly the FOURCC-ordering '
                  'hazard / resolution-does-not-configure-the-board caveat it warns about.')


if __name__ == '__main__':
    main()
