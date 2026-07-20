"""Virtual camera backend that accepts pushed JPEG frames over a WSS connection."""

import asyncio
import ssl
import threading

import cv2
import numpy as np

import olab_utils				# A bunch of (somewhat) helpful functions and variables

from .camera import Camera
from .streaming import websockets, _HAS_WEBSOCKETS


class _WebSocketReceiveServer:
	"""Asyncio WebSocket server that receives pushed JPEG frames.

	Accepts exactly one frame-pusher connection at a time (first connect wins;
	additional connections are rejected with close 1008).  When the frame-pusher
	disconnects the slot is freed and the next connection can claim it.

	Runs inside an asyncio event loop in a daemon thread, fully isolated from
	the parent Camera's threading model.  Thread-safety: pushFrame() is called
	from inside the asyncio loop (same thread), so no extra locking is needed
	beyond what frameDeque/announceCondition already provide.
	"""

	def __init__(self, camObject):
		self._cam    = camObject
		self._pusher = None        # currently connected frame-pusher websocket
		self._stop_event = None    # asyncio.Event set by stop()
		self._loop   = None        # the asyncio loop running serve()

	async def _handler(self, websocket):
		client_ip = websocket.remote_address[0]
		if self._cam.ipAllowlist and client_ip not in self._cam.ipAllowlist:
			await websocket.close(1008, 'Access denied')
			return
		if client_ip in self._cam.ipBlocklist:
			await websocket.close(1008, 'Access denied')
			return

		if self._pusher is not None:
			# A pusher is already connected — reject.
			await websocket.close(1008, 'Frame-pusher slot occupied')
			return

		self._pusher = websocket
		self._cam.logger.log('CameraWebSocket: frame-pusher connected', severity=olab_utils.SEVERITY_INFO)
		try:
			async for message in websocket:
				if isinstance(message, bytes):
					self._cam.pushFrame(message)
		except Exception as e:
			self._cam.logger.log(f'CameraWebSocket: pusher error: {e}', severity=olab_utils.SEVERITY_WARNING)
		finally:
			self._pusher = None
			self._cam.logger.log('CameraWebSocket: frame-pusher disconnected', severity=olab_utils.SEVERITY_INFO)

	async def serve(self, port, ssl_context):
		self._loop        = asyncio.get_running_loop()
		self._stop_event  = asyncio.Event()
		async with websockets.serve(self._handler, '', port, ssl=ssl_context):
			await self._stop_event.wait()

	def stop(self):
		"""Signal the serve() coroutine to exit. Safe to call from any thread."""
		if self._loop and self._stop_event:
			self._loop.call_soon_threadsafe(self._stop_event.set)


