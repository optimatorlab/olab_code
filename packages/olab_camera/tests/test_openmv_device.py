"""Tests for OpenMVDevice, the host-side session/control wrapper around the
`openmv.Camera` protocol client. The real client needs hardware to run for
real, so it's entirely mocked here via the `client_class=` seam -- these
tests cover our own logic (port/timeout validation, lifecycle, channel
passthrough, runScriptFile's file-input validation), not the client itself.
Kept minimal/high-signal per the project's stated test-thoroughness
preference.
"""

import os

import pytest

from olab_camera.openmv_device import OpenMVDevice


class _FakeClient:
    """Records every call; connect()/disconnect() toggle is_connected()."""

    def __init__(self, port, **kwargs):
        self.port = port
        self.kwargs = kwargs
        self._connected = False
        self.calls = []

    def connect(self):
        self.calls.append('connect')
        self._connected = True

    def disconnect(self):
        self.calls.append('disconnect')
        self._connected = False

    def is_connected(self):
        return self._connected

    def stop(self):
        self.calls.append('stop')

    def exec(self, script):
        self.calls.append(('exec', script))

    def version(self):
        return {'firmware_version': (4, 0, 0)}

    def system_info(self):
        return {'cpu_id': 0x1234}

    def read_stdout(self):
        return 'hello'

    def has_channel(self, name):
        return name == 'stream'

    def read_status(self):
        return {'stream': True}

    def channel_size(self, name):
        return 5 if name == 'stream' else 0

    def channel_read(self, name, size=None):
        return b'chunk' if name == 'stream' else None

    def channel_write(self, name, data):
        self.calls.append(('channel_write', name, data))
        return name == 'stream'

    def streaming(self, enable, raw=False, resolution=None):
        self.calls.append(('streaming', enable, raw, resolution))

    def read_frame(self):
        return {'width': 320, 'height': 320, 'format': 1, 'depth': 1, 'data': b'x', 'raw_size': 1}


class _FakeClientFailingIdentity(_FakeClient):
    """connect() itself succeeds, but version()/system_info() raise --
    exercises OpenMVDevice.connect()'s own exception-safety."""

    def __init__(self, port, fail_at='version', **kwargs):
        super().__init__(port, **kwargs)
        self.fail_at = fail_at

    def version(self):
        if self.fail_at == 'version':
            raise RuntimeError('boom: version')
        return super().version()

    def system_info(self):
        if self.fail_at == 'system_info':
            raise RuntimeError('boom: system_info')
        return super().system_info()


def test_raises_import_error_without_client_class_or_openmv_installed(monkeypatch):
    import olab_camera.openmv_device as mod
    monkeypatch.setattr(mod, '_openmv_lib', None)
    with pytest.raises(ImportError, match='olab-camera\\[openmv\\]'):
        OpenMVDevice(port='/dev/ttyACM0')


def test_connect_disconnect_lifecycle_and_identity():
    device = OpenMVDevice(port='/dev/ttyACM0', client_class=_FakeClient)
    assert device.isConnected() is False

    device.connect()
    assert device.isConnected() is True
    assert device.versionInfo == {'firmware_version': (4, 0, 0)}
    assert device.systemInfo == {'cpu_id': 0x1234}

    device.disconnect()
    assert device.isConnected() is False
    # Idempotent -- a second disconnect() is a no-op, not an error.
    device.disconnect()


@pytest.mark.parametrize('fail_at', ['version', 'system_info'])
def test_connect_disconnects_local_client_on_post_connect_identity_failure(fail_at):
    """If connect() itself succeeds but version()/system_info() then raise,
    OpenMVDevice.connect() must not leave the now-open local client
    connected -- it should disconnect it before propagating."""
    device = OpenMVDevice(
        port='/dev/ttyACM0', client_class=_FakeClientFailingIdentity, fail_at=fail_at)

    with pytest.raises(RuntimeError):
        device.connect()

    assert device.isConnected() is False
    assert device._client is None


def test_stop_script_run_source_and_channels():
    device = OpenMVDevice(port='/dev/ttyACM0', client_class=_FakeClient)
    device.connect()

    device.stopScript()
    device.runSource('print(1)')
    assert device.hasChannel('stream') is True
    assert device.hasChannel('nope') is False
    assert device.readChannelStatus() == {'stream': True}
    assert device.channelSize('stream') == 5
    assert device.readChannel('stream') == b'chunk'
    assert device.readChannel('stream', 5) == b'chunk'
    assert device.writeChannel('stream', b'data') is True
    assert device.readStdout() == 'hello'

    device.streaming(True, raw=False)
    frame = device.readFrame()
    assert frame['width'] == 320


def test_run_script_file_valid_file_execs_contents(tmp_path):
    device = OpenMVDevice(port='/dev/ttyACM0', client_class=_FakeClient)
    device.connect()
    script_file = tmp_path / 'good.py'
    script_file.write_text('print("hi")', encoding='utf-8')

    device.runScriptFile(script_file)

    assert ('exec', 'print("hi")') in device._client.calls
