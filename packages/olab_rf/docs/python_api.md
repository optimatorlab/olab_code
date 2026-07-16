# olab_rf Python API

`olab_rf` is a Python library for receive-only RF workflows. The web UI is only a
demo/test surface over the same backend objects.

Use one `SessionManager` per receiver. A manager owns one active receiver
workflow at a time; starting a new workflow stops the previous one.

## Setup

Normal installation (no `olab_code` checkout required):

```bash
python3 -m venv venv
source venv/bin/activate
pip install "olab-rf[web,ais,pyrtlsdr,nats] @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_rf"
```

Once release wheels exist, prefer pinning the release's exact URL and
SHA-256 hash instead of a git reference. That installs the Python
dependencies for:

- web demo: `fastapi`, `uvicorn`
- AIS parsing: `pyais`
- NATS transport support: `msgpack`, `nats-py`
- experimental direct RTL-SDR Python access: `pyrtlsdr`

NumPy (PCM voice segmentation and IQ peak estimation) is always installed —
it's a base dependency, not an extra.

**Local development**, against an `olab_code` checkout, to run the test
suite or make changes:

```bash
pip install -e "packages/olab_rf[dev,web,ais,pyrtlsdr,nats]"
pytest packages/olab_rf/tests -q
```

For the live voice-segment notebook, add the optional Jupyter Notebook
server. Normal installation: add `notebook` to the extras list in the
`pip install "olab-rf[...] @ git+..."` command above. Local development:

```bash
pip install -e "packages/olab_rf[notebook]"
```

Pip does not install system SDR tools. Live receiver workflows still need
command-line tools such as `rtl_power`, `rtl_sdr`, `rtl_fm`, `rtl_ais`, and
`readsb` installed separately or configured in `olab_rf.yaml`.

NumPy is a base dependency used by voice segmentation and the normal IQ scan
path, which uses the system `rtl_sdr` recorder. The
`pyrtlsdr` extra is for lower-level direct-library experiments and requires a
compatible system `librtlsdr`.

Create a manager from local config:

```python
from olab_rf import SessionManager
from olab_rf.config import load_config
from olab_rf.history import SqliteHistory

config = load_config("olab_rf.yaml")
history = SqliteHistory(config.history.sqlite_path)
manager = SessionManager.from_config(config, history=history)
```

If no local hardware/config is needed, instantiate directly. This is useful for
synthetic replay, catalog inspection, model tests, history-only inspection, and
code that supplies receiver/config objects manually. Live SDR workflows still
need hardware and decoder paths.

```python
from olab_rf import SessionManager

manager = SessionManager()
```

## SDRTrunk Digital Listening

SDRTrunk profile listening is receive-only and uses SDRTrunk's native speaker
output. Configure local-only `sdrtrunk` paths and a profile system in ignored
`olab_rf.yaml`; the playlist must already be selected in SDRTrunk's GUI because
the validated launcher has no profile-selection argument.

```python
session = manager.start_digital_listen(system_id="local-p25-probe")
status = manager.current_digital_listen_status()
print(status.state, status.process_running, status.error)

# Poll to retain launcher stderr and notice an unexpected GUI exit.
manager.poll()
manager.stop()
```

The digital status reports launcher/profile/JMBE readiness and process state.
It does not decode P25 in Python, modify SDRTrunk playlists, parse call logs,
or capture audio recordings.

Use this local-only configuration shape (with absolute paths on the operator's
machine):

```yaml
sdrtrunk:
  launcher_path: /path/to/sdrtrunk/bin/sdr-trunk
  java_path: /path/to/sdrtrunk/bin/java
  working_directory: /path/to/sdrtrunk
  profile_path: /path/to/SDRTrunk/playlist/local-digital-probe.xml
  jmbe_path: /path/to/SDRTrunk/jmbe
digital_system_catalog:
  systems:
    - id: local-p25-probe
      label: Local P25 probe
      backend: sdrtrunk
      mode: profile
      frequency_hz: 424300000
      protocol: p25_phase1
      nac_hex: "29A"
      sdrtrunk_profile_path: /path/to/SDRTrunk/playlist/local-digital-probe.xml
```

