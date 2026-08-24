#!/usr/bin/env python3
"""Compare four local trackers on live RF-DETR USB-camera detections.

Example:
    python examples/generic_local_tracking.py --weights rf-detr-small.pth

The checkpoint must already exist locally. This script never downloads a
model or tracker asset and does not use hosted inference.
"""

import argparse
import time
from pathlib import Path

from olab_camera import CameraUSB


TRACKERS = (('sort', 'sort'), ('byte', 'bytetrack'),
            ('oc', 'ocsort'), ('bot', 'botsort'))


def resolve_weights_path(value):
    """Resolve a pre-provisioned checkpoint without opening the camera first."""
    weights_path = Path(value).expanduser()
    if not weights_path.is_absolute():
        weights_path = Path.home() / 'Projects' / 'olab_models' / weights_path
    if not weights_path.is_file():
        raise SystemExit(f'Checkpoint not found: {weights_path}')
    return str(weights_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', required=True,
                        help='Local checkpoint; relative names resolve under ~/Projects/olab_models.')
    parser.add_argument('--camera', default='/dev/video0',
                        help='USB camera device (default: %(default)s).')
    parser.add_argument('--variant', choices=('nano', 'small', 'medium', 'large'), default='small')
    parser.add_argument('--seconds', type=float, default=20,
                        help='Run duration (default: %(default)s).')
    parser.add_argument('--fps', type=int, default=5,
                        help='RF-DETR inference target FPS (default: %(default)s).')
    parser.add_argument('--device', default='cpu',
                        help='RF-DETR device, e.g. cpu or cuda (default: %(default)s).')
    parser.add_argument('--stream-port', type=int, default=8000,
                        help='MJPEG stream port; 0 disables streaming (default: %(default)s).')
    args = parser.parse_args()
    weights_path = resolve_weights_path(args.weights)

    camera = CameraUSB(device=args.camera)
    camera.start()
    if not camera.camOn:
        raise SystemExit(f'Camera failed to start: {args.camera}; see the logged error above.')

    for name, algorithm in TRACKERS:
        camera.addTracker(name, algorithm, decorate=True)
    if set(camera.trackers) != {name for name, _ in TRACKERS}:
        camera.stop()
        raise SystemExit('Tracker setup failed; install olab-camera[tracking] and see the logged error above.')

    def track_rfdetr_result(args_dict):
        result = args_dict['result']
        payload = {key: result[key] for key in ('xyxy', 'class_id', 'class', 'class_conf')}
        if len(result['masks']) == len(result['xyxy']):
            payload['masks'] = result['masks']
        # BoT-SORT can use an image for camera-motion compensation. The latest
        # BGR capture is the closest available frame at callback time.
        results = camera.updateTrackers(payload, tuple(name for name, _ in TRACKERS),
                                         frame=camera.getFrameCopy(), timestamp=time.monotonic())
        print({name: None if tracked is None else tracked['track_id']
               for name, tracked in results.items()})

    camera.addRFDETR('detect', task='detect', model_variant=args.variant,
                     weights_path=weights_path, fps_target=args.fps,
                     postFunction=track_rfdetr_result, decorate=False,
                     device=args.device)
    if 'detect' not in camera.rfdetr:
        camera.stop()
        raise SystemExit('addRFDETR failed; check --weights and the rfdetr extra.')

    if args.stream_port:
        camera.startStream(port=args.stream_port)
        if camera.streamURL:
            print(f'View tracker overlays: {camera.streamURL}')
    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            feature = camera.rfdetr.get('detect')
            if feature is None or (not feature.isThreadActive and not feature.deque):
                raise RuntimeError('RF-DETR worker stopped; see the logged error above.')
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        feature = camera.rfdetr.get('detect')
        if feature is not None:
            feature.stop()
        camera.stop()


if __name__ == '__main__':
    main()
