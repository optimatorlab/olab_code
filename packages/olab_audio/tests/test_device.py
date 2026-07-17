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
