# olab_rf Installation Notes

Use a virtual environment for all Python work. Do not install into system
Python.

```bash
python3 -m venv venv
source venv/bin/activate
pip install "olab-rf[web,ais,pyrtlsdr,nats]"
```

**Local development**, against an `olab_code` checkout, to run the test
suite or make changes:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e "packages/olab_rf[dev,web,ais,pyrtlsdr,nats]"
pytest packages/olab_rf/tests -q
```

`olab_rf` does not run `apt` installations automatically. Before installing SDR
system packages, use:

```bash
olab-rf-check
```

The check command is dry-run only. It reports whether tools such as `readsb`,
`rtl_ais`, `rtl_power`, `rtl_fm`, and `rtl_test` are available. When run from a
directory containing `olab_rf.yaml`, it uses the decoder paths from that local
config. Pass `--config PATH` to check a specific config file.

SQLite history is enabled by default for the demo server. Inspect saved
favorites, spectrum events, tracks, and frequency scans with:

```bash
olab-rf-history favorites --config olab_rf.yaml
olab-rf-history frequency-scans --config olab_rf.yaml --limit 10
olab-rf-history spectrum-events --config olab_rf.yaml --format csv
```

Use `olab-rf-demo-server --no-history` only when you intentionally want an
ephemeral demo session.

When started from the repository root, `olab-rf-demo-server` automatically uses a
local `olab_rf.yaml` if it exists. Pass `--config PATH` to use a different config.

## Current Ubuntu Host Checks

`olab-rf-check` is intentionally non-invasive. It does not install packages,
change kernel modules, write udev rules, or blacklist drivers.

The check reports:

- current working directory, selected config path, and whether local
  `olab_rf.yaml` was found
- decoder tools from config or `PATH`: `readsb`, `rtl_ais`, `rtl_power`,
  `rtl_fm`, `rtl_sdr`, plus `rtl_tcp` and `rtl_test`
- whether configured decoder paths exist and are executable
- RTL-SDR USB devices visible through `lsusb`
- RTL-SDR devices visible through `rtl_test -t`, when `rtl_test` is installed
- loaded kernel DVB modules that commonly claim RTL2832U dongles
- warnings for missing tools, duplicate serials, and likely driver conflicts

## RTL-SDR Base Tooling

On Ubuntu 24.04, this host reports the `rtl-sdr` package is available from the
Ubuntu `noble/universe` repository. That package provides tools such as
`rtl_test`.

Do not run this until explicitly approved:

```bash
sudo apt install rtl-sdr
```

After installation, run:

```bash
olab-rf-check
rtl_test -t
```

For an RTL-SDR Blog V3 / common R820T-family dongle, output like this is enough
to prove the laptop can see the receiver:

```text
Found 1 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: 00000001
Using device 0: Generic RTL2832U OEM
Detached kernel driver
Found Rafael Micro R820T tuner
Supported gain values ...
No E4000 tuner found, aborting.
Reattached kernel driver
```

The `No E4000 tuner found` line is expected for R820T/R820T2 devices; it does
not mean the RTL-SDR is unusable. The important signals are that the device,
serial number, and Rafael tuner were detected.

If the dongle is visible in `lsusb` but `rtl_test` cannot open it, the usual
causes are:

- Linux DVB kernel modules claimed the device.
- udev permissions do not allow the current user to access the USB device.
- multiple RTL-SDR dongles have duplicate/default serials.

The optional `rtl_sdr_iq` backend uses the system `rtl_sdr` recorder for normal
runtime scans, and IQ recording (`SessionManager.start_recording(kind="iq")`)
and the `iq_replay` scan backend (`start_iq_replay_scan(...)`) share the same
`rtl_sdr` command-line dependency — `iq_replay` itself needs no hardware at
replay time, only for the original recording. None of these paths use
`pyrtlsdr`.

The lower-level direct Python adapter (`capture_iq_samples`) is the only code
that loads `librtlsdr` through `pyrtlsdr`. `pyrtlsdr` 0.4 and 0.5 bind
`rtlsdr_set_dithering` at import time, and Ubuntu's `librtlsdr` 2.0.1 does not
export it, so `import rtlsdr` fails with
`undefined symbol: rtlsdr_set_dithering`. That breaks only this adapter; voice
capture, scans, priority scanning and IQ recording are unaffected. To use the
adapter, install a `librtlsdr` that exports that symbol, or use `pyrtlsdr` 0.3.0
with `setuptools<81` (0.3.0 imports `pkg_resources`), which loads against 2.0.1.
Otherwise use the command-line `rtl_sdr` path.

## DVB Driver Conflict

RTL-SDR dongles often appear as `0bda:2838 Realtek RTL2838 DVB-T`. On a stock
Linux desktop, kernel DVB modules may claim the device before SDR tools can use
it.

`olab-rf-check` reports loaded modules such as:

```text
dvb_usb_rtl28xxu
rtl2832
rtl2832_sdr
dvb_usb_v2
dvb_core
```

Blacklisting/removing kernel modules changes system behavior, so `olab_rf` does
not do it automatically. We should review and approve those steps before making
system changes.

## ADS-B Decoder

`olab_rf` currently targets `readsb` for ADS-B. On this host, `readsb` was not
available from the default apt package search, so use upstream install/build
instructions when ready.

Useful upstream references:

- `readsb` repository: https://github.com/wiedehopf/readsb
- The repository README points to automatic installation and source-build
  instructions.

Any `readsb` installation will require approval before running package install
commands.

### Build `readsb` Debian Package

The upstream `readsb` README recommends building a Debian package with RTL-SDR
support. The source can be cloned into `external/readsb`, which is ignored by
git.

Required build dependencies from upstream:

```bash
sudo apt install --no-install-recommends --no-install-suggests \
  git build-essential debhelper libusb-1.0-0-dev pkg-config fakeroot \
  libncurses-dev zlib1g-dev libzstd-dev librtlsdr-dev help2man
