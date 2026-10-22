"""Real-hardware check for the Mic.stop() / Speaker.stop() 'always close' fix. Needs an audio device; not run by pytest.

Leak detector: pyaudio keeps every OPEN stream in `PyAudio._streams`; close() removes it. After a stop, the count must
return to its baseline. (A stream whose stop_stream() raised and whose close() was skipped stays in that set -- that is
the bug.) For hw: devices opened directly by this process we also count /dev/snd file descriptors.

Checks (each prints PASS/FAIL):
  1 Mic healthy start/stop x3: no errors, stream released each time.
  2 Mic with a REAL open stream whose stop_stream() is forced to raise: stop() must still close it
    (stream released, exactly one 'Could not stop stream' message, a restart works -> device not left busy).
  3 Mic with close() forced to raise: one 'Could not close stream' message, mic.stream is None; the script then
    closes the stream itself so it does not leak.
  4 Speaker (default output, quiet 0.3 s tone) healthy play: stream released.
  5 Speaker with stop_stream() forced to raise: play() must raise OSError AND the stream must still be released.
  Optional --unplug: real-world version of 2 -- pull the USB mic while recording, then stop().

To see checks 2 and 5 FAIL on the code from before the fix, point PYTHONPATH at an older olab_audio `src`
directory (e.g. extract commit e2d10c9 with `git archive`); the script prints which olab_audio it imported.
See hardware_tests/README.md.
"""
import argparse
import os
import sys
import time

import numpy as np

sys.stdout.reconfigure(line_buffering=True)

import olab_audio  # noqa: E402
from olab_audio import Mic, Speaker, get_input_devices, get_output_devices  # noqa: E402
from olab_audio import device as _dev  # noqa: E402
import olab_audio.speaker as _speaker_mod  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print(f'  [{"PASS" if ok else "FAIL"}] {name} {detail}')


def open_streams():
    return len(_dev.audio._get()._streams)


def snd_fds():
    n = 0
    for fd in os.listdir('/proc/self/fd'):
        try:
            if os.readlink(f'/proc/self/fd/{fd}').startswith('/dev/snd/pcm'):
                n += 1
        except OSError:
            pass
    return n


def raiser(msg):
    def f(*a, **k):
        raise OSError(-9999, msg)
    return f


def pick_mic(args):
    devs = get_input_devices()
    for d in devs:
        if args.mic_name:
            if args.mic_name.lower() in d['name'].lower():
                return d
        elif 'hw:' in d['name']:
            return d
    raise SystemExit(f'no input device found (use --mic-name); candidates: {[d["name"] for d in devs]}')


