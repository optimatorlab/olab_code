"""Device enumeration, the lazy PyAudio singleton, and PulseAudio/PipeWire port control."""

import os
import time

import pyaudio


class _LazyPyAudio:
	"""Defers pyaudio.PyAudio() construction (which opens the whole PortAudio
	subsystem) until the first real use, instead of unconditionally at module
	import time — so `import olab_audio` alone doesn't touch audio hardware
	or fail on a machine with no audio drivers. Callers use this exactly like
	a real PyAudio instance (`audio.open(...)`, `audio.get_device_info_by_host_api_device_index(...)`, etc.).
	"""

	_instance = None

	def _get(self):
		if self._instance is None:
			self._instance = pyaudio.PyAudio()
		return self._instance

	def _reset(self):
		"""Terminate and drop the cached instance, so the next access creates a fresh one."""
		if self._instance is not None:
			self._instance.terminate()
			self._instance = None

	def __getattr__(self, name):
		return getattr(self._get(), name)


audio = _LazyPyAudio()


def terminate():
	"""Release PortAudio system resources. Do this when done with **everything**."""
	audio._reset()


def reinit_audio():
	"""Force PortAudio to re-probe connected hardware, so a device plugged in
	after this module was first used becomes visible to get_input_devices()/
	get_output_devices()/get_connected_devices() (see their notes below —
	they read from the same cached `audio` singleton otherwise).

	Callers MUST ensure no Mic (or other stream user of `audio`) is
	currently open before calling this — doing so invalidates any stream
	still referencing the old instance. This function does not check that
	itself; it's a generic primitive, the caller owns that bookkeeping.
	"""
	audio._reset()


def get_default_source_ports():
	"""
	List the available ports on the current default PulseAudio/PipeWire
	input source, and which one is currently active.

	This is NOT about separate hardware devices (a USB mic, say, already
	enumerates fine as its own distinct entry via get_input_devices() —
	no PulseAudio involvement needed for that). This is specifically for
	hardware like a laptop's built-in codec, which exposes multiple
	mutually-exclusive PORTS (internal mic / headphone-jack mic / headset
	mic) on ONE physical source — PyAudio's ALSA host API only ever sees
	that as a single always-current-default device ("pipewire"/"pulse"/
	"default" in get_input_devices()'s output), with no way to pick a
	specific port.

	Returns {'ports': [{'name', 'description'}, ...], 'activePort': name},
	or None if pulsectl isn't available or there's no default source.

	Ports whose hardware jack-sensing reports 'no' (nothing physically
	plugged into that jack) are excluded — PulseAudio/PipeWire's
	PulsePortInfo.available field is 'yes'/'no'/'unknown' per port; 'unknown'
	means the codec doesn't support jack-sensing for that port (e.g. a
	built-in internal mic, which is always kept) and is deliberately NOT
	filtered out, only a confirmed 'no' is. Note this depends on the
	codec's jack-sensing actually being wired up and reporting promptly —
	on hardware where it isn't, this filter is a no-op and every port
	still shows (same as before this filter existed).
	"""
	try:
		import pulsectl
		with pulsectl.Pulse('olab-audio-port-query') as pulse:
			info = pulse.server_info()
			source = pulse.get_source_by_name(info.default_source_name)
			return {
				'ports': [{'name': p.name, 'description': p.description}
						  for p in source.port_list if p.available != 'no'],
				'activePort': source.port_active.name if source.port_active else None,
			}
	except Exception as e:
		print(f'ERROR in get_default_source_ports: {e}')
		return None


def set_default_source_port(port_name):
	"""
	Switch the active port on the current default PulseAudio/PipeWire
	input source (see get_default_source_ports() above for what this
	means and why).

	Callers MUST ensure no Mic stream is currently reading from this
	source before calling this — switching the port while a stream is
	open doesn't error, it silently swaps that stream's audio out from
	under it (all consumers of one physical source share whatever port is
	currently active; there's no per-stream isolation). This function does
	not check that itself — same pattern as reinit_audio(), the caller
	owns that bookkeeping.
	"""
	import pulsectl
	with pulsectl.Pulse('olab-audio-port-set') as pulse:
		info = pulse.server_info()
		source = pulse.get_source_by_name(info.default_source_name)
		pulse.source_port_set(source.index, port_name)


def _find_pulse_routed_portaudio_device(host_api_info, devices):
	"""Return the entry in `devices` (get_input_devices()'s output) that
	represents the one shared PulseAudio/PipeWire-routed PortAudio capture
	device, or None if there isn't one.

	Only meaningful on an ALSA host API. `'pipewire'`/`'pulse'`/`'default'`
	are only a safe ALSA pseudo-device allowlist in `_is_real_alsa_device()`
	-- `_keep_device()` passes every device through unfiltered on non-ALSA
	host APIs, so a real CoreAudio/WASAPI device could independently be
	named `'default'` without being PulseAudio-controlled at all. Refusing
	to match unless `host_api_info` is actually ALSA keeps that from being
	mistaken for a Pulse-routed device.
	"""
	if not _is_alsa_host_api(host_api_info):
		return None
	for d in devices:
		if d['name'] in ('default', 'pipewire', 'pulse'):
			return d
	return None