```

On this laptop, the currently missing packages were:

```text
debhelper
librtlsdr-dev
help2man
```

After dependencies are installed:

```bash
cd external/readsb
export DEB_BUILD_OPTIONS=noddebs
rm -f ../readsb_*.deb
dpkg-buildpackage -b -ui -uc -us --build-profiles=rtlsdr
sudo dpkg -i ../readsb_*.deb
```

Then verify:

```bash
readsb --help | head
olab-rf-check
```

If the Debian package build compiles `external/readsb/readsb` but fails during
packaging, the compiled binary can still be used directly for development:

```bash
external/readsb/readsb --help | head
```

Create a local `olab_rf.yaml` pointing to that binary:

```yaml
receivers:
  - id: rtlsdr-1
    type: rtlsdr
    serial: "00000001"
    gain: auto

decoders:
  readsb:
    path: external/readsb/readsb
  rtl_ais:
    path: rtl_ais
  rtl_power:
    path: rtl_power
  rtl_fm:
    path: rtl_fm
```

`olab_rf.yaml` is ignored by git so local hardware paths and serials do not need
to be committed.

## AIS Decoder

`olab_rf` normalizes AIS from NMEA `!AIVDM` / `!AIVDO` sentences using the Python
`pyais` parser. Any decoder that writes AIS NMEA lines to stdout or stderr can
be adapted to the current `ais` mode.

The first backend is `rtl_ais` from `dgiardini/rtl-ais`. It was not available
in the default Ubuntu 24.04 package search on this laptop, but it builds from
source with the already-installed RTL-SDR development libraries:

```bash
git clone --depth 1 https://github.com/dgiardini/rtl-ais.git external/rtl-ais
make -C external/rtl-ais
```

The `-n` flag logs decoded NMEA sentences to stderr, which `olab_rf` ingests:

```bash
external/rtl-ais/rtl_ais -n -d 0
```

`rtl_ais` uses `-d` as a device index. With one RTL-SDR attached, use `0`.

Because the current location is several miles inland, AIS live testing may need:

- a coastal/river test location
- a recorded NMEA fixture
- a different antenna placement

## Remote ID Decoders (Wi-Fi and Bluetooth)

`olab_rf` receives ASTM F3411 / ASD-STAN 4709-002 Remote ID broadcasts over
two independent transports, each with its own tool: `droneid-go` for Wi-Fi
(`RemoteIdManager` + `DroneidGoBackend`), `unix_rid_capture` for Bluetooth
Low Energy (`UnixRidCaptureBackend`). Both run as separate, concurrent
processes; neither is wired into `SessionManager`, `olab-rf-demo-server`, or
`olab-rf-check` in this pass (see the pairwork plan for the reasoning) —
construct a `RemoteIdManager` directly in Python.

> **Validation status: not validated on real traffic.** The `droneid-go`
> command-line flags were confirmed against its own `-h` output (v0.3.1), but
> the ZMQ message schema that `parse_droneid_go_json` reads is reconstructed
> from `alphafox02/DragonSync`'s consumer code, and `droneid-go` has never
> been run against real Remote ID traffic. The Python wrapper logic is
> unit-tested against hand-built fixtures only. Treat this feature as
> experimental until it has been through a hardware test.
>
> **BLE via `droneid-go -ble`:** receiving BLE Remote ID through
> `droneid-go -ble <sniffle-serial-port>` and a Sniffle dongle is planned and
> untested. Nothing in `olab_rf` uses it yet.
>
> **`unix_rid_capture` (BLE) is known not to work on a Bluetooth 5.2 adapter,
> and fails silently.** On an Intel AX201 the controller rejects its legacy LE
> scan commands (HCI status 0x0c, command disallowed), because the kernel
> drives the adapter with extended scanning. The process keeps running and
> prints `rx packets 0`, so `UnixRidCaptureBackend.is_running()` and
> `RemoteIdManager.backend_status()` report BLE as healthy while nothing is
> scanned. It also parses legacy advertising reports only (no extended
> advertising, no Long Range). It is unverified on any adapter and may be
> removed once BLE through `droneid-go -ble` has been tested.

### droneid-go (Wi-Fi)

`droneid-go` (`alphafox02`/CEMAXECUTER LLC) is **proprietary** — personal,
educational, and research use is permitted, but the binary cannot be
redistributed and its source is unavailable. `olab_rf` cannot vendor it.
Download a release yourself:

- Upstream: https://github.com/alphafox02/droneid-go
- Pin a specific release tag rather than tracking `main` — this project
  moves fast (commits within days), so an unpinned "latest" can change
  behavior/output under you between test runs.
- `DroneidGoBackend` launches droneid-go with `-z -zmqsetting host:port`
  (droneid-go v0.3.1: `-z` enables the ZMQ publisher, `-zmqsetting` is its
  bind address, default `127.0.0.1:4224`) and connects to that `PUB` socket
  directly — no separate ZMQ broker or bridge process is needed.
  Requires the `droneid-go` install extra (`pip install
  "olab-rf[droneid-go]"`), which pulls in `pyzmq`; this is the only new
  dependency this feature adds, and it stays optional the same way `pyais`
  does for the `ais` extra.
- droneid-go's own `-ble <sniffle-serial-port>` (Sniffle long-range BLE; it
  takes a serial port, not a bare switch) mode is not used by
  `DroneidGoBackend` — no Sniffle dongle (nRF52840/Sonoff CC2652P) is
  available in this environment. BLE reception comes from
  `unix_rid_capture` instead (below), over a plain Bluetooth adapter.
- droneid-go's ZMQ wire schema is not documented by the tool itself;
  `parse_droneid_go_json` is grounded in a real downstream consumer's
  parser (`alphafox02/DragonSync`) rather than invented, but has **not**
  been verified against the real binary's actual output. Re-verify with a
  real or synthetic Remote ID broadcast, the same way the SigMF ADS-B/AIS/FRS
  fixtures from issue #13 were eventually checked against real signals.

### `unix_rid_capture` (Bluetooth Low Energy)

`unix_rid_capture` (`sxjack`) is MIT-licensed, full C source, dead since
2023-03-06 (predates ASTM F3411-22a) — built from source under
`external/unix_rid_capture`, matching the existing `external/readsb`/
`external/rtl-ais` pattern:

```bash
git clone https://github.com/sxjack/unix_rid_capture.git external/unix_rid_capture
```

`unix_rid_capture`'s `CMakeLists.txt` references `opendroneid.c`/
`opendroneid.h`, which the repository does **not** vendor itself — copy them
in from `opendroneid-core-c` before building, or the build fails:

```bash
git clone --depth 1 https://github.com/opendroneid/opendroneid-core-c.git /tmp/opendroneid-core-c
cp /tmp/opendroneid-core-c/libopendroneid/opendroneid.c external/unix_rid_capture/
cp /tmp/opendroneid-core-c/libopendroneid/opendroneid.h external/unix_rid_capture/
```

Patch `external/unix_rid_capture/CMakeLists.txt` **before** running `cmake .`
(or re-run `cmake .` afterward — `rid_capture.h` is generated from
`rid_capture.h.in` via `configure_file()`, embedding `#define ENABLE_PCAP
${enable_pcap}`, so a stale generated header is the easy way to think pcap
is disabled when it isn't):

```diff
- set(enable_pcap 1)
+ set(enable_pcap 0)
- set(fa_export 3)
+ set(fa_export 0)
```

`enable_pcap 0` compiles the entire WiFi/pcap code path out of the binary
(confirmed by reading `rid_capture.c`: every `pcap_create`/`pcap_activate`/
`pcap_loop`/`pcap_close` call, and the header include itself, is wrapped in
`#if ENABLE_PCAP`) — so `unix_rid_capture`'s BLE-only role never touches the
WiFi adapter `droneid-go` owns, and its hardcoded-default-WiFi-interface
behavior (which otherwise aborts the whole process via `exit(1)` if that
interface, e.g. `wlp5s0b1`/`wlan1`, doesn't exist) is moot — that code isn't
compiled in at all. `fa_export 0` disables compiling in `export.c`'s
FlightAware `aircraft.json` writer, which otherwise writes into whatever
working directory the process is spawned from.

Required build dependencies: `libbluetooth-dev`, `cmake`, a C compiler.
Build is in-source:

```bash
cd external/unix_rid_capture
cmake .
make
```

The binary lands at `external/unix_rid_capture/rid_capture` (not a `build/`
subdirectory). Running it needs root, or `cap_net_raw`/`cap_net_admin`
(per the upstream README) — including for the BLE-only path used here:

```bash
sudo setcap cap_net_raw,cap_net_admin+eip external/unix_rid_capture/rid_capture
```

Create a local `olab_rf.yaml` entry (or pass `path=...` directly to
`UnixRidCaptureBackend`) pointing at that binary. If the capability is
missing, `rid_capture` exits non-zero rather than crashing `olab_rf` itself
— `UnixRidCaptureBackend.stderr_lines()` shows why.

Neither backend has been validated on real hardware or real broadcasts.
Both backends' Python wrapper logic (command construction, parsing) is
unit-tested against hand-built fixtures only. The `droneid-go` flags are
confirmed from its `-h`; the `unix_rid_capture` binary has not been built or
run as part of this work. Build and validate against real hardware before
relying on either.
