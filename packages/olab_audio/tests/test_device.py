import sys

import pyaudio

from olab_audio import device


class _FakePyAudio:
    """Minimal stand-in for pyaudio.PyAudio(), enumerating a realistic mix
    of real hardware and ALSA pseudo-device plugins -- confirmed via real
    hardware testing to include entries like 'vdownmix' that segfault the
    process if opened as a capture stream (not just UI clutter)."""

    _DEVICES = [
        {'name': 'HDA Intel PCH: ALC289 Analog (hw:0,0)', 'maxInputChannels': 2, 'maxOutputChannels': 0},
        {'name': 'default', 'maxInputChannels': 1, 'maxOutputChannels': 2},
        {'name': 'pipewire', 'maxInputChannels': 1, 'maxOutputChannels': 2},
        {'name': 'sysdefault', 'maxInputChannels': 1, 'maxOutputChannels': 1},
        {'name': 'lavrate', 'maxInputChannels': 1, 'maxOutputChannels': 1},
        {'name': 'samplerate', 'maxInputChannels': 1, 'maxOutputChannels': 1},
        {'name': 'speexrate', 'maxInputChannels': 1, 'maxOutputChannels': 1},
        {'name': 'upmix', 'maxInputChannels': 1, 'maxOutputChannels': 1},
        {'name': 'vdownmix', 'maxInputChannels': 0, 'maxOutputChannels': 1},
    ]

    def __init__(self, host_api_type=pyaudio.paALSA):
        self._host_api_type = host_api_type

    def get_host_api_info_by_index(self, index):
        return {'deviceCount': len(self._DEVICES), 'type': self._host_api_type}

    def get_device_info_by_host_api_device_index(self, host_api_index, device_index):
        return self._DEVICES[device_index]


class _FakeCoreAudioPyAudio:
    """Simulates a macOS CoreAudio (non-ALSA) host API, where device names
    don't follow ALSA conventions at all ('hw:', 'default', 'pipewire',
    'pulse') -- confirms the restrictive filter is never applied here."""

    _DEVICES = [
        {'name': 'MacBook Pro Microphone', 'maxInputChannels': 1, 'maxOutputChannels': 0},
        {'name': 'External USB Mic', 'maxInputChannels': 1, 'maxOutputChannels': 0},
        {'name': 'MacBook Pro Speakers', 'maxInputChannels': 0, 'maxOutputChannels': 2},
    ]

    def get_host_api_info_by_index(self, index):
        return {'deviceCount': len(self._DEVICES), 'type': pyaudio.paCoreAudio}

    def get_device_info_by_host_api_device_index(self, host_api_index, device_index):
        return self._DEVICES[device_index]


def test_get_input_devices_filters_alsa_pseudo_devices(monkeypatch):
    monkeypatch.setattr(device, "audio", _FakePyAudio())

    names = [d['name'] for d in device.get_input_devices()]

    assert names == ['HDA Intel PCH: ALC289 Analog (hw:0,0)', 'default', 'pipewire']
    assert 'vdownmix' not in names
    assert 'sysdefault' not in names
    assert 'lavrate' not in names


def test_get_output_devices_filters_alsa_pseudo_devices(monkeypatch):
    monkeypatch.setattr(device, "audio", _FakePyAudio())

    names = [d['name'] for d in device.get_output_devices()]

    assert 'vdownmix' not in names
    assert set(names) <= {'default', 'pipewire'}


def test_get_connected_devices_filters_and_reports_correct_channels(monkeypatch):
    """Also covers a real bug found in the original source: get_connected_devices()
    populated 'maxInputChannels' with the maxOutputChannels value (copy-paste bug)."""
    monkeypatch.setattr(device, "audio", _FakePyAudio())

    devices = {d['name']: d for d in device.get_connected_devices()}

    assert 'vdownmix' not in devices
    hw_device = devices['HDA Intel PCH: ALC289 Analog (hw:0,0)']
    assert hw_device['maxInputChannels'] == 2
    assert hw_device['maxOutputChannels'] == 0


def test_is_real_alsa_device():
    assert device._is_real_alsa_device('HDA Intel PCH: ALC289 Analog (hw:0,0)') is True
    assert device._is_real_alsa_device('default') is True
    assert device._is_real_alsa_device('pipewire') is True
    assert device._is_real_alsa_device('pulse') is True
    assert device._is_real_alsa_device('vdownmix') is False
    assert device._is_real_alsa_device('sysdefault') is False
    assert device._is_real_alsa_device('lavrate') is False


def test_non_alsa_host_api_is_not_filtered(monkeypatch):
    """On a non-ALSA host API (macOS CoreAudio, Windows WASAPI/MME/etc.),
    device names don't follow ALSA's 'hw:'/'default'/'pipewire'/'pulse'
    conventions at all -- applying the restrictive ALSA filter there would
    incorrectly drop every real device, breaking enumeration entirely on
    those platforms. Only ALSA gets the restrictive filter."""
    monkeypatch.setattr(device, "audio", _FakeCoreAudioPyAudio())

    names = [d['name'] for d in device.get_input_devices()]

    assert names == ['MacBook Pro Microphone', 'External USB Mic']