class CameraWebSocket(Camera):
	"""Virtual camera that accepts pushed JPEG frames over a WebSocket connection.

	Instead of pulling frames from hardware, this class runs a small WSS server
	that a browser-side Cesium viewport connects to and pushes rendered JPEG
	frames.  Once received, frames flow through the standard Camera pipeline
	(frameDeque → streaming/CV features) exactly like any other camera.

	Intended for SITL sim cameras: the GCS opens a popup window running
	``cesium_cam.html``, which renders the Cesium scene from the declared
	camera-mount perspective and pushes frames here.

	Key differences from hardware cameras:
	  - No ``_thread_capture`` poll loop — the WS server thread IS the capture path.
	  - ``wsPort`` attribute is set after the server binds (0 until then).
	  - Only one frame-pusher WebSocket connection is accepted at a time.
	  - ``canChangeRes`` should be False in camera_models.yaml (canvas size is fixed
	    at popup-open time).

	Usage::

		cam = CameraWebSocket(
		    paramDict={'res_rows': 480, 'res_cols': 640, 'fps_target': 10},
		    sslPath='/path/to/ssl',
		    pubCamStatusFunction=my_status_cb,
		)
		cam.start(assetID='107', startStream=True, port=8001)
		# cam.wsPort is now set; publish it so the browser knows where to connect.
	"""

	def __init__(self, paramDict={}, logger=None, sslPath=None,
				 pubCamStatusFunction=None, showFPS=True,
				 ipAllowlist=[], ipBlocklist=[], **kwargs):
		super().__init__(paramDict, logger, sslPath, pubCamStatusFunction,
						 False, showFPS, ipAllowlist, ipBlocklist)
		self.wsPort    = 0          # 0 until WS receive-server has bound a port
		self._ws_server = _WebSocketReceiveServer(self)
		self._ws_thread = None

	# ------------------------------------------------------------------
	# Public frame-push entry point (called from within the WS event loop)
	# ------------------------------------------------------------------

	def pushFrame(self, jpeg_bytes: bytes):
		"""Decode a pushed JPEG and inject it into the standard frame pipeline.

		Mirrors ``CameraROS.callback_CompressedImage`` exactly — imdecode,
		optional digital zoom, frameDeque append, condition notify, fps calc.
		Called by ``_WebSocketReceiveServer._handler`` from inside the asyncio
		event loop; all downstream operations (frameDeque, condition) are
		already thread-safe.
		"""
		try:
			np_arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
			frame  = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
			if frame is None:
				self.logger.log('CameraWebSocket.pushFrame: imdecode returned None',
								severity=olab_utils.SEVERITY_WARNING)
				return
			frame = self.zoomFunction(frame)
			self.frameDeque.append(frame)
			self.announceCondition()
			self.calcFramerate(self.fps['capture'], 'capture')
		except Exception as e:
			self.logger.log(f'CameraWebSocket.pushFrame error: {e}',
							severity=olab_utils.SEVERITY_ERROR)

	# ------------------------------------------------------------------
	# Lifecycle
	# ------------------------------------------------------------------

	def _thread_receive(self, port):
		"""Daemon thread: run the WSS receive server until shutdown() is called."""
		if not _HAS_WEBSOCKETS:
			self.logger.log(
				"CameraWebSocket requires 'websockets'. "
				"Install with: pip install olab-camera[websocket]",
				severity=olab_utils.SEVERITY_ERROR)
			self.wsPort = 0
			return
		try:
			self._ensureSslPath()		# Generate a local cert now, if one doesn't exist yet.

			ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
			ssl_context.load_cert_chain(
				keyfile  = f'{self.sslPath}/ca.key',
				certfile = f'{self.sslPath}/ca.crt')

			self.wsPort = port
			self.camOn  = True
			self.logger.log(f'CameraWebSocket: WSS receive server listening on port {port}',
							severity=olab_utils.SEVERITY_INFO)
			asyncio.run(self._ws_server.serve(port, ssl_context))
		except Exception as e:
			self.logger.log(f'CameraWebSocket._thread_receive error: {e}',
							severity=olab_utils.SEVERITY_ERROR)
		finally:
			self.wsPort = 0
			self.camOn  = False
			self.logger.log('CameraWebSocket: receive server stopped', severity=olab_utils.SEVERITY_INFO)

	def start(self, assetID=None, startStream=False, port=None,
			  ws_port=None, protocol='mjpeg', **kwargs):
		"""Start the WebSocket receive server and optionally the MJPEG/WebRTC stream.

		Args:
			assetID:     Ignored (no hardware init needed), kept for API symmetry.
			startStream: If True, also start the outbound MJPEG/WebRTC stream server.
			port:        Port for the outbound stream server (required if startStream=True).
			ws_port:     Port for the inbound WebSocket receive server.  If omitted,
			             falls back to ``port + 1`` when startStream=True, or ``port``
			             when startStream=False.
			protocol:    Outbound stream protocol ('mjpeg', 'websocket', 'webrtc').
		"""
		# Determine receive port
		if ws_port is not None:
			recv_port = ws_port
		elif startStream and port is not None:
			recv_port = port + 1
		else:
			recv_port = port

		if recv_port is None:
			self.logger.log('CameraWebSocket.start: no port specified', severity=olab_utils.SEVERITY_ERROR)
			return

		self._ws_thread = threading.Thread(target=self._thread_receive, args=(recv_port,), daemon=True)
		self._ws_thread.start()

		if startStream and port is not None:
			self.startStream(port=port, protocol=protocol)

	def shutdown(self):
		"""Stop the WebSocket receive server and any active outbound stream."""
		self._ws_server.stop()
		self.stopStream()
		self.camOn = False
		self.logger.log('CameraWebSocket: shutdown complete', severity=olab_utils.SEVERITY_INFO)
		
