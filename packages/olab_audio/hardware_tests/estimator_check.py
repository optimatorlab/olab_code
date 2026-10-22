"""Validate the overflow lost-frame estimator MicArray uses (hardware diagnostic; not run by pytest).

Uses raw pyaudio, not MicArray. Opens N channels int32 and discards the data (only per-callback
timing is kept). Open the device's FULL width (--channels, default 18 for a UMC1820): a partial-width open
overflowed constantly on that device and invalidates the estimator check. Three modes:

  baseline  8192 frames, no overflows expected. Measures the drift ratio
            (captured frames per monotonic second) = UMC clock vs laptop clock.
  inject    8192 frames (production setting); the callback sleeps 0.3 s about every 5 s
            (longer than the ~171 ms buffer) to force controlled overflows.
  natural   1024 frames; the laptop overflows by itself about every 3.1 s.

Per-callback log is saved to <out>.npz; analysis is printed at the end and can be re-run with
--analyze <out>.npz [--ratio R]. Judge inject/natural against the BASELINE ratio (pass --ratio),
not a flat 48000: a 20 ppm clock difference is ~576 samples over 10 min.

Estimators (for each callback i whose status has paInputOverflow, gap measured from callback i-1):
  adc   : round((adc[i]-adc[i-1])*rate) - frames[i-1]          (PortAudio input_buffer_adc_time)
  mono  : round((mono[i]-mono[i-1])*ratio) - frames[i-1]       (callback-entry time.monotonic())
  Both clamped at >= 0. Unflagged gaps are reported separately and never filled.
"""
import argparse
import sys
import time

import numpy as np


def run(args):
    import pyaudio

    pa = pyaudio.PyAudio()
    idx = None
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d['maxInputChannels'] > 0 and args.name.lower() in d['name'].lower():
            idx, info = i, d
            break
    if idx is None:
        raise SystemExit(f'No input device matching {args.name!r}')
    frames = args.frames
    print(f"Using [{idx}] {info['name']}, {args.channels}ch int32 @ {args.rate}, "
          f"{frames} frames/buffer, mode={args.mode}, {args.minutes} min", flush=True)

    mono, adc, nfr, ovf = [], [], [], []
    state = {'last_inject': None}

    def cb(in_data, frame_count, time_info, status):
        now = time.monotonic()
        mono.append(now)
        adc.append(time_info['input_buffer_adc_time'])
        nfr.append(frame_count)
        ovf.append(bool(status & pyaudio.paInputOverflow))
        if args.mode == 'inject':
            if state['last_inject'] is None:
                state['last_inject'] = now
            elif now - state['last_inject'] >= 5.0:
                time.sleep(0.3)
                state['last_inject'] = time.monotonic()
        return (None, pyaudio.paContinue)

    stream = pa.open(format=pyaudio.paInt32, channels=args.channels, rate=args.rate, input=True,
                     input_device_index=idx, frames_per_buffer=frames, stream_callback=cb)
    t0 = time.monotonic()
    stream.start_stream()
    try:
        while time.monotonic() - t0 < args.minutes * 60:
            time.sleep(10)
            print(f'{time.monotonic() - t0:6.0f}s cb={len(mono)} overflows={sum(ovf)}', flush=True)
    except KeyboardInterrupt:
        pass
    stream.stop_stream()
    stream.close()
    pa.terminate()
    np.savez(args.out, mono=mono, adc=adc, nfr=nfr, ovf=ovf, rate=args.rate, frames=frames, mode=args.mode)
    print(f'saved {args.out}.npz' if not args.out.endswith('.npz') else f'saved {args.out}')