`sdrtrunk_profile_path` is checked for provenance and readiness. It is not
passed to SDRTrunk, which must already have this playlist selected by the
operator.

## Active Channel Scan

This is the recommended Python workflow for finding an active known channel in a
catalog range such as FRS/GMRS.

This workflow has been validated against a local FRS/GMRS walkie-talkie setup.
Leave `gain_db=None` for automatic/default gain unless local testing shows
front-end overload. If your SDR overloads on a nearby transmitter, pass a local
manual value such as `gain_db=0`; do not treat that as a package-wide default.
For the quick answer, use `scan.best_matched_candidate`.

```python
from time import sleep

from olab_rf import SessionManager
from olab_rf.config import load_config
from olab_rf.history import SqliteHistory

config = load_config("olab_rf.yaml")
history = SqliteHistory(config.history.sqlite_path)
manager = SessionManager.from_config(config, history=history)

scan = manager.find_active_channels(
    range_id="frs_gmrs",
    duration_sec=10,
    gain_db=None,
)

while scan.status == "running":
    sleep(0.25)
    manager.poll()
    scan = manager.current_frequency_scan()

print(scan.status, scan.error)

for candidate in scan.candidates:
    print(
        candidate.label or "(unmatched)",
        candidate.frequency_hz,
        candidate.matched_frequency_hz,
        candidate.power_db,
        candidate.margin_db,
    )

best = scan.best_matched_candidate
if best:
    print(
        best.label,
        best.matched_frequency_hz,
        best.frequency_hz,
        best.frequency_offset_hz,
        best.margin_db,
    )

# Close the SQLite connection when the application is done with it.
history.close()
```

`scan.candidates` is already ranked by the backend and includes unmatched peaks.
Use it when you want to inspect artifacts or unknown signals.

`scan.matched_candidates` filters the same scan to catalog-matched channels and
ranks those by `margin_db` when available, otherwise by `power_db`.

`scan.best_matched_candidate` is the first item from `matched_candidates`, or
`None`.

For `rtl_power`, scans are non-blocking. Call `manager.poll()` until the scan is
not `running` before treating candidates as final. Polling faster than
`rtl_power` emits sweeps is fine; repeated status values simply mean no new
sweep output has arrived.

`history.close()` is not part of the scan workflow. It closes the SQLite
connection when your program is finished with the history object. Long-running
applications can keep the history object open for the life of the application.

## Baseline Then Active Scan

Use a baseline when you want candidate margins over a quiet RF environment.

```python
from time import sleep

baseline_scan = manager.capture_range_baseline(
    range_id="frs_gmrs",
    duration_sec=10,
    gain_db=None,
)

while baseline_scan.status == "running":
    sleep(0.25)
    manager.poll()
    baseline_scan = manager.current_frequency_scan()

baseline = manager.latest_frequency_baseline()

scan = manager.find_active_channels(
    range_id="frs_gmrs",
    duration_sec=10,
    baseline=baseline,
    gain_db=None,
)

while scan.status == "running":
    sleep(0.25)
    manager.poll()
    scan = manager.current_frequency_scan()

best = scan.best_matched_candidate
if best:
    print(best.label, best.matched_frequency_hz, best.margin_db)
```

## Range Scans

Use `start_range_scan(...)` when you want a general range scan rather than the
channel-specific `find_active_channels(...)` helper.

Supported `backend` values:

- `rtl_power`: default broad sweep backend. Reports coarse observed bin centers.
- `rtl_sdr_iq`: channelized IQ peak backend. Requires `numpy` and the system
  `rtl_sdr` command-line recorder; slower, more precise around known channels,
  and not intended for broad sweeping.

Catalog range:

```python
scan = manager.start_range_scan(
    range_id="frs_gmrs",
    backend="rtl_power",
    duration_sec=10,
    gain_db=None,
)
```

Arbitrary grid:

```python
scan = manager.start_range_scan(
    min_freq_hz=150_000_000,
    max_freq_hz=150_500_000,
    step_hz=25_000,
    backend="rtl_power",
    duration_sec=10,
)
```

IQ channel check:

```python
scan = manager.start_range_scan(
    range_id="frs_gmrs",
    backend="rtl_sdr_iq",
    duration_sec=0.25,
    sample_rate_hz=240_000,
)
```

