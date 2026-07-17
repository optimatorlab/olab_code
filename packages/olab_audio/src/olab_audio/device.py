"""Device enumeration, the lazy PyAudio singleton, and PulseAudio/PipeWire port control."""

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