def _is_alsa_host_api(host_api_info) -> bool:
	"""True if this PortAudio host API is ALSA specifically.

	The pseudo-device-plugin proliferation this module filters around
	(see _is_real_alsa_device()) is an ALSA-specific PortAudio behavior --
	confirmed via real Linux/RPi hardware testing, olab_audio's actual v1
	target platform. macOS (CoreAudio) and Windows (WASAPI/MME/DirectSound)
	host APIs use entirely different device-naming conventions with no
	'hw:'/'default'/'pipewire'/'pulse' equivalents and no equivalent
	plugin-node segfault risk -- applying the ALSA name filter there would
	incorrectly filter out every real device. Only ALSA gets the
	restrictive filter; every other host API passes all devices through
	unfiltered (the original, pre-fix behavior).
	"""
	return host_api_info.get('type') == pyaudio.paALSA


def _is_real_alsa_device(name: str) -> bool:
	"""True for a genuine hardware ALSA device or the safe default alias.

	Only meaningful when the host API is actually ALSA -- see
	_is_alsa_host_api(). PyAudio's ALSA host API enumerates a long list of
	generic ALSA plugin/pseudo-devices as their own "input devices" -- not
	just 'pipewire'/'pulse', but resampling/mixing plugins like
	'sysdefault', 'lavrate', 'samplerate', 'speexrate', 'speex', 'upmix',
	'vdownmix' (confirmed via real hardware: a single physical laptop mic
	produced 9 enumerated "devices", only 1 of which was real). None of
	these are separate hardware -- they're all aliases/plugins layered over
	the same physical source 'default' already represents. Worse than UI
	clutter: real hardware testing found that opening one of these as a
	PyAudio input stream (e.g. 'vdownmix', which isn't even a capture-
	capable plugin) segfaults the whole process -- a C-level crash
	Python's try/except cannot catch, so filtering these out of
	enumeration entirely is the only real fix, not just a cosmetic one.
	Real ALSA hardware devices are reliably named with 'hw:' by
	PortAudio (e.g. 'HDA Intel PCH: ALC289 Analog (hw:0,0)') -- keep only
	those, plus the literal 'default'/'pipewire'/'pulse' aliases (which
	PortAudio itself resolves safely, unlike the individual plugin nodes).
	"""
	return ('hw:' in name) or (name in ('default', 'pipewire', 'pulse'))


def _keep_device(host_api_info, name: str) -> bool:
	"""Apply the ALSA pseudo-device filter only on an ALSA host API; pass
	every device through unfiltered on any other host API (macOS, Windows,
	or a non-ALSA Linux backend)."""
	if not _is_alsa_host_api(host_api_info):
		return True
	return _is_real_alsa_device(name)


def get_input_devices():
	print('NOTE: This function will not capture devices added/removed since olab_audio was first used. Call reinit_audio() to force a re-scan.')

	info = audio.get_host_api_info_by_index(0)
	numdevices = info.get('deviceCount')
	devices = []
	for i in range(0, numdevices):
		dev_info = audio.get_device_info_by_host_api_device_index(0, i)
		if dev_info.get('maxInputChannels') > 0 and _keep_device(info, dev_info.get('name')):
			devices.append({'deviceID': i,
							 'deviceType': 'mic',
							 'name': dev_info.get('name')})
	return devices


def get_output_devices():
	print('NOTE: This function will not capture devices added/removed since olab_audio was first used. Call reinit_audio() to force a re-scan.')

	info = audio.get_host_api_info_by_index(0)
	numdevices = info.get('deviceCount')
	devices = []
	for i in range(0, numdevices):
		dev_info = audio.get_device_info_by_host_api_device_index(0, i)
		if dev_info.get('maxOutputChannels') > 0 and _keep_device(info, dev_info.get('name')):
			devices.append({'deviceID': i,
							 'deviceType': 'speaker',
							 'name': dev_info.get('name')})
	return devices


def get_connected_devices():
	print('NOTE: This function will not capture devices added/removed since olab_audio was first used. Call reinit_audio() to force a re-scan.')
	info = audio.get_host_api_info_by_index(0)
	numdevices = info.get('deviceCount')
	devices = []
	for i in range(0, numdevices):
		dev_info = audio.get_device_info_by_host_api_device_index(0, i)
		if not _keep_device(info, dev_info.get('name')):
			continue
		devices.append({'deviceID': i,
						 'name': dev_info.get('name'),
						 'maxInputChannels': dev_info.get('maxInputChannels'),
						 'maxOutputChannels': dev_info.get('maxOutputChannels')})
	return devices


