"""Host-side session/control wrapper around the openmv.Camera protocol client."""

import logging
import math
import os
import random
import struct
import time

try:
	import openmv as _openmv_lib
	import openmv.transport as _openmv_transport
	from openmv.constants import Flags, Opcode, Protocol, Status
	from openmv.exceptions import (
		ChecksumException,
		OMVException,
		SequenceException,
		TimeoutException,
	)
except Exception as e:
	print(f'INFO: openmv is not installed and was not imported.  You may ignore this message.  Unless you are using an OpenMV camera you do not need the openmv package.')
	_openmv_lib = None


if _openmv_lib is not None:

	class _EventSafeTransport(_openmv_transport.Transport):
		"""`Transport` with one behavior change: periodic protocol EVENT
		packets (e.g. the firmware's ~50ms stdout "tick" notifications, see
		`openmv/openmv` PR #3138) no longer renew this call's response-wait
		deadline.

		Upstream `recv_packet()` unconditionally does `start_time =
		time.time()` on every EVENT packet, alongside genuine FRAGMENT
		packets (actual response-assembly progress, which legitimately
		should renew the deadline). On real hardware, a channel op that
		races a script-stop transition can get its real response silently
		rejected at the parse layer (sequence mismatch -> `_check_seq()`
		fails -> byte dropped, never surfaced as a packet); combined with a
		still-running script's steady stream of EVENT packets, upstream's
		behavior means the deadline is perpetually renewed and
		`recv_packet()` never times out -- an infinite hang instead of a
		bounded `TimeoutException`. This override is otherwise a verbatim
		copy of the upstream method; only the EVENT branch's deadline reset
		is removed. See docs/investigations/openmv_hang_investigation.md
		for the full hardware-reproduced trace this is based on.
		"""

		def recv_packet(self, poll_events=False):
			if not self.serial or not self.serial.is_open:
				raise OMVException("Serial connection not open")

			fragments = bytearray()
			start_time = time.time()

			while time.time() - start_time < self.timeout:
				if self.serial.in_waiting > 0:
					data = self.serial.read(self.serial.in_waiting)
					self.buf.extend(data)

				if not (packet := self._process()):
					if poll_events:
						return
					time.sleep(0.001)
					continue

				if self.drop_rate > 0.0 and random.random() < self.drop_rate:
					self.log(packet=packet, direction="Drop")
					continue

				self.stats['received'] += 1
				self.log(packet=packet, direction="Recv")

				if (packet['flags'] & Flags.RTX) and (self.sequence != packet['sequence']):
					if packet['flags'] & Flags.ACK_REQ:
						self.send_packet(packet['opcode'], packet['channel'],
										 Flags.ACK, sequence=packet['sequence'])
					continue

				if packet['flags'] & Flags.ACK_REQ:
					if self.drop_rate > 0.0 and random.random() < self.drop_rate:
						self.log(packet['sequence'], packet['channel'], packet['opcode'], Flags.ACK, 0, "Drop")
					else:
						self.send_packet(packet['opcode'], packet['channel'], Flags.ACK)

				if packet['flags'] & Flags.EVENT:
					self.event_callback(packet['channel'], 0xFFFF if not packet['length']
										else struct.unpack('<H', packet['payload'])[0])
					# Deliberately NOT resetting start_time here -- see class
					# docstring. This is the one behavior change vs upstream.
					continue

				self.sequence = (self.sequence + 1) & 0xFF

				if packet['flags'] & Flags.FRAGMENT:
					fragments.extend(packet['payload'])
					start_time = time.time()
					continue

				if packet['flags'] & Flags.NAK:
					status = struct.unpack('<H', packet['payload'][:2])[0]
					if status == Status.CHECKSUM:
						raise ChecksumException("")
					elif status == Status.SEQUENCE:
						raise SequenceException("")
					elif status == Status.TIMEOUT:
						raise TimeoutException("")
					elif status != Status.BUSY:
						raise OMVException(f"Command failed with status: {Status(status).name}")
					return False

				if fragments:
					fragments.extend(packet['payload'])
					packet['payload'] = bytes(fragments)
					packet['length'] = len(fragments)

				return True if not packet['length'] else bytes(packet['payload'])

			if not poll_events:
				raise TimeoutException("Packet receive timeout")

	class _EventSafeOpenMVCamera(_openmv_lib.Camera):
		"""`openmv.Camera` with `_EventSafeTransport` wired in.

		`_resync()` is the only place upstream constructs a `Transport`, and
		it does so freshly on every resync (including the first connect and
		every automatic reconnect after a `ResyncException`) -- so this must
		override `_resync()` itself rather than patch `self.transport` after
		construction, or a resync would silently revert to the buggy
		upstream transport.
		"""

		def _resync(self):
			logging.info("🔁 Resynchronizing")

			self.transport = _EventSafeTransport(
				self._serial, crc=True, seq=True,
				max_payload=Protocol.MIN_PAYLOAD_SIZE, timeout=self.timeout,
				event_callback=self._handle_event, drop_rate=self.drop_rate)

			for attempt in range(self.max_retry):
				try:
					self.transport.reset_sequence()
					self.transport.send_packet(Opcode.PROTO_SYNC, 0, 0)
					if self.transport.recv_packet():
						self.transport.reset_sequence()
						break
				except OMVException:
					if attempt < self.max_retry - 1:
						logging.warning(f"⚠️ Sync attempt {attempt + 1} failed, retrying...")
						continue
					else:
						logging.error("❌ Failed to resync after maximum attempts")
						raise TimeoutException("Resync failed - unable to synchronize with device")

			self.update_capabilities()
			self.transport.update_caps(self.caps['crc'], self.caps['seq'],
									   self.caps['ack'], self.caps['max_payload'])


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
				defaults to `_EventSafeOpenMVCamera`, a thin `openmv.Camera`
				subclass that fixes an upstream transport bug where
				protocol EVENT packets (e.g. firmware stdout "ticks")
				perpetually renew a request's response-wait deadline,
				which can turn a rejected/desynced response into an
				infinite hang instead of a bounded `TimeoutException`.
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
			resolved_client_class = _EventSafeOpenMVCamera

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

	def readChannelStatus(self):
		"""Return a mapping of named custom channels to readiness flags."""
		return self._client.read_status()

	def channelSize(self, name):
		"""Return the presently available byte count for a named channel."""
		return self._client.channel_size(name)

	def readChannel(self, name, size=None):
		"""Read a named channel, optionally with a previously observed byte count."""
		return self._client.channel_read(name, size)

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
