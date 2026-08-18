#!/usr/bin/env python3
"""Replay a raw GENX320 EVT2.0 event recording made with `--record DIR` in
genx_hardware_test.py (or CameraOpenMV.addEventRecorder() directly).

Reads back the same EventBatch objects the camera produced live, so this is
also a reference for building your own after-the-fact motion analysis over
`batch.x`/`batch.y`/`batch.polarity`/`batch.timestamps_us` -- this script
just renders them back to the same decaying ON/OFF preview used live, either
in a window or to an .mp4.

Examples
--------
    # Play back a recording in a window, at the recorded sensor pace.
    python genx_replay.py ./genx-session

    # Render the same recording to a watchable .mp4 instead.
    python genx_replay.py ./genx-session --out ./genx-session-replay.mp4
"""

import argparse
import json
from pathlib import Path

import cv2

from olab_camera.openmv_events import EventPreview, replay_events


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('recording_dir', help='Directory written by addEventRecorder() '
                     '(must contain manifest.json and events.jsonl).')
    ap.add_argument('--out', metavar='PATH', default=None,
                    help='Render to this .mp4 instead of showing a live window.')
    ap.add_argument('--fps', type=float, default=30.0,
                    help='Render/window rate (default: 30). Batches are replayed '
                         'in recorded order at this fixed rate, not at the '
                         'original sensor-time spacing.')
    ap.add_argument('--width', type=int, default=320, help='Sensor width (default: 320).')
    ap.add_argument('--height', type=int, default=320, help='Sensor height (default: 320).')
    args = ap.parse_args()

    manifest_path = Path(args.recording_dir) / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    print(f'Replaying {args.recording_dir!r}  (format={manifest["format"]!r}, '
          f'metadata={manifest["metadata"]})')

    preview = EventPreview(shape=(args.height, args.width))
    writer = None
    if args.out:
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(args.out, fourcc, args.fps, (args.width, args.height))

    batches = 0
    events = 0
    try:
        for batch in replay_events(args.recording_dir):
            batches += 1
            events += batch.count
            frame = preview.render(batch)
            if writer is not None:
                writer.write(frame)
            else:
                cv2.imshow('genx_replay', frame)
                if cv2.waitKey(int(1000 / args.fps)) & 0xFF == ord('q'):
                    break
    finally:
        if writer is not None:
            writer.release()
        else:
            cv2.destroyAllWindows()

    print(f'batches={batches}  events={events}')
    if args.out:
        print(f'wrote {args.out!r}')


if __name__ == '__main__':
    main()