The `rtl_sdr_iq` backend uses NumPy (a base dependency, always installed)
plus the system `rtl_sdr` command-line recorder. It is slower and
channelized; it is not a broad sweep replacement for `rtl_power`.

## Frequency Catalog

`FrequencyCatalog` contains named ranges, known channels, and favorites. The
package ships defaults; `olab_rf.yaml` can override or append ranges.

```python
from olab_rf import FrequencyCatalog

catalog = FrequencyCatalog.default()
match = catalog.match_frequency(462_612_500)
print(match.label)
```

Local YAML uses Hz integers:

```yaml
frequency_catalog:
  ranges:
    - id: local_walkies
      label: Local walkies
      min_freq_hz: 462000000
      max_freq_hz: 468000000
      default_modulation: NFM
      default_bin_size_hz: 12500
      channels:
        - id: local_ch_1
          label: Local Ch 1
          frequency_hz: 462612500
          modulation: NFM
```

## Spectrum And Listen

Use spectrum monitoring for live sweep/waterfall/event views:

```python
manager.start_spectrum(
    preset_id="frs_gmrs",
    threshold_db=12.0,
)

manager.poll()

snapshot = manager.current_spectrum()
history = manager.spectrum_history(limit=20)
events = manager.spectrum_events(limit=20)
```

Set a listen frequency, then start demodulated audio playback:

```python
manager.set_watch_frequency(462_612_500, modulation="NFM")
manager.start_listen()
```

Starting listen stops any active spectrum or scan workflow because the receiver
cannot be shared.

### Voice Segments For Speech-To-Text

For FRS/GMRS transmissions, use the iterator API. It starts `rtl_fm`, gates
static in Python, yields complete mono 16 kHz PCM segments, and stops the
receiver in all cases. It does not play speaker audio.

```python
for segment in manager.iter_voice_segments(
    frequency_hz=462_712_500,
    modulation="NFM",
    duration_sec=120,
    max_segments=10,
    debug_wav_dir="data/voice_segments",
):
    print(segment.duration_sec, segment.rms_db, segment.wav_path)
    audio_blob = segment.to_audio_blob_payload()
```

`to_audio_blob_payload()` returns the current dictionary shape accepted by
`olab_voice.AudioBlob.from_dict(...)`; `olab_rf` does not require or import
`olab_voice`. Use `start_voice_segments(...)`, `poll()`,
`pop_voice_segments()`, and `current_voice_segment_status()` when integrating
the lifecycle into another event loop.

`duration_sec` is the maximum wall-clock time the iterator will keep its SDR
session open. Omit it to run until interrupted. `max_segments` is the maximum
number of completed transmissions to yield. Omit it to yield every completed
transmission until the duration expires or the iterator is closed. Either limit
stops the receiver and exits the iterator; `max_segments` does not limit the
length of an individual transmission. Use `max_segment_sec` for that.

The iterator exposes the same capture and segmentation controls as
`start_voice_segments(...)`:

- `threshold_db=10.0`: the demodulated audio must become this many dB quieter
  than the inactive FM-noise level to count as a carrier. This uses FM
  quieting, not speech loudness. Raise it to require a stronger/cleaner carrier;
  lower it when a weak transmission is missed.
- `pre_roll_ms=200`: audio retained before speech is confirmed. Raise it if
  word starts are clipped; lower it to include less preceding static.
- `hang_time_ms=600`: required below-threshold quiet time before ending a
  transmission. Raise it to preserve pauses between words; lower it to split
  separate transmissions sooner.
- `min_active_ms=120`: sustained above-threshold time required to begin a
  transmission; raise it to ignore short static bursts.
- `min_segment_ms=400`: discard shorter completed transmissions.
- `max_segment_sec=20.0`: force-close longer transmissions so one stuck signal
  does not produce an unbounded payload.
- `rtl_fm_squelch_db=None`: optional decoder-side `rtl_fm -l` squelch. Leave it
  unset for the default Python-side gating.