def get_loopback_input_devices():
	"""
	Discover PulseAudio/PipeWire sinks' monitor sources -- the supported way
	to record "whatever is playing on this speaker/output" -- and return
	them as loopback-flavored entries.

	**Every returned entry shares the same `deviceID`.** PortAudio's ALSA
	host API only ever exposes PulseAudio/PipeWire routing as one generic
	`'pipewire'`/`'pulse'`/`'default'` device (see get_default_source_ports()'s
	docstring for the same fact in the port-selection context) -- there is
	no distinct PortAudio device per Pulse sink. Discovery alone therefore
	cannot select *which* sink's audio gets captured; call
	start_loopback_capture(mic, entry, ...) with the desired entry to start
	`Mic` and route only that stream's own PulseAudio source-output to it,
	or `Mic` will simply capture whatever source already happens to be
	PulseAudio's default.

	Returns entries shaped like:
		{'deviceID', 'deviceType': 'loopback', 'name',
		 'sinkName', 'sinkDescription', 'sourceName', 'isDefault'}
	`isDefault` reflects the sink being the server's default *sink* -- purely
	informational, independent of which *source* is currently selected for
	capture (see start_loopback_capture()).

	Returns `[]` (never raises) if `pulsectl` isn't installed, the host API
	isn't ALSA, no PulseAudio/PipeWire server is reachable, there are no
	sinks, no sink has a monitor source, or get_input_devices() doesn't
	expose a `'pipewire'`/`'pulse'`/`'default'` alias device -- each case
	prints its own one-line diagnostic.
	"""
	try:
		import pulsectl
	except ImportError as e:
		print(f'ERROR in get_loopback_input_devices: pulsectl not available: {e}')
		return []

	try:
		host_api_info = audio.get_host_api_info_by_index(0)
		shared_device = _find_pulse_routed_portaudio_device(host_api_info, get_input_devices())
	except Exception as e:
		print(f'ERROR in get_loopback_input_devices: could not enumerate PortAudio devices: {e}')
		return []
	if shared_device is None:
		print('ERROR in get_loopback_input_devices: no PulseAudio/PipeWire-routed PortAudio device found (not an ALSA host API, or no pipewire/pulse/default alias enumerated).')
		return []

	try:
		with pulsectl.Pulse('olab-audio-loopback-query') as pulse:
			default_sink_name = pulse.server_info().default_sink_name
			loopbacks = []
			for sink in pulse.sink_list():
				if not sink.monitor_source_name:
					print(f'NOTE in get_loopback_input_devices: sink {sink.name!r} has no monitor source, skipping.')
					continue
				try:
					source = pulse.get_source_by_name(sink.monitor_source_name)
				except Exception as e:
					print(f'NOTE in get_loopback_input_devices: could not resolve monitor source {sink.monitor_source_name!r} for sink {sink.name!r}: {e}, skipping.')
					continue
				loopbacks.append({'deviceID': shared_device['deviceID'],
								   'deviceType': 'loopback',
								   'name': shared_device['name'],
								   'sinkName': sink.name,
								   'sinkDescription': sink.description,
								   'sourceName': source.name,
								   'isDefault': sink.name == default_sink_name})
			return loopbacks
	except Exception as e:
		print(f'ERROR in get_loopback_input_devices: {e}')
		return []


def _pulse_source_output_pid(pulse, source_output):
	"""Resolve the PID that owns `source_output`, checking its own proplist
	first and falling back to its owning client's proplist if absent (a
	source-output's proplist can omit `application.process.id` while its
	client's proplist still has it). Returns the PID as a string, or None
	if neither object exposes one.
	"""
	pid = source_output.proplist.get('application.process.id')
	if pid is not None:
		return pid
	try:
		client = pulse.client_info(source_output.client)
	except Exception:
		return None
	return client.proplist.get('application.process.id')


def _find_new_own_source_output(pulse, existing_indices):
	"""Return the one source-output in `pulse.source_output_list()` that
	is both new (its index isn't in `existing_indices`) and positively
	identified as belonging to this process (PID match via
	_pulse_source_output_pid()) -- or None if there isn't exactly one.

	Never considers an entry present in `existing_indices`, regardless of
	its identity -- that's what makes "new" a structural guarantee rather
	than a heuristic. A new entry whose identity can't be confirmed as this
	process's (via its own or its client's proplist) is excluded, not
	treated as a candidate by elimination.
	"""
	this_pid = str(os.getpid())
	candidates = [
		so for so in pulse.source_output_list()
		if so.index not in existing_indices and _pulse_source_output_pid(pulse, so) == this_pid
	]
	if len(candidates) == 1:
		return candidates[0]
	return None


