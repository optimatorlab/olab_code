# SDRTrunk Capability Probe

This is the Slice 0 operator procedure for validating SDRTrunk before `olab_rf`
gains a digital-listening backend. It is receive-only and deliberately does
not modify SDRTrunk playlists or application data after the operator creates
the profile.

## Current Local Readiness

On 2026-07-10, this development environment has OpenJDK 21.0.11 available as
`/usr/bin/java`. No SDRTrunk launcher, SDRTrunk application root/profile, or
JMBE library was found. Java compatibility must be confirmed against the
specific SDRTrunk release selected below; do not assume that Java 21 is valid
for every release.

## Installation Boundary

Download the Linux archive for a selected SDRTrunk release from the official
[SDRTrunk releases](https://github.com/DSheirer/sdrtrunk/releases) page and
extract it into an operator-owned directory, for example
`$HOME/opt/sdrtrunk/<release>`. Do not install an OS package, change drivers,
or change kernel-module blacklists as part of this procedure without separate
operator approval.

Use the launch script included in that archive. Do not substitute a guessed
`java -jar` command: the installed archive's script and its working-directory
requirements are part of the capability contract being measured.

For the locally validated Linux SDRTrunk 0.6.1 archive, that script is
`bin/sdr-trunk`. It resolves its own application directory, runs the bundled
`bin/java`, and forwards application arguments. The script does not itself
provide a profile or application-root selector. Select the dedicated playlist
in SDRTrunk's GUI before a launch; the first `olab_rf` integration will validate
that playlist path but will not pass it as an unsupported launcher argument.

SDRTrunk's release notes state that JMBE 1.0.9 or newer is required for P25
Phase 1/2 audio on Linux. In SDRTrunk, use its supported JMBE creation or
configuration flow, then record the resulting library path. The official JMBE
project describes selecting that library under SDRTrunk's user preferences.

## Manual Probe

Run the following from the normal desktop terminal, outside the restricted
development sandbox. Replace the placeholders with the actual extracted
location; the commands only inspect files and launch the GUI.

```bash
java --version
cd "$HOME/opt/sdrtrunk/<release>"
find . -maxdepth 2 -type f -perm -u+x -print
./<launcher> --help
```

If `--help` is unsupported, record that fact and exit it normally. Next,
launch the GUI using the exact command that works for the extracted release.
Record all of these facts before closing it:

- SDRTrunk version and archive name.
- Exact launcher path, arguments, working directory, and whether it supports
  an application-root or profile-selection argument.
- The resulting application-root location and the playlist/profile locations.
- JMBE version, its configured path, and whether the GUI reports it ready for
  P25 audio.
- RTL-SDR discovery, selected tuner, native laptop-speaker output, and clean
  shutdown behavior.

Create an operator-owned profile that uses the intended RTL-SDR and the
default laptop output. The first probe should use `424.300 MHz`, a P25 Phase 1
decoder, and no NAC filter. Local validation on 2026-07-11 subsequently
confirmed intelligible P25 Phase 1 voice with unencrypted frames and NAC
`666/x29A` (decimal 666 / hexadecimal `0x29A`). It did not establish a
trunked-system or site identity.

Before starting the profile, stop any `rtl_fm`, `rtl_power`, `readsb`, AIS, or
other process using the same RTL-SDR. Then observe the profile long enough to
classify the outcome as silent, conventional, trunked control/traffic,
non-P25, encrypted, or unsupported. Encrypted and unsupported traffic are
normal no-audio outcomes; do not attempt decryption or circumvention.

## Probe Record (Keep Local)

Keep this record outside Git because it can contain local paths and radio
details:

```text
date/time:
sdrtrunk archive/version:
java version:
launcher command (including working directory):
launcher --help result / profile-selection contract:
application root:
profile/playlist location:
JMBE version and path:
RTL-SDR discovered / selected tuner:
audio output and test result:
clean shutdown behavior:
probe observation at 424.300 MHz:
stdout/stderr and application-log locations:
recording/event artifacts captured (if authorized):
```

Only sanitized launcher/output samples and fixtures that are necessary for
tests or documentation may be committed. Do not add any `olab_rf` digital
backend, profile schema, playlist writer, or recording parser until this
record proves the installed release's launcher/profile contract.

## `olab-rf-check` Configuration

After the operator has completed this probe, add the proven local paths to the
ignored `olab_rf.yaml`. This only lets `olab-rf-check` verify their existence; it
does not launch SDRTrunk or edit any of its files.

```yaml
sdrtrunk:
  launcher_path: /absolute/path/to/sdrtrunk/bin/sdr-trunk
  java_path: /absolute/path/to/sdrtrunk/bin/java
  working_directory: /absolute/path/to/sdrtrunk
  profile_path: /absolute/path/to/SDRTrunk/playlist/local-digital-probe.xml
  jmbe_path: /absolute/path/to/SDRTrunk/jmbe
```

Run `venv/bin/olab-rf-check --config olab_rf.yaml`. Its `sdrtrunk` result reports
the launcher and Java executability plus the working directory, profile, and
JMBE path checks. It does not validate an audio call or infer radio protocol.