def test_is_alsa_host_api():
    assert device._is_alsa_host_api({'type': pyaudio.paALSA}) is True
    assert device._is_alsa_host_api({'type': pyaudio.paCoreAudio}) is False
    assert device._is_alsa_host_api({'type': pyaudio.paWASAPI}) is False


def test_lazy_pyaudio_singleton_does_not_construct_until_first_use(monkeypatch):
    calls = []

    class _FakePyAudioModule:
        def PyAudio(self):
            calls.append(1)
            return _FakePyAudio()

    lazy = device._LazyPyAudio()
    assert calls == []  # constructing the proxy itself must not touch PortAudio

    import olab_audio.device as device_module
    monkeypatch.setattr(device_module, "pyaudio", _FakePyAudioModule())

    lazy.get_host_api_info_by_index(0)  # first real use
    assert calls == [1]

    lazy.get_host_api_info_by_index(0)  # second use: must not re-construct
    assert calls == [1]


def test_reinit_audio_resets_the_cached_instance(monkeypatch):
    terminated = []

    class _FakeInstance:
        def terminate(self):
            terminated.append(1)

    lazy = device._LazyPyAudio()
    lazy._instance = _FakeInstance()

    monkeypatch.setattr(device, "audio", lazy)
    device.reinit_audio()

    assert terminated == [1]
    assert lazy._instance is None


class _FakeNonAlsaWithDefaultNamedDevicePyAudio:
    """A non-ALSA host API (e.g. CoreAudio) that happens to expose a real
    device literally named 'default' -- distinct from ALSA's 'default'
    alias, and must never be mistaken for a PulseAudio-routed device."""

    _DEVICES = [
        {'name': 'default', 'maxInputChannels': 1, 'maxOutputChannels': 0},
    ]

    def get_host_api_info_by_index(self, index):
        return {'deviceCount': len(self._DEVICES), 'type': pyaudio.paCoreAudio}

    def get_device_info_by_host_api_device_index(self, host_api_index, device_index):
        return self._DEVICES[device_index]


class _FakeAlsaNoAliasPyAudio:
    """An ALSA host API that enumerates only real hardware, no
    'default'/'pipewire'/'pulse' alias at all."""

    _DEVICES = [
        {'name': 'HDA Intel PCH: ALC289 Analog (hw:0,0)', 'maxInputChannels': 2, 'maxOutputChannels': 0},
    ]

    def get_host_api_info_by_index(self, index):
        return {'deviceCount': len(self._DEVICES), 'type': pyaudio.paALSA}

    def get_device_info_by_host_api_device_index(self, host_api_index, device_index):
        return self._DEVICES[device_index]


class _FakePulseSource:
    def __init__(self, name):
        self.name = name


class _FakePulseSink:
    def __init__(self, name, description, monitor_source_name=None):
        self.name = name
        self.description = description
        self.monitor_source_name = monitor_source_name


class _FakePulseServerInfo:
    def __init__(self, default_sink_name, default_source_name):
        self.default_sink_name = default_sink_name
        self.default_source_name = default_source_name


class _FakePulseClient:
    """Stands in for a `pulsectl.Pulse(...)` instance used as a context
    manager -- the first `pulsectl`-based fixture in this file."""

    def __init__(self, sinks=(), default_sink_name=None,
                 default_source_name='alsa_input.pci-0000_00.1f.3.analog-stereo',
                 raise_on_enter=None, unmatched_source_names=()):
        self._sinks = list(sinks)
        self._default_sink_name = default_sink_name
        self._default_source_name = default_source_name
        self._raise_on_enter = raise_on_enter
        self._unmatched_source_names = set(unmatched_source_names)

    def __enter__(self):
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return self

    def __exit__(self, *exc_info):
        return False

    def server_info(self):
        return _FakePulseServerInfo(self._default_sink_name, self._default_source_name)

    def sink_list(self):
        return self._sinks

    def get_source_by_name(self, name):
        if name in self._unmatched_source_names:
            raise Exception(f'PulseIndexError: no such source: {name}')
        return _FakePulseSource(name)


class _FakePulsectlModule:
    """Stands in for the whole `pulsectl` module -- its `Pulse` attribute
    is the constructor tests actually care about; `pulsectl.Pulse(name)`
    always returns the one fake client instance so tests can inspect calls
    made through it afterward."""

    def __init__(self, client):
        self._client = client
        self.pulse_client_names = []

    def Pulse(self, client_name):
        self.pulse_client_names.append(client_name)
        return self._client


