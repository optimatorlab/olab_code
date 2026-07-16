# olab_rf Installation Notes

Use a virtual environment for all Python work. Do not install into system
Python.

Normal installation (no `olab_code` checkout required):

```bash
python3 -m venv venv
source venv/bin/activate
pip install "olab-rf[web,ais,pyrtlsdr,nats] @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_rf"
```

Once release wheels exist, prefer pinning the release's exact URL and
SHA-256 hash instead of a git reference.

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
runtime scans. The lower-level direct Python adapter can also load `librtlsdr`
through `pyrtlsdr`; if that path reports an undefined symbol such as
`rtlsdr_set_dithering`, the Python wrapper and installed system `librtlsdr` are
not compatible. Use the command-line `rtl_sdr` path or resolve the `librtlsdr`
installation before debugging direct-adapter code.

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