def start_loopback_capture(mic, source, *, timeout=2.0, poll_interval=0.05, **mic_start_kwargs):
	"""
	Start `mic`'s PortAudio capture stream and route only *this* stream's
	resulting PulseAudio source-output to the given loopback monitor --
	without touching PulseAudio's server-wide default source, and without
	affecting any other application's capture stream.

	`mic` must not already be started (`mic.micOn` must be False) -- this
	function calls `mic.start(**mic_start_kwargs)` itself, immediately
	after snapshotting existing source-outputs, so identifying "which
	source-output is ours" has no race window an external caller could
	introduce by starting the stream separately. `**mic_start_kwargs` are
	forwarded to `mic.start()` unchanged (`reachbackFunc`, `postFunc`,
	`frmt`, `channels`, `samplerate`, `frames_per_buffer`) -- loopback
	capture behaves exactly like a normal `Mic.start()` call otherwise.

	`source` is either a `get_loopback_input_devices()` result dict or a
	raw Pulse source name string.

	Mechanism: PortAudio's ALSA host API only exposes one shared
	Pulse-routed device (see `get_loopback_input_devices()`), so there's no
	per-sink `deviceID` to select. Instead, once `mic.start()` creates a
	new PulseAudio source-output for this process, that specific
	source-output is moved to the target monitor via `pulsectl`'s
	`source_output_move()` -- a per-stream move, not a global default
	change. The source-output is identified by snapshotting existing
	source-outputs before `mic.start()`, then polling for a new one whose
	PID (from its own or its owning client's proplist) matches this
	process. An entry present in the snapshot is never considered,
	regardless of identity; an entry whose identity can't be positively
	confirmed as this process's is never considered either -- ambiguity or
	an unidentifiable entry both fail closed rather than guessing.

	On any failure (already started, `mic.start()` failing, timeout,
	ambiguity, or `source_output_move()` itself raising) -- `mic.stop()` is
	called if it was started by this call, and a `RuntimeError` is raised.
	Never leaves an unrouted stream silently capturing the wrong source. A
	failed `mic.start()`'s read-only Pulse lookups (target resolution,
	snapshot) having already happened before the failure is expected --
	what's guaranteed is that no `source_output_move()` call or persistent
	routing mutation happens.

	A successful (non-raising) `source_output_move()` call is treated as
	confirmation the move worked -- there is deliberately no post-move
	re-check of the source-output's reported `.source`/attached-source
	field. Real-hardware testing found that field unreliable on at least
	one `pipewire-pulse` version: it read identically before and after a
	`pactl move-source-output` that a live-audio check confirmed had
	actually succeeded. Comparing against it produced false failures, so
	this function doesn't -- same trust-the-non-raising-call precedent as
	`set_default_source_port()` elsewhere in this module.

	No restoration call exists or is needed: this mutates only the one
	source-output belonging to this specific `Mic` stream, which PulseAudio
	destroys automatically when `mic.stop()` closes the underlying stream --
	unlike a server-wide default change, there is nothing left to restore.
	"""
	import pulsectl

	if mic.micOn:
		raise RuntimeError('start_loopback_capture: mic is already started -- this function must own the start() call.')

	source_name = source['sourceName'] if isinstance(source, dict) else source

	with pulsectl.Pulse('olab-audio-loopback-capture') as pulse:
		target_source = pulse.get_source_by_name(source_name)
		existing_indices = {so.index for so in pulse.source_output_list()}

		mic.start(**mic_start_kwargs)
		if not mic.micOn:
			raise RuntimeError('start_loopback_capture: mic.start() failed -- see excFunc output for details.')

		try:
			deadline = time.monotonic() + timeout
			candidate = None
			while time.monotonic() < deadline:
				candidate = _find_new_own_source_output(pulse, existing_indices)
				if candidate is not None:
					break
				time.sleep(poll_interval)

			if candidate is None:
				raise RuntimeError(
					f'start_loopback_capture: could not identify a unique new capture stream for this '
					f'process within {timeout}s (either none appeared, or more than one did and none/'
					f'multiple could be positively identified as ours).'
				)

			pulse.source_output_move(candidate.index, target_source.index)
		except Exception as e:
			# Anything going wrong after mic.start() succeeded -- our own
			# RuntimeErrors above, or a pulsectl-side exception (server
			# disconnect, rejected move, etc.) from source_output_list()/
			# source_output_move() -- must never leave mic running and
			# silently capturing the wrong (physical mic) source.
			mic.stop()
			if isinstance(e, RuntimeError):
				raise
			raise RuntimeError(f'start_loopback_capture: Pulse operation failed after starting mic: {e}') from e