def _install_fake_pulsectl(monkeypatch, client):
    fake_module = _FakePulsectlModule(client)
    monkeypatch.setitem(sys.modules, 'pulsectl', fake_module)
    return fake_module


def test_get_loopback_input_devices_returns_default_and_nondefault_sinks_sharing_deviceid(monkeypatch):
    monkeypatch.setattr(device, "audio", _FakePyAudio())
    sinks = [
        _FakePulseSink('alsa_output.pci-1', 'Built-in Speakers',
                        monitor_source_name='alsa_output.pci-1.monitor'),
        _FakePulseSink('alsa_output.usb-2', 'USB Headset',
                        monitor_source_name='alsa_output.usb-2.monitor'),
    ]
    client = _FakePulseClient(sinks=sinks, default_sink_name='alsa_output.pci-1')
    _install_fake_pulsectl(monkeypatch, client)

    loopbacks = device.get_loopback_input_devices()

    assert len(loopbacks) == 2
    shared_ids = {entry['deviceID'] for entry in loopbacks}
    assert len(shared_ids) == 1  # every entry shares one deviceID -- by design
    by_sink = {entry['sinkName']: entry for entry in loopbacks}
    assert by_sink['alsa_output.pci-1']['isDefault'] is True
    assert by_sink['alsa_output.usb-2']['isDefault'] is False
    assert by_sink['alsa_output.pci-1']['sourceName'] == 'alsa_output.pci-1.monitor'
    assert all(entry['deviceType'] == 'loopback' for entry in loopbacks)


def test_get_loopback_input_devices_no_sinks(monkeypatch):
    monkeypatch.setattr(device, "audio", _FakePyAudio())
    _install_fake_pulsectl(monkeypatch, _FakePulseClient(sinks=[]))

    assert device.get_loopback_input_devices() == []


def test_get_loopback_input_devices_sink_without_monitor_source_is_skipped(monkeypatch):
    monkeypatch.setattr(device, "audio", _FakePyAudio())
    sinks = [_FakePulseSink('alsa_output.pci-1', 'Built-in Speakers', monitor_source_name=None)]
    _install_fake_pulsectl(monkeypatch, _FakePulseClient(sinks=sinks))

    assert device.get_loopback_input_devices() == []


def test_get_loopback_input_devices_pulsectl_not_installed(monkeypatch):
    monkeypatch.setattr(device, "audio", _FakePyAudio())
    monkeypatch.setitem(sys.modules, 'pulsectl', None)  # forces ImportError on `import pulsectl`

    assert device.get_loopback_input_devices() == []


def test_get_loopback_input_devices_no_pulse_server_reachable(monkeypatch):
    monkeypatch.setattr(device, "audio", _FakePyAudio())
    _install_fake_pulsectl(monkeypatch, _FakePulseClient(raise_on_enter=Exception('no PulseAudio server')))

    assert device.get_loopback_input_devices() == []


def test_get_loopback_input_devices_returns_empty_when_portaudio_enumeration_raises(monkeypatch):
    """Reviewer-flagged gap: audio.get_host_api_info_by_index() (or the
    nested get_input_devices() call) can itself raise on a host with no
    usable PortAudio backend / an audio-driver failure -- that must not
    escape as an uncaught exception from a function documented to "never
    raise"."""
    class _FakeBrokenPyAudio:
        def get_host_api_info_by_index(self, index):
            raise OSError('no default host api')

    monkeypatch.setattr(device, "audio", _FakeBrokenPyAudio())
    fake_pulsectl = _install_fake_pulsectl(monkeypatch, _FakePulseClient())

    assert device.get_loopback_input_devices() == []
    assert fake_pulsectl.pulse_client_names == []  # never opened


def test_get_loopback_input_devices_no_alias_device_present(monkeypatch):
    """ALSA host API, but no 'default'/'pipewire'/'pulse' alias enumerated
    -- get_loopback_input_devices() must return [] without ever opening a
    pulsectl client."""
    monkeypatch.setattr(device, "audio", _FakeAlsaNoAliasPyAudio())
    fake_pulsectl = _install_fake_pulsectl(monkeypatch, _FakePulseClient())

    assert device.get_loopback_input_devices() == []
    assert fake_pulsectl.pulse_client_names == []  # never opened


def test_get_loopback_input_devices_non_alsa_host_api_never_matches_default_named_device(monkeypatch):
    """A non-ALSA host API's real device literally named 'default' must
    never be mistaken for the ALSA Pulse-routed alias -- confirms the
    required host-API guard, not just the name check."""
    monkeypatch.setattr(device, "audio", _FakeNonAlsaWithDefaultNamedDevicePyAudio())
    fake_pulsectl = _install_fake_pulsectl(monkeypatch, _FakePulseClient())

    assert device.get_loopback_input_devices() == []
    assert fake_pulsectl.pulse_client_names == []  # never opened
