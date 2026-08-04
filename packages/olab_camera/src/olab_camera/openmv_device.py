"""Host-side session/control wrapper around the openmv.Camera protocol client."""

import math
import os

try:
	import openmv as _openmv_lib
except Exception as e:
	print(f'INFO: openmv is not installed and was not imported.  You may ignore this message.  Unless you are using an OpenMV camera you do not need the openmv package.')
	_openmv_lib = None


class OpenMVDevice:
	"""Thin host-control wrapper around the official `openmv.Camera` protocol
	client -- addresses a board by explicit serial path, uploads/executes
	profile or custom scripts, and exposes the named-channel transport.

	This is a session/control layer only: it has no `Camera`/frameDeque
	concept of its own (see `CameraOpenMV` for the frame-integration layer
	built on top of it) and never auto-reconnects -- a disconnect or
	protocol error is raised to the caller, not retried.

	Attributes:
		port (str): Serial device path (e.g. '/dev/ttyACM0'), as given.
		timeout (float): Configured protocol response timeout in seconds,
			passed through to the underlying client. Bounds how long any
			single blocking client call (including readFrame()) can take.
		versionInfo (dict): Populated by connect() from the client's own
			version() call (protocol/bootloader/firmware version tuples).
		systemInfo (dict): Populated by connect() from the client's own
			system_info() call (board capability/identity fields).
	"""

	def __init__(self, port, client_class=None, timeout=1.0, **client_kwargs):
		"""Initialize the device wrapper (does not connect).

		Args:
			port (str): Serial device path. Required, non-empty -- there is
				no discovery/default-device policy in this release; the
				caller must name the port explicitly.
			client_class (type, optional): Injectable protocol-client class,
				for testing only. Real callers should never pass this --
				defaults to the real `openmv.Camera` when installed.
			timeout (float): Protocol response timeout (seconds) passed to
				the client. Must be a finite positive number -- it's the
				basis for every bounded-wait calculation in `CameraOpenMV`'s
				shutdown sequencing, so an invalid value would silently
				break that design rather than fail loudly here.
			**client_kwargs: Additional keyword arguments forwarded to the
				client constructor (e.g. `baudrate`, `crc`, `max_retry`).

		Raises:
			ValueError: `port` is not a non-empty string, or `timeout` is
				not a finite positive number.
			ImportError: `openmv` is not installed and no `client_class` was
				supplied. Install it with `pip install olab-camera[openmv]`.
		"""
		if not isinstance(port, str) or port == '':
			raise ValueError(f'port must be a non-empty string, got {port!r}')

		if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
			raise ValueError(f'timeout must be a finite positive number, got {timeout!r}')

		resolved_client_class = client_class
		if resolved_client_class is None:
			if _openmv_lib is None:
				raise ImportError(
					'openmv is required for OpenMVDevice but is not installed. '
					'Install it with: pip install olab-camera[openmv]')
			resolved_client_class = _openmv_lib.Camera

		self.port = port
		self.timeout = timeout
		self._client_class = resolved_client_class
		self._client_kwargs = client_kwargs

		self._client = None
		self.versionInfo = {}
		self.systemInfo = {}


	def connect(self):
		"""Connect to the device and cache its version/system identity.

		Exception-safe end to end (review round 2, Stage 2, finding 2): if
		`connect()` itself succeeds but the subsequent identity queries
		(`version()`/`system_info()`) fail, the already-open local client is
		disconnected before the exception propagates -- `self._client` is
		only ever left set when this method fully succeeds, so a caller
		that sees this raise never needs to guess whether a connection was
		left dangling.
		"""
		client = self._client_class(self.port, timeout=self.timeout, **self._client_kwargs)
		try:
			client.connect()
			version_info = client.version()
			system_info = client.system_info()
		except Exception:
			try:
				client.disconnect()
			except Exception:
				pass
			raise

		self._client = client
		self.versionInfo = version_info
		self.systemInfo = system_info

	def disconnect(self):
		"""Disconnect, idempotently -- safe to call whether or not connected."""
		client, self._client = self._client, None
		if client is not None:
			client.disconnect()

	def isConnected(self):
		"""Return True if currently connected."""
		return self._client is not None and self._client.is_connected()


	def stopScript(self):
		"""Stop any script currently running on the device."""
		self._client.stop()

	def runSource(self, source):
		"""Upload and execute a literal MicroPython source string.

		Args:
			source (str): Complete script source to upload and run.
		"""
		self._client.exec(source)

	def runScriptFile(self, path):
		"""Upload and execute the contents of a local `.py` file.

		Args:
			path (str or os.PathLike): Path to a local, UTF-8-encoded script.

		Raises:
			ValueError: `path` does not resolve to an existing regular file,
				or its contents cannot be decoded as UTF-8 -- a pure
				input-shape error, raised before any device interaction and
				never conflated with a hardware/upload failure from the
				`runSource()` call this delegates to.
		"""
		path_str = os.fspath(path)
		if not os.path.isfile(path_str):
			raise ValueError(f'runScriptFile: no such file: {path_str!r}')
		try:
			with open(path_str, 'r', encoding='utf-8') as f:
				source = f.read()
		except (OSError, UnicodeDecodeError) as e:
			raise ValueError(f'runScriptFile: could not read {path_str!r} as UTF-8: {e}') from e
		self.runSource(source)

	def readStdout(self):
		"""Return the device's decoded stdout text buffer, or None."""
		return self._client.read_stdout()


	def hasChannel(self, name):
		"""Return True if a channel of the given name is registered on the device."""
		return self._client.has_channel(name)

	def readChannel(self, name):
		"""Read and return the full contents of a named channel, or None."""
		return self._client.channel_read(name)

	def writeChannel(self, name, payload):
		"""Write payload to a named channel. Returns True if the channel exists."""
		return self._client.channel_write(name, payload)


	def streaming(self, enable, raw=False, resolution=None):
		"""Enable or disable the device's standard frame/raw stream."""
		self._client.streaming(enable, raw=raw, resolution=resolution)

	def readFrame(self):
		"""Read the latest stream frame.

		Returns:
			dict or None: `{'width', 'height', 'format', 'depth', 'data',
			'raw_size'}` as produced by the client, or None if no frame is
			currently available (mirrors the underlying client's own
			no-frame-available return, rather than raising).
		"""
		return self._client.read_frame()