def mic_checks(args):
    d = pick_mic(args)
    print(f'\n== Mic on [{d["deviceID"]}] {d["name"]} channels={args.channels}')
    errs = []
    base_streams, base_fds = open_streams(), snd_fds()

    def mk():
        return Mic(deviceID=d['deviceID'], channels=args.channels, samplerate=args.rate,
                   excFunc=lambda msg: errs.append(msg))

    # 1 healthy cycles
    ok = True
    for i in range(3):
        m = mk()
        m.start()
        if not m.micOn:
            check('1 healthy start', False, f'start failed: {errs}')
            return
        time.sleep(1.0)
        m.stop()
        ok &= (open_streams() == base_streams) and not errs
    check('1 healthy start/stop x3 releases the stream, no errors', ok,
          f'streams {open_streams()} (baseline {base_streams}) fds {snd_fds()} (baseline {base_fds}) errs={errs}')

    # 2 stop_stream raises on a REAL stream
    errs.clear()
    m = mk()
    m.start()
    time.sleep(0.5)
    real = m.stream
    real.stop_stream = raiser('forced stop_stream failure')
    m.stop()
    stop_msgs = [e for e in errs if 'Could not stop stream' in e]
    released = open_streams() == base_streams
    check('2 stop_stream raising: stream still closed/released', released,
          f'streams {open_streams()} (baseline {base_streams}) fds {snd_fds()} (baseline {base_fds})')
    check('2 exactly one "Could not stop stream" message, mic.stream is None', len(stop_msgs) == 1 and m.stream is None,
          f'msgs={errs}')
    if not released:   # old behaviour leaked it: clean up so later checks are meaningful
        try:
            real.stop_stream = type(real).stop_stream.__get__(real)
            real.close()
        except Exception as e:
            print('  (cleanup of leaked stream failed:', e, ')')
    errs.clear()
    m2 = mk()
    m2.start()
    check('2 restart on the same device works (not left busy)', m2.micOn, f'errs={errs}')
    time.sleep(0.5)
    m2.stop()

    # 3 close raises
    errs.clear()
    m = mk()
    m.start()
    time.sleep(0.5)
    real = m.stream
    real_close = real.close
    real.close = raiser('forced close failure')
    m.stop()
    close_msgs = [e for e in errs if 'Could not close stream' in e]
    check('3 close raising: one "Could not close stream" message, mic.stream is None',
          len(close_msgs) == 1 and m.stream is None, f'msgs={errs}')
    real_close()   # we forced the failure, so release it ourselves
    check('3 (cleanup) stream released afterwards', open_streams() == base_streams)

    if args.unplug:
        errs.clear()
        m = mk()
        m.start()
        print('\n  >>> UNPLUG the USB mic/interface NOW (while it is recording), wait 2 s, then press Enter.')
        input()
        m.stop()
        print('  stop() after real unplug -> excFunc messages:', errs)
        print('  streams now:', open_streams(), '(baseline', base_streams, ')  <- should be equal if the fix works')
        print('  >>> Re-plug it, wait for it to enumerate, press Enter to test a restart.')
        input()
        _dev.reinit_audio()
        d = pick_mic(args)   # PortAudio indexes can change after a re-plug; re-resolve (mk() reads `d`)
        print(f'  re-resolved device: [{d["deviceID"]}] {d["name"]}')
        m = mk()
        m.start()
        print('  restart after replug: micOn =', m.micOn, 'errs=', errs)
        time.sleep(0.5)
        m.stop()


def speaker_checks(args):
    outs = [d for d in get_output_devices() if d['name'] == 'default']
    if not outs:
        print('\n== Speaker: no output device named "default"; skipping checks 4-5')
        return
    print(f'\n== Speaker on [{outs[0]["deviceID"]}] {outs[0]["name"]}  (plays a quiet 0.3 s tone)')
    base = open_streams()
    t = np.arange(int(44100 * 0.3)) / 44100.0
    tone = np.sin(2 * np.pi * 440.0 * t)
    sp = Speaker(deviceID=outs[0]['deviceID'])

    sp.play(tone, volume=0.05)
    check('4 healthy play releases the stream', open_streams() == base and sp.stream is None,
          f'streams {open_streams()} (baseline {base})')

    real_audio = _speaker_mod.audio

    class Proxy:
        def open(self, **kw):
            s = real_audio.open(**kw)
            s.stop_stream = raiser('forced stop_stream failure')
            self.last = s
            return s

        def __getattr__(self, n):
            return getattr(real_audio, n)

    proxy = Proxy()
    _speaker_mod.audio = proxy
    raised = False
    try:
        sp.play(tone, volume=0.05)
    except OSError:
        raised = True
    finally:
        _speaker_mod.audio = real_audio
    released = open_streams() == base
    check('5 stop_stream raising: play() re-raises OSError', raised)
    check('5 ...and the stream is still closed/released, sp.stream is None', released and sp.stream is None,
          f'streams {open_streams()} (baseline {base})')
    if not released:
        try:
            del proxy.last.stop_stream
            proxy.last.close()
        except Exception as e:
            print('  (cleanup of leaked stream failed:', e, ')')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mic-name', default=None, help='substring of the input device name (default: first hw: input)')
    ap.add_argument('--channels', type=int, default=1)
    ap.add_argument('--rate', type=int, default=None, help='default: device default')
    ap.add_argument('--unplug', action='store_true', help='also run the manual unplug-while-recording check')
    ap.add_argument('--no-speaker', action='store_true')
    args = ap.parse_args()
    print('olab_audio from:', os.path.dirname(olab_audio.__file__))
    mic_checks(args)
    if not args.no_speaker:
        speaker_checks(args)
    bad = [n for n, ok in results if not ok]
    print('\nSUMMARY:', 'ALL PASS' if not bad else f'{len(bad)} FAIL -> {bad}')


if __name__ == '__main__':
    main()