For an initial live test, use the defaults and save debug WAVs. If a weak
carrier is missed, lower `threshold_db` in 2–3 dB steps. If static alone opens
a segment, increase it. If speech starts late, increase `pre_roll_ms` by 100
ms. If the end of a transmission is cut off, increase `hang_time_ms` by 200 ms.
Leave the channel idle for about one second after starting capture so the
inactive FM-noise level can calibrate before the first transmission. No absolute
bench level is embedded in the library; `threshold_db` is relative to the level
measured in the current RF environment.

For long-running field capture, use the lifecycle API to adjust carrier-gate
settings without restarting the SDR process:

```python
import asyncio

manager.start_voice_segments(frequency_hz=462_712_500)

# After inspecting current_voice_segment_status(), apply to future frames.
manager.update_voice_segment_settings(
    threshold_db=7.0,
    hang_time_ms=800,
)

# Recalibrate after moving locations or changing antennas. This is allowed only
# between transmissions, so an active segment is never corrupted.
manager.reset_voice_segment_calibration()
```

`threshold_db`, `min_active_ms`, `hang_time_ms`, `min_segment_ms`,
`max_segment_sec`, and `pre_roll_ms` are live-adjustable. Frequency,
modulation, gain, sample rate, and backend changes require a new SDR session.
Live speaker tee playback is future work.

### Automated Capture And Status

Most server/event-loop integrations should call `manager.poll()` themselves.
For a simple standalone process, set `auto_poll=True` and the manager runs that
poll loop in a daemon thread. Do not use both approaches for the same manager.

```python
from time import sleep

manager.start_voice_segments(
    frequency_hz=462_712_500,
    auto_poll=True,
    on_event=lambda event: print(event.event, event.state),
    on_segment=lambda segment: print(segment.segment_id),
)

while manager.voice_capture_running():
    status = manager.current_voice_segment_status()
    if status:
        print(status.state, status.capture_running, status.last_frame_rms_db)
    for segment in manager.pop_voice_segments():
        print(segment.segment_id, segment.duration_sec)
    sleep(0.25)
```

`VoiceSegmentStatus.state` is `calibrating`, `idle`, `transmitting`, `stopped`,
or `error`. `capture_running` is the binary receiver-process indicator.
`last_frame_rms_db`, `last_frame_peak_db`, and `last_frame_at` provide a
continuously refreshed input-level meter for clients that poll this status.
`pop_voice_events()` returns durable lifecycle events for polling clients;
`on_event` and `on_segment` provide immediate optional callbacks for simple
local consumers. Callback failures are reported in the voice status and do not
stop the decoder process.

### Framework And NATS Adapters

The core library does not print or depend on a web framework. Adapters should
consume `current_voice_segment_status()`, `pop_voice_events()`, and
`pop_voice_segments()`. The models provide transport-ready dictionaries:
`VoiceSegmentStatus.to_dict()`, `VoiceCaptureEvent.to_dict()`, and
`RadioVoiceSegment.to_dict()`. Segment metadata excludes audio bytes by
default; use `to_audio_blob_payload()` when WAV bytes are needed.

The optional NATS adapter publishes MessagePack payloads on these subjects:

- `rf.voice.status`: live status/meter payload.
- `rf.voice.event`: capture/transmission lifecycle event.
- `rf.voice.segment`: completed segment metadata, without audio bytes.
- `rf.voice.audio`: WAV payload compatible with `olab_voice.AudioBlob`.

An async service normally owns explicit polling rather than using callback
threads:

```python
manager.start_voice_segments(frequency_hz=462_712_500)

while manager.voice_capture_running():
    manager.poll()
    status = manager.current_voice_segment_status()
    if status:
        await transport.publish_voice_status(status)
    for event in manager.pop_voice_events():
        await transport.publish_voice_event(event)
    for segment in manager.pop_voice_segments():
        await transport.publish_voice_segment(segment)
        await transport.publish_voice_audio(segment)
    await asyncio.sleep(0.05)
```

For an HTTP/WebSocket adapter, serialize the same status/event/metadata
dictionaries as JSON and expose or stream WAV bytes separately. JavaScript
clients should not receive Python callback objects.

## ADS-B And AIS

ADS-B and AIS are live decoder workflows. They require local SDR hardware.
Decoder paths normally come from the manager config. Pass explicit paths only
when overriding config for a specific call.

Start ADS-B with `readsb`, then poll and inspect tracks:

