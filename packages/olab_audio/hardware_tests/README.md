# olab_audio hardware tests

Manual checks that need real audio hardware. They are **not** run by `pytest` or CI, and are not part
of the published package. The unit tests use fake PortAudio streams, and that is not enough: the fake
stream hid two real `MicArray` bugs (see below) that only showed on a Behringer UMC1820.

Outstanding hardware validation is tracked in
[#81](https://github.com/optimatorlab/olab_code/issues/81); results from these scripts belong there.

## Running

Run from this directory, inside the venv that has `olab_audio` installed (editable install of a checkout,
or a released version), and on a machine that can see the audio card (not inside a container without
`/dev/snd`):

```bash
cd packages/olab_audio/hardware_tests
python <script>.py ...
```

To test a checkout without installing it, set `PYTHONPATH=<checkout>/packages/olab_audio/src`. The scripts
print which `olab_audio` they imported. PortAudio/ALSA print some noise to stderr; filter it with
`2>&1 | grep -v '^ALSA\|^Cannot connect\|^jack\|JackShm'` if you like. Scripts that record write into
`./takes/` or `./est_*.npz` in the directory you run them from (both are git-ignored).

The defaults match the rig these were written on (a UMC1820, plus an ADAT-connected preamp): change
`--name`, `--jacks` and `--channels` for other hardware.

## `micarray_smoke.py`

End-to-end check of `MicArray` on a real interface: starts the array, prints live levels (with callback
timing and overflow-flag counts) for `--monitor` seconds, records `--seconds` (clap once when prompted),
reads the WAVs back, and prints an audit comparing the audio duration with wall-clock time.

```bash
python micarray_smoke.py --name UMC1820 --jacks 1,14
python micarray_smoke.py --frames 1024        # force natural overflows to exercise the zero-fill path
```

Look for `active=True`, `overflow_flagged=0`, `gaps: []`, equal file lengths with sample width 4, a non-null
`startedAtWall`, `measuredFramesPerWallSecond` close to the sample rate, and an AUDIT line close to zero.
Also check a clap shows up in the WAVs.

## `estimator_check.py`

Checks the lost-frame estimator `MicArray` uses for overflows, with raw pyaudio (no `MicArray`). Modes:
`baseline` (clean stream, measures the clock-drift ratio), `inject` (the callback stalls on purpose at 8192
frames), `natural` (1024 frames, the laptop overflows by itself). Run `baseline` first and pass its
"frames per monotonic second" to the others with `--ratio`.

```bash
python estimator_check.py --mode baseline
python estimator_check.py --mode inject --ratio 47996.654
python estimator_check.py --mode natural --ratio 47996.654
```

Use the device's full channel width: a 14-of-18-channel open overflowed constantly on a UMC1820, which
makes the check meaningless. `--analyze <file>.npz --ratio <r>` re-runs the analysis on a saved run.

## `stop_close_check.py`

Checks that `Mic.stop()` and `Speaker.stop()` always close their stream, by forcing `stop_stream()` or
`close()` to raise on a real open stream and checking that the stream is still released (pyaudio's open-stream
registry and `/dev/snd` descriptors). `--unplug` adds a manual pull-the-cable check; on the UMC1820 a real
unplug did not make `stop_stream()` raise, so the forced-failure checks are the meaningful ones.

```bash
python stop_close_check.py --mic-name UMC --no-speaker
```

All checks should PASS. To see checks 2 and 5 FAIL on the code from before the fix, extract the old source
and point `PYTHONPATH` at it:

```bash
mkdir -p /tmp/olab_old && git archive e2d10c9 packages/olab_audio/src | tar -x -C /tmp/olab_old
PYTHONPATH=/tmp/olab_old/packages/olab_audio/src python stop_close_check.py --mic-name UMC --no-speaker
```

## Lessons these scripts encode

- `Stream.get_time()` on a freshly opened callback stream raised `OSError -9986` and killed the stream on a
  UMC1820 (zero callbacks). `MicArray` never calls it.
- Opening 14 of the UMC1820's 18 inputs overflowed about every 1.4 s even with raw pyaudio; opening all 18
  was clean. `MicArray` opens the device's full input width.
- With an overflowing stream the gap estimate was about 2.4x too high, so overflow handling is a safety net,
  not something to rely on.
