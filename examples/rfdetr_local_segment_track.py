#!/usr/bin/env python3
"""Run local RF-DETR instance segmentation with ByteTrack on a USB camera.

Example:
    python examples/rfdetr_local_segment_track.py --weights /opt/models/rf-detr-seg-small.pt
"""

import argparse
import time
from pathlib import Path

from olab_camera import CameraUSB


def resolve_weights_path(value):
    """Resolve a local checkpoint without opening the camera first."""
    weights_path = Path(value).expanduser()
    if not weights_path.is_absolute():
        weights_path = Path.home() / 'Projects' / 'olab_models' / weights_path
    if not weights_path.is_file():
        raise SystemExit(f'Checkpoint not found: {weights_path}')
    return str(weights_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', required=True, help='Checkpoint path; relative names resolve under ~/Projects/olab_models.')
    parser.add_argument('--camera', default='/dev/video0', help='USB camera device (default: %(default)s).')
    parser.add_argument('--variant', choices=('nano', 'small', 'medium', 'large'), default='small')
    parser.add_argument('--seconds', type=float, default=20, help='Run duration (default: %(default)s).')
    parser.add_argument('--fps', type=int, default=5, help='Inference target FPS (default: %(default)s).')
    parser.add_argument('--device', default='cpu', help='RF-DETR device, e.g. cpu or cuda (default: %(default)s).')
    parser.add_argument('--stream-port', type=int, default=8000, help='MJPEG stream port; 0 disables streaming (default: %(default)s).')
    args = parser.parse_args()
    weights_path = resolve_weights_path(args.weights)

    camera = CameraUSB(device=args.camera)
    camera.start()
    if not camera.camOn:
        raise SystemExit(f'Camera failed to start: {args.camera}; see the logged error above.')
    camera.addRFDETR('segment-track', task='segment', model_variant=args.variant,
                     weights_path=weights_path, tracker='bytetrack', fps_target=args.fps, device=args.device)
    if 'segment-track' not in camera.rfdetr:
        camera.stop()
        raise SystemExit('addRFDETR failed; see the logged error above (check --weights and the rfdetr extra).')
    if args.stream_port:
        camera.startStream(port=args.stream_port)
        if camera.streamURL:
            print(f'View stream: {camera.streamURL}')
    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            feature = camera.rfdetr.get('segment-track')
            if feature is None or (not feature.isThreadActive and not feature.deque):
                raise RuntimeError('RF-DETR worker stopped; see the logged error above.')
            if feature.deque:
                latest = feature.deque[0]
                for label, track_id, mask in zip(latest['class'], latest['track_id'], latest['masks']):
                    if track_id >= 0:
                        print(f'ID {track_id:3}: {label:16} {mask.sum()} mask pixels')
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        feature = camera.rfdetr.get('segment-track')
        if feature is not None:
            feature.stop()
        camera.stop()


if __name__ == '__main__':
    main()