```python
from time import sleep

session = manager.start_adsb()

try:
    while manager.status.process_running:
        sleep(1.0)
        manager.poll()

        for track in manager.track_store.list():
            print(
                track.track_id,
                track.label or "",
                track.lat,
                track.lon,
                track.altitude_m,
                track.speed_mps,
                track.last_seen,
            )
finally:
    manager.stop()
```

Start AIS with `rtl_ais` the same way:

```python
from time import sleep

session = manager.start_ais()

try:
    while manager.status.process_running:
        sleep(1.0)
        manager.poll()

        for track in manager.track_store.list():
            print(
                track.track_id,
                track.label or "",
                track.lat,
                track.lon,
                track.speed_mps,
                track.course_deg,
                track.heading_deg,
                track.last_seen,
            )
finally:
    manager.stop()
```

`manager.poll()` advances decoder ingestion. If you poll faster than `readsb`
writes JSON or faster than `rtl_ais` emits NMEA lines, `message_count`,
`last_message_at`, and `track_store.list()` can repeat between polls. That is
normal; a changed `track.last_seen` means that track was updated.

If the manager has a `SqliteHistory`, decoded observations and latest track
states are persisted during `poll()`.

ADS-B uses `readsb` JSON output as decoder scratch data. By default
`start_adsb()` creates a temporary scratch directory and removes it on
`manager.stop()`. Pass `write_json_dir=...` only when you want to inspect or
keep the raw `readsb` JSON files. This is separate from `SqliteHistory`
persistence.

Live validation has two layers. `running=True` with `error=None` confirms that
the external decoder started and is holding the SDR. `messages > 0` or
`tracks > 0` requires real RF reception, a suitable antenna, and nearby ADS-B
or AIS traffic. If `readsb` or `rtl_ais` exits early, call `manager.poll()` and
inspect `manager.status.error`; decoder stderr such as device-busy or SDR-open
failures is reported there.

## History

When a manager has a `SqliteHistory`, completed active scans are persisted.

```python
rows = history.list_frequency_scans(limit=5)
for row in rows:
    best = row.get("best_matched_candidate") or row.get("best_candidate")
    print(row["started_at"], row["status"], best)
```

Simple inspection is also available through `get_history(...)`:

```python
from olab_rf import get_history

favorites = get_history(type="favorites", config="olab_rf.yaml", limit=20)
scans = get_history(type="frequency_scans", config="olab_rf.yaml", limit=20)
events = get_history(type="spectrum_events", config="olab_rf.yaml", limit=100)
tracks = get_history(type="tracks", config="olab_rf.yaml", limit=20)
```

## Callable Reference

### `SessionManager(...)`

Create one manager per receiver.

Common parameters:

- `receiver: ReceiverConfig`: receiver identity, serial, PPM, and gain config.
- `frequency_catalog: FrequencyCatalog`: catalog used for ranges and channel matching.
- `history: SqliteHistory | None`: optional persistence store.
- `config: OlabRfConfig | None`: optional package config used for default decoder
  paths.

Use `SessionManager.from_config(config, history=history)` for normal local
hardware workflows. It selects the configured receiver, merges the configured
frequency catalog, stores the config on the manager, and lets workflow methods
resolve decoder paths without repeating `config.decoders[...]`.

### `find_active_channels(...) -> FrequencyScanStatus`

Scan known catalog channels in a range.

Parameters:

- `range_id: str`: required catalog range id. The range must define channels.
- `backend: "rtl_power" | "rtl_sdr_iq" = "rtl_power"`: scan backend.
- `path: str | None = None`: optional decoder path override. When omitted,
  the manager uses configured decoder paths, then command-name defaults.
- `duration_sec: float = 10.0`: scan duration.
- `channel_width_hz: int | None = None`: match/search width around channels.
- `gain_db: float | None = None`: receiver gain; `None` means auto/default.
- `sample_rate_hz: int | None = None`: backend sample rate when supported.
- `baseline: FrequencyBaseline | None = None`: baseline for margin calculations.
- `resume_previous: bool = False`: resume an interrupted spectrum workflow after completion.

Returns:

- `FrequencyScanStatus`. For `rtl_power`, this is initially `running`; poll
  until it reaches `complete` or `error`.