def analyze(path, ratio_arg):
    z = np.load(path)
    mono, adc, nfr, ovf = z['mono'], z['adc'], z['nfr'].astype(np.int64), z['ovf']
    rate = int(z['rate'])
    n = len(mono)
    if n < 3:
        print('too few callbacks')
        return
    elapsed = mono[-1] - mono[0]
    captured = int(nfr[1:].sum())          # frames delivered after the first callback
    own_ratio = captured / elapsed
    adc_ratio = (adc[-1] - adc[0]) * rate / elapsed   # ADC-time seconds per monotonic second, in frames
    ratio = ratio_arg if ratio_arg else own_ratio
    print(f"\n== {path}  mode={z['mode']} frames/buf={int(z['frames'])} callbacks={n} elapsed={elapsed:.2f}s")
    print(f'overflow-flagged callbacks: {int(ovf.sum())}')
    print(f'captured frames (after 1st callback): {captured}')
    print(f'this-run frames per monotonic second:  {own_ratio:.3f}  ({(own_ratio / rate - 1) * 1e6:+.1f} ppm vs nominal)')
    print(f'ADC-time rate vs monotonic (frames/s): {adc_ratio:.3f}  ({(adc_ratio / rate - 1) * 1e6:+.1f} ppm)')
    print(f'ratio used for judging: {ratio:.3f}' + ('  (from --ratio)' if ratio_arg else '  (this run; only valid for a clean baseline)'))

    dadc = np.diff(adc) * ratio - nfr[:-1]         # excess frames between callback i-1 and i, ADC clock
                                                   # (ADC time tracks the laptop clock, so convert with the same ratio)
    dmono = np.diff(mono) * ratio - nfr[:-1]       # same on the monotonic clock
    flagged = ovf[1:]
    expected_total = ratio * elapsed
    print(f'expected frames by clock (ratio*elapsed): {expected_total:.1f}')

    for name, d in (('adc', dadc), ('mono', dmono)):
        est = np.maximum(np.round(d[flagged]), 0).sum()
        resid = captured + est - expected_total
        print(f'[{name:4s}] est_lost={int(est):8d}  captured+est-expected = {resid:+10.1f} frames '
              f'({resid / int(z["frames"]):+.2f} buffers, {resid / rate * 1000:+.1f} ms)')

    unfl = ~flagged
    big = unfl & (np.abs(dadc) > 0.5 * int(z['frames']))
    print(f'unflagged callbacks with ADC gap > half a buffer: {int(big.sum())}')
    print(f'ADC jitter on unflagged callbacks (frames): median|{np.median(np.abs(dadc[unfl])):.1f}| '
          f'p99|{np.percentile(np.abs(dadc[unfl]), 99):.1f}|')
    if flagged.any():
        print('per-flag gap estimates (frames): index  adc  mono')
        for k in np.flatnonzero(flagged)[:25]:
            print(f'   cb {k + 1:6d}  {dadc[k]:10.1f} {dmono[k]:10.1f}')
        if flagged.sum() > 25:
            print(f'   ... ({int(flagged.sum()) - 25} more)')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mode', choices=['baseline', 'inject', 'natural'])
    ap.add_argument('--name', default='UMC1820')
    ap.add_argument('--channels', type=int, default=18)
    ap.add_argument('--rate', type=int, default=48000)
    ap.add_argument('--frames', type=int, default=None, help='default: 8192 (baseline/inject), 1024 (natural)')
    ap.add_argument('--minutes', type=float, default=10.0)
    ap.add_argument('--out', default=None, help='default: est_<mode>.npz')
    ap.add_argument('--ratio', type=float, default=None, help='baseline frames/monotonic-second to judge against')
    ap.add_argument('--analyze', default=None, help='re-analyze a saved .npz instead of capturing')
    args = ap.parse_args()
    if args.analyze:
        analyze(args.analyze, args.ratio)
        return
    if not args.mode:
        ap.error('--mode is required unless --analyze is given')
    if args.frames is None:
        args.frames = 1024 if args.mode == 'natural' else 8192
    if args.out is None:
        args.out = f'est_{args.mode}.npz'
    run(args)
    analyze(args.out if args.out.endswith('.npz') else args.out + '.npz', args.ratio)


if __name__ == '__main__':
    sys.exit(main())
