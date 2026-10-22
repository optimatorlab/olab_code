"""Real-hardware smoke test for olab_audio.MicArray. Needs an audio interface; not run by pytest.

The defaults match the rig it was written on: a Behringer UMC1820 (--name UMC1820) with mics on jack 1
(UMC analog 1) and jack 14 (an ADAT-connected preamp). Override with --name and --jacks (1-based jacks).

Steps: (1) live meters for --monitor seconds (talk / clap near the mics),
(2) record --seconds to ./takes/<basename>_<label>.wav + manifest (a CLAP cue is printed),
(3) read the WAVs back with stdlib wave and print equal-length / width / gap / duration checks,
    plus an AUDIT line comparing the file's audio duration with the wall-clock time.
The monitor output includes callback timing and overflow-flag counts (it reads MicArray's private
attributes for diagnostics, so it may need updating if those change).

Use --frames 1024 to force natural overflows (~every 3 s at 18 channels) and exercise the zero-fill path.
See hardware_tests/README.md for how to run it and what to look for.
"""
import argparse
import json
import os
import sys
import time
import wave

from olab_audio import MicArray


def main():
    sys.stdout.reconfigure(line_buffering=True)   # prompts must appear live even when piped through grep
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='UMC1820')
    ap.add_argument('--jacks', default='1,14', help='comma-separated 1-based jacks (default 1,14 = UMC analog 1 + ADA8200 port 4)')
    ap.add_argument('--frames', type=int, default=8192)
    ap.add_argument('--monitor', type=float, default=5.0)
    ap.add_argument('--seconds', type=float, default=20.0)
    ap.add_argument('--out', default='takes')
    args = ap.parse_args()

    jacks = [int(j) for j in args.jacks.split(',')]
    channels = [{'label': f'J{j}', 'channel': j, 'meta': {'jack': j, 'note': 'smoke'}} for j in jacks]
    errors = []
    arr = MicArray(channels, deviceName=args.name, samplerate=48000, frames_per_buffer=args.frames,
                   excFunc=lambda msg: (errors.append(msg), print('EXC:', msg)))

    # Diagnostics: time MicArray's own callback body and count PortAudio overflow flags.
    import pyaudio
    cbstats = {'n': 0, 'flagged': 0, 'max_ms': 0.0, 'sum_ms': 0.0}
    orig_process = arr._process

    def timed_process(in_data, frame_count, time_info, status):
        t = time.perf_counter()
        orig_process(in_data, frame_count, time_info, status)
        ms = (time.perf_counter() - t) * 1000.0
        cbstats['n'] += 1
        cbstats['flagged'] += 1 if (status & pyaudio.paInputOverflow) else 0
        cbstats['max_ms'] = max(cbstats['max_ms'], ms)
        cbstats['sum_ms'] += ms
    arr._process = timed_process

    if not arr.start():
        raise SystemExit(f'start() failed: {errors}')
    print(f'started, samplerate={arr.samplerate}, labels={arr.labels}')

    t0 = time.monotonic()
    while time.monotonic() - t0 < args.monitor:
        time.sleep(1.0)
        # _stream/_stream_frames are private MicArray attributes: used for diagnostics only.
        st = arr._stream
        print(f'  active={st.is_active() if st else None} stream_frames={arr._stream_frames} '
              f'cb={cbstats["n"]} overflow_flagged={cbstats["flagged"]} cb_max_ms={cbstats["max_ms"]:.2f}',
              ' db', {k: round(float(v), 1) for k, v in arr.db.items()},
              ' peak', {k: round(float(v), 1) for k, v in arr.peak_db.items()})

    base = time.strftime('smoke_%H%M%S')
    print(f'\n*** MONITOR DONE. Starting a {args.seconds}s recording in 2 s -- get ready to CLAP near the mics ***')
    time.sleep(2.0)
    print(f'*** RECORDING NOW ({args.seconds}s) -> {args.out}/{base}_* -- CLAP once in the first few seconds ***')
    done = []
    if not arr.recordStart(args.out, base, timeLimitSec=args.seconds, postFunc=lambda take: done.append(take)):
        arr.stop()
        raise SystemExit(f'recordStart failed: {errors}')
    t_rec = time.monotonic()
    while not done and time.monotonic() - t_rec < args.seconds + 15:
        time.sleep(0.2)
    wall = time.monotonic() - t_rec
    take = done[0] if done else arr.recordStop()
    arr.stop()
    if not take:
        raise SystemExit('no take returned')

    n = max(cbstats['n'], 1)
    print(f'\ncallback stats over whole run: cb={cbstats["n"]} overflow_flagged={cbstats["flagged"]} '
          f'mean_ms={cbstats["sum_ms"] / n:.3f} max_ms={cbstats["max_ms"]:.2f} (buffer = {args.frames / 48.0:.0f} ms)')
    print(f'\nwall elapsed (recordStart -> take final): {wall:.2f}s')
    if take.get('gaps'):
        est = sum(g.get('est_lost_frames') or 0 for g in take['gaps'])
        real = take['frames'] - est
        print(f'AUDIT: file frames={take["frames"]} ({take["frames"] / 48000:.2f}s)  zero-filled={est} '
              f'real captured={real} ({real / 48000:.2f}s)  wall={wall:.2f}s  '
              f'=> real+filled should be ~wall; it is {(take["frames"] / 48000) - wall:+.2f}s off')
    print('take frames:', take.get('frames'), ' gaps:', take.get('gaps'), ' error:', take.get('error'))
    lens = {}
    for label, path in take['files'].items():
        with wave.open(path, 'rb') as wf:
            lens[label] = wf.getnframes()
            print(f'  {label}: {path}  ch={wf.getnchannels()} width={wf.getsampwidth()} '
                  f'rate={wf.getframerate()} frames={wf.getnframes()} ({wf.getnframes() / wf.getframerate():.3f}s)')
    print('equal lengths:', len(set(lens.values())) == 1)
    with open(take['manifest']) as f:
        m = json.load(f)
    print('manifest keys:', sorted(m))
    for k in ('startedAtWall', 'measuredFramesPerWallSecond', 'totalFrames'):
        print(f'  {k}: {m.get(k)}')
    print('manifest per-label meta:', {k: v.get('meta') for k, v in m.get('channels', m.get('labels', {})).items()}
          if isinstance(m.get('channels', m.get('labels', {})), dict) else 'see manifest file')
    if errors:
        print('\nexcFunc messages:', errors)


if __name__ == '__main__':
    main()