Raises:

- `ValueError` if the range id is missing or the range has no channels.

### `start_range_scan(...) -> FrequencyScanStatus`

Run a general range scan from a catalog range or explicit frequency grid.

Parameters:

- `range_id: str | None = "frs_gmrs"`: catalog range id. Ignored when explicit
  `min_freq_hz` and `max_freq_hz` are provided.
- `min_freq_hz`, `max_freq_hz`: explicit range bounds in Hz.
- `step_hz`: grid spacing for arbitrary ranges or catalog ranges without channels.
- `channel_frequencies_hz`: optional manual channel list in Hz.
- `channel_width_hz`: match/search width in Hz.
- `backend`: `rtl_power` or `rtl_sdr_iq`.
- `path`, `duration_sec`, `gain_db`, `sample_rate_hz`, `baseline`,
  `resume_previous`: same meaning as `find_active_channels(...)`.

Returns:

- `FrequencyScanStatus`.

### `capture_range_baseline(...) -> FrequencyScanStatus`

Capture a quiet baseline for a catalog range or explicit frequency grid.

Parameters:

- Same range-planning parameters as `start_range_scan(...)`.
- `duration_sec: float = 10.0`.
- `gain_db` should generally match the later active scan.

Returns:

- `FrequencyScanStatus`. When complete, retrieve the baseline with
  `latest_frequency_baseline()`.

### `start_frequency_scan(...) -> FrequencyScanStatus`

Lower-level primitive when you already know exact scan bounds.

Required parameters:

- `min_freq_hz`, `max_freq_hz`: frequency bounds in Hz.
- `bin_size_hz`: `rtl_power` bin size or approximate scan bin size.
- `duration_sec`: scan duration.

Optional parameters:

- `channel_frequencies_hz`, `channel_width_hz`, `backend`, `path`, `gain_db`,
  `sample_rate_hz`, `baseline`, `resume_previous`.

Prefer `find_active_channels(...)` or `start_range_scan(...)` unless you need
this lower-level control.

### `FrequencyScanStatus`

Important fields and properties:

- `status`: `created`, `running`, `complete`, `stopped`, or `error`.
- `request`: `FrequencyScanRequest` used for the scan.
- `progress`: `0.0` to `1.0`.
- `sweeps_completed`: number of backend sweep outputs consumed.
- `candidates`: backend-ranked `FrequencyCandidate` values, including unmatched peaks.
- `best_candidate`: first raw candidate, if present.
- `matched_candidates`: matched channel candidates ranked by margin/power.
- `best_matched_candidate`: first matched candidate, if present.
- `error`: backend or validation error text.

`to_dict()` includes the same fields plus dict forms of candidates. Web/API
adapters should use `to_dict()` rather than inspecting dataclass internals.

### `FrequencyCandidate`

Important fields:

- `frequency_hz`: observed backend frequency. For `rtl_power`, this is a bin center.
- `matched_frequency_hz`: catalog channel frequency when matched.
- `frequency_offset_hz`: observed minus matched frequency in Hz.
- `power_db`: measured candidate power.
- `baseline_power_db`: baseline power at/near the candidate frequency, if available.
- `margin_db`: active power minus baseline power, if available.
- `label`, `range_id`, `channel_id`, `modulation`: catalog match metadata.
- `source`: candidate source, such as `bin`, `channel`, or `iq_peak`.

### `start_adsb(...) -> RadioSession`

Start an ADS-B receive workflow using the external `readsb` command.

Parameters:

- `path: str | None = None`: optional executable path override. When omitted,
  the manager uses `config.decoders["readsb"].path`, then `readsb`.
- `write_json_dir: str | Path | None = None`: optional directory where
  `readsb` writes `aircraft.json`. When omitted, olab_rf creates a temporary
  scratch directory and removes it on `stop()`.

Returns:

- `RadioSession` with mode `adsb`, decoder `readsb`, and the command used.

Behavior:

- Starts `readsb` as a subprocess.
- `poll()` reads `aircraft.json`, converts aircraft with positions into
  `Track` values, upserts them into `manager.track_store`, and persists them
  when history is configured.
- Uses the receiver serial from `ReceiverConfig.serial` when present.
- The JSON directory is decoder scratch space, not olab_rf history persistence.

