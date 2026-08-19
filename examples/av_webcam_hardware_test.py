#!/usr/bin/env python3
"""Manual hardware test for AVWebcam -- a paired USB camera + mic.

Opens a video window showing the live camera feed with a simple dB level
bar for the paired mic overlaid at the bottom, so you can confirm both
halves are actually capturing at the same time from one physical webcam.

Run with --list first if you don't already know your camera/mic device
IDs -- USB webcam camera nodes and PortAudio mic indices are assigned
independently by the OS, so they must be looked up separately even though
they're the same physical device.

Examples
--------
    # See available camera nodes and mic device IDs.
    python av_webcam_hardware_test.py --list

    # Open the paired devices and preview both in a window (press q to quit).
    python av_webcam_hardware_test.py --camera /dev/video0 --mic 3
"""

import argparse
import glob

import cv2

from olab_audio import get_input_devices
from olab_camera import AVWebcam


def list_devices():
    print('--- camera nodes ---')
    for node in sorted(glob.glob('/dev/video*')):
        print(f'  {node}')
    print('--- mic input devices ---')
    for dev in get_input_devices():
        print(f'  {dev["deviceID"]}: {dev["name"]}')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list', action='store_true',
                    help='List candidate camera nodes and mic device IDs, then exit.')
    ap.add_argument('--camera', default='/dev/video0',
                    help='Camera device node (default: /dev/video0).')
    ap.add_argument('--mic', type=int, default=None,
                    help='Mic PortAudio device ID (see --list).')
    args = ap.parse_args()

    if args.list:
        list_devices()
        return

    if args.mic is None:
        ap.error('--mic is required (run --list to find a device ID)')

    av = AVWebcam(camera_device=args.camera, mic_device=args.mic)
    av.start()
    print(f'AVWebcam started (camera={args.camera!r}, mic={args.mic}). Press q to quit.')

    try:
        while True:
            frame = av.camera.getFrameCopy()
            if frame is not None:
                h, w = frame.shape[:2]
                # Map roughly [-60, 0] dB onto a bottom-of-frame bar width.
                db = max(-60.0, min(0.0, av.mic.db))
                bar_w = int(w * (db + 60.0) / 60.0)
                cv2.rectangle(frame, (0, h - 20), (w, h), (40, 40, 40), -1)
                cv2.rectangle(frame, (0, h - 20), (bar_w, h), (0, 200, 0), -1)
                cv2.putText(frame, f'{db:.1f} dB', (5, h - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                cv2.imshow('av_webcam_hardware_test', frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        av.stop()
        cv2.destroyAllWindows()
        print('stopped')


if __name__ == '__main__':
    main()
