#!/usr/bin/env python3
"""Minimal hardware bring-up test for the OpenMV GENX320 event camera.

Exercises any of the three CameraOpenMV modes against a real board and prints a
clear summary so you can confirm the sensor is producing data. Designed to fail
loud and clean up deterministically (always stops the device in `finally`) --
useful given the earlier post-exec hang investigation.

Examples
--------
    # Provisional default: 320x320 histogram preview, no browser stream.
    python genx_hardware_test.py --port /dev/ttyACM0

    # Histogram + on-board movement regions, 10 seconds, with browser stream.
    python genx_hardware_test.py --mode regions --seconds 10 --stream

    # Raw EVT2.0 events: tally polarity, optionally record the session.
    python genx_hardware_test.py --mode raw --record ./genx-session

    # Histogram preview, also recorded as a watchable .mp4.
    python genx_hardware_test.py --mode histogram --movie-out ./genx-session.mp4

Stop early any time with Ctrl-C; the camera is still shut down cleanly.
"""

import argparse
import os
import time

from olab_camera import CameraOpenMV

MODES = {
    'histogram': 'genx_histogram_preview',
    'regions':   'genx_histogram_regions',
    'raw':       'genx_raw_events',
}

# Per lab convention, point streaming at the shared leaf certs rather than the
# auto-generated self-signed cert. Only used when --stream is passed.
DEFAULT_SSL_PATH = os.path.expanduser('~/Projects/ca-vault/leaf-certs/local')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', default='/dev/ttyACM0',
                    help='OpenMV CDC serial device (default: /dev/ttyACM0). '
                         'Name it explicitly -- ACM numbering can change on reconnect.')
    ap.add_argument('--mode', choices=MODES, default='histogram',
                    help='Which GENX320 mode to test (default: histogram).')
    ap.add_argument('--seconds', type=float, default=8.0,
                    help='How long to run the capture (default: 8).')
    ap.add_argument('--stream', action='store_true',
                    help='Also start the MJPEG browser stream on --port-out.')
    ap.add_argument('--port-out', type=int, default=8000,
                    help='Streaming server port when --stream is set (default: 8000).')
    ap.add_argument('--ssl-path', default=DEFAULT_SSL_PATH,
                    help='Leaf-cert dir used for the stream (default: %(default)s).')
    ap.add_argument('--record', metavar='DIR', default=None,
                    help='raw mode only: record EVT2.0 batches to this directory.')
    ap.add_argument('--movie-out', metavar='PATH', default=None,
                    help='histogram/regions modes only: also record the rendered '
                         'preview frames to this .mp4 file (for watching back, not '
                         'motion analysis -- see --record for that).')
    ap.add_argument('--movie-fps', type=float, default=15.0,
                    help='playback fps for --movie-out (default: 15).')
    args = ap.parse_args()

    if args.movie_out and args.mode == 'raw':
        ap.error('--movie-out only applies to histogram/regions modes; '
                  'use --record for raw mode.')

    profile = MODES[args.mode]
    print(f'Opening {args.port!r} in mode {args.mode!r} (profile={profile!r}) '
          f'for {args.seconds:g}s...')

    cam = CameraOpenMV(args.port, profile=profile,
                       sslPath=args.ssl_path if args.stream else None)

    # ---- raw-mode opt-in extras: register BEFORE start() ----
    raw_tally = {'batches': 0, 'events': 0, 'on': 0, 'off': 0}
    if args.mode == 'raw':
        def on_batch(batch):
            raw_tally['batches'] += 1
            raw_tally['events'] += batch.count
            if batch.count:
                on = int(batch.polarity.sum())
                raw_tally['on'] += on
                raw_tally['off'] += batch.count - on
        cam.addEventCallback(on_batch)
        if args.record:
            cam.addEventRecorder(outputDir=args.record)
            print(f'  recording raw batches to {args.record!r}')

    last_seq = None
    try:
        cam.start(startStream=args.stream, port=args.port_out)
        if args.stream:
            print(f'  streaming: https://<this-host>:{args.port_out}/  (self-signed/leaf cert)')

        if args.movie_out:
            # recordVideoLocal() needs a first frame to size the writer -- give
            # the capture thread a bounded moment to deliver one.
            deadline_first_frame = time.monotonic() + min(args.seconds, 5.0)
            while time.monotonic() < deadline_first_frame:
                try:
                    cam.getFrameAndMeta()
                    break
                except IndexError:
                    time.sleep(0.1)
            movie_dir, movie_file = os.path.split(args.movie_out)
            path, filename = cam.recordVideoLocal(path=movie_dir or '.', filename=movie_file,
                                                   fps=args.movie_fps)
            if filename is None:
                print('  WARNING: --movie-out failed to start (no frame available yet)')

        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(1.0)
            try:
                frame, meta = cam.getFrameAndMeta()
            except IndexError:
                print('  ...no frame yet')
                continue
            grew = '' if meta['sequence'] == last_seq else '  (new frames arriving)'
            last_seq = meta['sequence']
            elapsed = args.seconds - (deadline - time.monotonic())
            # cam.fps['capture'] is a _make_fps_dict object, not a float --
            # read its .actual field (recalculates every ~5s).
            fps_actual = getattr(cam.fps.get('capture'), 'actual', 0.0)
            print(f'  t+{elapsed:4.1f}s  frame={frame.shape} seq={meta["sequence"]} '
                  f'fps~{fps_actual:.1f}{grew}')
            if args.mode == 'regions' and cam.latestMovementRecord:
                regs = cam.latestMovementRecord.get('regions', [])
                print(f'            movement regions: {len(regs)} -> {regs}')
            if args.mode == 'raw':
                print(f'            raw so far: {raw_tally}')

    except KeyboardInterrupt:
        print('\nInterrupted -- shutting down.')
    finally:
        if args.movie_out:
            cam.stopRecordVideoLocal()
        cam.shutdown()

    # ---- summary ----
    print('\n=== summary ===')
    print(f'mode           : {args.mode} ({profile})')
    print(f'last frame seq : {last_seq}')
    if args.movie_out:
        print(f'movie          : {args.movie_out}')
    if args.mode == 'raw':
        print(f'raw tally      : {raw_tally}')
        print(f'event stats    : {cam.eventStats}')
        if any(k.endswith('_drops') and v for k, v in cam.eventStats.items()):
            print('NOTE: nonzero drop counters -- host could not keep up with the '
                  'event rate (expected under dense motion; not a failure).')
    if last_seq is None:
        print('WARNING: no frames were ever received. Check the device port, that '
              'the board is running, and dmesg for USB CDC enumeration.')


if __name__ == '__main__':
    main()