Raises:

- `RuntimeError` if the command cannot be started.

### `start_ais(...) -> RadioSession`

Start an AIS receive workflow using the external `rtl_ais` command.

Parameters:

- `path: str | None = None`: optional executable path override. When omitted,
  the manager uses `config.decoders["rtl_ais"].path`, then `rtl_ais`.

Returns:

- `RadioSession` with mode `ais`, decoder `rtl_ais`, and the command used.

Behavior:

- Starts `rtl_ais -n -d 0` as a subprocess, adding receiver PPM correction when
  configured.
- `poll()` reads NMEA lines from decoder output, parses positioned AIS messages
  into `Track` values, upserts them into `manager.track_store`, and persists
  them when history is configured.

Raises:

- `RuntimeError` if the command cannot be started.

### `poll() -> SensorStatus`

Advance the active workflow and return current receiver status.

Behavior depends on the active mode:

- ADS-B: ingest the latest `readsb` JSON.
- AIS: ingest available `rtl_ais` output.
- Spectrum: ingest available `rtl_power` sweeps.
- Frequency scan: update scan progress and candidates.
- Replay: advance synthetic messages.

Polling is intentionally non-blocking. Repeated status values usually mean the
backend has not produced new output yet.

### `Track`

Normalized moving-object state produced by ADS-B, AIS, and replay workflows.

Important fields:

- `track_id`: stable id such as `adsb-a1b2c3` or `ais-366967104`.
- `domain`: `air` for ADS-B, `marine` for AIS.
- `protocol`: `adsb`, `ais`, or another decoder protocol.
- `label`: callsign, MMSI, or other display label when available.
- `lat`, `lon`: position in decimal degrees.
- `altitude_m`: altitude in meters when available.
- `speed_mps`: speed in meters per second when available.
- `course_deg`, `heading_deg`: course/heading in degrees when available.
- `source_sensor`: receiver id.
- `first_seen`, `last_seen`: UTC datetimes for track lifecycle.
- `metadata`: original decoder payload details.

Use `track.to_dict()` when returning tracks through an API or serializing them.

### Other Workflow Methods

- `start_replay(steps=12) -> RadioSession`: synthetic tracks for tests/demos.
- `start_spectrum(...) -> RadioSession`: run live `rtl_power` monitoring.
- `start_listen(...) -> RadioSession`: start `rtl_fm | aplay` for the selected
  watch frequency.
- `start_voice_segments(...) -> RadioSession`: start `rtl_fm` PCM capture and
  transmission segmentation.
- `iter_voice_segments(...) -> Iterator[RadioVoiceSegment]`: scoped voice
  capture iterator that cleans up the receiver.
- `update_voice_segment_settings(...) -> VoiceSegmentStatus`: change carrier
  gate timings/threshold without stopping capture.
- `reset_voice_segment_calibration() -> VoiceSegmentStatus`: discard the idle
  level estimate between transmissions.
- `stop() -> None`: stop the active workflow.
- `poll_frequency_scan() -> FrequencyScanStatus | None`: advance only an active scan.

### State Accessors

- `status_dict()`: current receiver status as a dictionary.
- `session_dict()`: active/most recent session as a dictionary.
- `current_spectrum()`: latest `SpectrumSnapshot`.
- `spectrum_history(limit=None)`: recent spectrum snapshots.
- `spectrum_events(limit=None)`: recent in-memory spectrum events.
- `catalog_with_favorites()`: catalog overlaid with persisted favorites.
- `current_frequency_scan()`: active or most recent `FrequencyScanStatus`.
- `frequency_scan_dict()`: active or most recent scan as a dictionary.
- `latest_frequency_baseline()`: latest captured `FrequencyBaseline`.
- `set_watch_frequency(...)` / `watch_dict()`: listen target helpers.
- `save_frequency_favorite(...)`, `list_frequency_favorites()`,
  `delete_frequency_favorite(...)`: persisted favorites.

Primary public imports are listed in `src/olab_rf/__init__.py`.

## Demo Web UI

The FastAPI/static app is a demo and test surface over this Python API. It
should not own RF workflow behavior. See [Web Demo Boundary](web_demo.md).
