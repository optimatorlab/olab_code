"""Video streaming classes (MJPEG, WebSocket, WebRTC) used by `Camera.startStream()`.

Like the CV feature classes in `cv_features.py`, these are composed onto the
parent `Camera` instance (`camObject`) rather than inherited, so none of them
need to import `Camera` itself.
"""

import asyncio
import datetime
import math
import socketserver
import ssl
from http import server

import cv2
import numpy as np

STREAM_MAX_WAIT_TIME_SEC = 2  # max time (in seconds) we wait for condition

try:
	import websockets
	_HAS_WEBSOCKETS = True
except ImportError:
	websockets = None
	_HAS_WEBSOCKETS = False

try:
	import aiohttp
	import aiohttp.web
	from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
	from av import VideoFrame
	_HAS_WEBRTC = True
except ImportError:
	_HAS_WEBRTC = False
	VideoStreamTrack = object   # placeholder so CameraVideoTrack class body is valid


class _make_fps_dict():
	"""Internal frame rate tracking utility class.

	This simple data structure tracks frame rate metrics for camera capture and
	feature threads. It maintains a frame counter, start time, and computed actual
	frame rate, with periodic recalculation based on the recheck interval.

	Attributes:
		numFrames (int): Cumulative count of processed frames.
		startTime (datetime): Timestamp when frame counting began.
		actual (float): Current computed frame rate in frames per second.
		recheckInterval (float): Time interval in seconds between FPS recalculations.
	"""
	def __init__(self, startTime=datetime.datetime.now(), recheckInterval=5):
		"""Initialize frame rate tracking dictionary.

		Args:
			startTime (datetime): Initial timestamp for FPS calculation.
			recheckInterval (float): Seconds between FPS recalculations. Defaults to 5.
		"""
		self.numFrames       = 0
		self.startTime       = startTime
		self.actual          = 0
		self.recheckInterval = recheckInterval  # [seconds]

		
		
class StreamingHandler(server.BaseHTTPRequestHandler):
	"""HTTP request handler for MJPEG video streaming.

	Handles HTTP GET requests for the /stream.mjpg endpoint, providing real-time
	MJPEG (Motion JPEG) video streaming from the camera. Supports IP allowlisting
	and blocklisting for access control.

	The handler waits for new frames from the camera using threading conditions,
	applies decorations (ArUco markers, bounding boxes, etc.), and streams frames
	as a multipart HTTP response.

	Attributes:
		camObject: Parent Camera instance providing frames and configuration.

	Endpoints:
		/stream.mjpg: MJPEG video stream endpoint
	"""
	# See https://stackoverflow.com/questions/21631799/how-can-i-pass-parameters-to-a-requesthandler
	def __init__(self, camObject, *args, **kwargs):
		"""Initialize the streaming handler with a camera object.

		Args:
			camObject: Camera instance to stream from.
			*args: Positional arguments passed to BaseHTTPRequestHandler.
			**kwargs: Keyword arguments passed to BaseHTTPRequestHandler.

		Note:
			BaseHTTPRequestHandler calls do_GET inside __init__, so the camObject
			must be set before calling super().__init__().
		"""
		self.camObject = camObject   # This is an instance of one of our camera classes (like CamUSB)
		# BaseHTTPRequestHandler calls do_GET **inside** __init__ !!!
		# So we have to call super().__init__ after setting attributes.
		super().__init__(*args, **kwargs)
			
	def _error(self):
		self.send_error(404)
		self.end_headers()
					 
	def do_GET(self):
		# print(f'DEBUG: path? {self.path}')
		# print(f'DEBUG: clientIP: {self.client_address}')
		if (len(self.camObject.ipAllowlist) > 0):
			if (self.client_address[0] not in self.camObject.ipAllowlist):
				self._error()
				return
		elif (len(self.camObject.ipBlocklist) > 0):
			if (self.client_address[0] in self.camObject.ipBlocklist):
				self._error()
				return
		if (self.path == '/stream.mjpg'):
			self.send_response(200)
			self.send_header('Age', 0)
			self.send_header('Cache-Control', 'no-cache, private')
			self.send_header('Pragma', 'no-cache')
			self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
			self.end_headers()
			try:
				self.camObject.streamIncr(+1)
				# print(f'DEBUG: keepStreaming? {self.camObject.keepStreaming}')
				while self.camObject.keepStreaming:
					if (len(self.camObject.ipAllowlist) > 0) and (self.client_address[0] not in self.camObject.ipAllowlist):
						self.camObject.streamIncr(-1)
						break
					with self.camObject.condition:
						success = self.camObject.condition.wait(STREAM_MAX_WAIT_TIME_SEC)
					
					# We don't get here until the wait condition has finished 
					if (success):
						# Must use a copy if we decorate the frame.
						# Otherwise, our vision processing functions get messed up.
						# myNumpyArray = np.frombuffer(self.camObject.frame, dtype=np.uint8).reshape(self.camObject.res_rows, self.camObject.res_cols, 3)
						myNumpyArray = np.frombuffer(self.camObject.getFrameCopy(), dtype=np.uint8).reshape(self.camObject.res_rows, self.camObject.res_cols, 3)
							
						# Add annotions/decorations
						# updates myNumpyArray in-place
						self.camObject.decorateFrame(myNumpyArray)
															
						frame = cv2.imencode('.jpg',myNumpyArray)[1]
							
						self.wfile.write(b'--FRAME\r\n')
						self.send_header('Content-Type', 'image/jpeg')
						self.send_header('Content-Length', len(frame))
						self.end_headers()
						self.wfile.write(frame)
						self.wfile.write(b'\r\n')
						
						self.camObject.calcFramerate(self.camObject.fps['stream'], 'stream')
			except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
				self.camObject.streamIncr(-1)
			except Exception as e:
				print("ERROR in do_GET: {}".format(e))
				self.camObject.streamIncr(-1)
				# logging.warning('Removed streaming client %s: %s',self.client_address, str(e))
		else:
			self._error()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
	"""Threaded HTTP server for MJPEG video streaming.

	Multi-threaded HTTP server that handles multiple concurrent streaming clients.
	Each client connection is handled in a separate daemon thread.

	If `ssl_context` is given, the *listening* socket is left as plain TCP and
	each accepted connection is individually TLS-wrapped on its own worker
	thread (inside `process_request_thread()`, `ThreadingMixIn`'s actual
	thread target) instead of wrapping the listening socket itself. Wrapping
	the listening socket would make `accept()` perform the TLS handshake
	synchronously in the single main accept loop, so one stalled/slow client
	handshake would block every other client -- including brand-new
	connections -- from being accepted at all. Per-connection wrapping keeps
	the handshake confined to that connection's own thread, bounded by
	`handshake_timeout`.

	Attributes:
		allow_reuse_address (bool): Allow immediate reuse of socket address.
		daemon_threads (bool): All client threads are daemon threads.
		ssl_context (ssl.SSLContext or None): If set, each accepted connection
			is wrapped with this context. If None (default), the server is
			plain HTTP -- unchanged from this class's original behavior.
		log_handshake_failure (callable or None): Called as
			`log_handshake_failure(client_address, exception)` when a client's
			TLS handshake fails or times out. Exceptions raised by this
			callback are swallowed -- a broken logger must not skip socket
			cleanup or crash the worker thread.
		handshake_timeout (float): Seconds a single client's TLS handshake may
			take before being aborted. Only meaningful (and only validated)
			when `ssl_context` is set. Default 5.0.
	"""
	allow_reuse_address = True
	daemon_threads = True

	def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True,
		*, ssl_context=None, log_handshake_failure=None, handshake_timeout=5.0):
		"""Initialize the server, optionally with per-connection TLS wrapping.

		Args:
			server_address (tuple): (host, port) to bind and listen on.
			RequestHandlerClass: Handler class instantiated per connection.
			bind_and_activate (bool): Passed straight through to
				`http.server.HTTPServer`. Default True.
			ssl_context (ssl.SSLContext, optional): If given, each accepted
				connection is TLS-wrapped individually. If None (default),
				the server is plain HTTP.
			log_handshake_failure (callable, optional): `(client_address, exc)
				-> None`, called when a handshake fails/times out. Must be
				callable if given.
			handshake_timeout (float): Seconds allowed per handshake. Must be
				a finite, strictly positive number when `ssl_context` is set;
				ignored (unvalidated) when `ssl_context` is None. Default 5.0.

		Raises:
			ValueError: `ssl_context` is neither None nor an `ssl.SSLContext`;
				`log_handshake_failure` is neither None nor callable; or
				`ssl_context` is set and `handshake_timeout` is not a finite,
				strictly positive number (this also rejects bool, since bool
				is a subclass of int in Python).
		"""
		# Validate the whole TLS-related API boundary *before* calling
		# super().__init__() (which binds/activates the socket) -- a
		# misconfigured server should fail loudly at construction, not bind
		# a port and then misbehave (or silently accept a useless timeout)
		# on the first real connection.
		if ssl_context is not None and not isinstance(ssl_context, ssl.SSLContext):
			raise ValueError(f'ssl_context must be an ssl.SSLContext instance or None, got {ssl_context!r}')
		if log_handshake_failure is not None and not callable(log_handshake_failure):
			raise ValueError(f'log_handshake_failure must be callable or None, got {log_handshake_failure!r}')
		if ssl_context is not None:
			# handshake_timeout is only meaningful once TLS is configured --
			# but must still be a finite, strictly positive real number, since
			# 0/negative disables the intended bound and None/NaN/inf either
			# raise deep inside socket.settimeout()/the handshake itself (as
			# an unhandled ValueError, outside the (ssl.SSLError, OSError)
			# path in process_request_thread) or silently mean "no timeout".
			if (isinstance(handshake_timeout, bool)
					or not isinstance(handshake_timeout, (int, float))
					or not math.isfinite(handshake_timeout)
					or handshake_timeout <= 0):
				raise ValueError(
					f'handshake_timeout must be a finite, positive number when '
					f'ssl_context is set, got {handshake_timeout!r}')

		super().__init__(server_address, RequestHandlerClass, bind_and_activate)
		self.ssl_context = ssl_context
		self.log_handshake_failure = log_handshake_failure
		self.handshake_timeout = handshake_timeout

	def process_request_thread(self, request, client_address):
		"""Same as `ThreadingMixIn.process_request_thread()`, but TLS-wraps
		`request` first when `ssl_context` is set.

		Runs on the per-connection worker thread (not the accept loop), so a
		stalled/failed handshake here cannot block other clients.
		"""
		if self.ssl_context is None:
			# No TLS configured -- unchanged plain-HTTP behavior.
			super().process_request_thread(request, client_address)
			return

		# Python >=3.10: socket.timeout is TimeoutError, a subclass of
		# OSError -- no separate except clause or `import socket` needed.
		wrapped = None
		handshake_error = None
		try:
			request.settimeout(self.handshake_timeout)
			wrapped = self.ssl_context.wrap_socket(request, server_side=True)
			wrapped.settimeout(None)   # restore blocking mode for the actual HTTP work
		except (ssl.SSLError, OSError) as e:
			handshake_error = e
		finally:
			# Runs on *any* failure above -- including wrapped.settimeout(None)
			# failing after a successful handshake -- and even if the
			# caller-supplied log_handshake_failure callback itself raises,
			# since that call is nested inside this same finally.
			if handshake_error is not None:
				if self.log_handshake_failure is not None:
					try:
						self.log_handshake_failure(client_address, handshake_error)
					except Exception:
						pass   # a broken logging callback must not skip socket cleanup
				try:
					(wrapped or request).close()
				except OSError:
					pass

		if handshake_error is not None:
			return
		super().process_request_thread(wrapped, client_address)


class WebSocketStreamingServer:
	"""Asyncio-based WebSocket server for broadcasting JPEG frames.

	Maintains a set of connected WebSocket clients and broadcasts each new
	camera frame to all of them as a binary JPEG message. Runs within a
	single asyncio event loop that lives in its own daemon thread, fully
	isolated from the main threading model.

	IP allowlist and blocklist are enforced on connection and re-checked
	on every frame broadcast.
	"""

	def __init__(self, camObject):
		"""Initialize the server with a reference to the parent camera.

		Args:
			camObject: Camera instance providing frameDeque, condition,
				decorateFrame(), and access-control lists.
		"""
		self.camObject = camObject
		self.clients   = set()   # connected websockets.ServerConnection objects

	def _is_blocked(self, ip):
		"""Return True if the given IP address should be denied access."""
		if self.camObject.ipAllowlist and ip not in self.camObject.ipAllowlist:
			return True
		if ip in self.camObject.ipBlocklist:
			return True
		return False

	async def _handler(self, websocket):
		"""Manage one client connection lifecycle.

		Called by websockets.serve() for each new connection. Checks IP
		access, registers the client, and waits for the connection to close.
		"""
		client_ip = websocket.remote_address[0]
		if self._is_blocked(client_ip):
			await websocket.close(1008, 'Access denied')
			return
		self.clients.add(websocket)
		self.camObject.streamIncr(+1)
		try:
			await websocket.wait_closed()
		finally:
			self.clients.discard(websocket)
			self.camObject.streamIncr(-1)

	def _wait_for_frame(self):
		"""Block until the next frame is ready (runs in a thread-pool executor)."""
		with self.camObject.condition:
			return self.camObject.condition.wait(STREAM_MAX_WAIT_TIME_SEC)

	async def _broadcaster(self):
		"""Encode and broadcast frames to all connected clients until stopped."""
		loop = asyncio.get_running_loop()
		while self.camObject.keepStreaming:
			success = await loop.run_in_executor(None, self._wait_for_frame)

			if not self.camObject.keepStreaming:
				break

			if not success or not self.clients:
				continue

			# Per-frame IP re-check: close any newly-blocked clients
			for ws in list(self.clients):
				if self._is_blocked(ws.remote_address[0]):
					self.clients.discard(ws)
					asyncio.create_task(ws.close(1008, 'Access denied'))

			if not self.clients:
				continue

			frame_array = np.frombuffer(
				self.camObject.getFrameCopy(), dtype=np.uint8
			).reshape(self.camObject.res_rows, self.camObject.res_cols, 3)
			self.camObject.decorateFrame(frame_array)
			_, jpeg = cv2.imencode('.jpg', frame_array)

			websockets.broadcast(self.clients, jpeg.tobytes())
			self.camObject.calcFramerate(self.camObject.fps['stream'], 'stream')

	async def serve(self, port, ssl_context):
		"""Start the WebSocket server and run the broadcaster until stopped.

		Args:
			port (int): TCP port to listen on.
			ssl_context (ssl.SSLContext): TLS context for wss:// connections.
		"""
		async with websockets.serve(self._handler, '', port, ssl=ssl_context):
			await self._broadcaster()


# ---------------------------------------------------------------------------
# WebRTC streaming support
# ---------------------------------------------------------------------------

_WEBRTC_HTML_PAGE = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>olab_camera Stream</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #111; display: flex; flex-direction: column;
           align-items: center; justify-content: center;
           min-height: 100vh; font-family: sans-serif; color: #ccc; }
    video { max-width: 100%; max-height: 90vh; border: 1px solid #333; }
    #status { margin-top: 8px; font-size: 0.85em; }
  </style>
</head>
<body>
  <video id="video" autoplay playsinline muted></video>
  <div id="status">Connecting...</div>
  <script>
    (async function () {
      const status = document.getElementById('status');
      try {
        const pc = new RTCPeerConnection();
        pc.addTransceiver('video', { direction: 'recvonly' });
        pc.ontrack = (e) => {
          document.getElementById('video').srcObject = e.streams[0];
        };
        pc.onconnectionstatechange = () => {
          status.textContent = pc.connectionState;
        };
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        const resp = await fetch('/offer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sdp: offer.sdp, type: offer.type })
        });
        if (!resp.ok) throw new Error('Offer rejected: ' + resp.status);
        await pc.setRemoteDescription(await resp.json());
      } catch (err) {
        status.textContent = 'Error: ' + err.message;
        console.error(err);
      }
    })();
  </script>
</body>
</html>
"""


class CameraVideoTrack(VideoStreamTrack):
	"""aiortc VideoStreamTrack that pulls frames from a Camera's frameDeque.

	Each connected WebRTC client gets its own CameraVideoTrack instance, but
	all instances share the same frameDeque and threading.Condition from the
	parent Camera. The synchronous condition wait is offloaded to a thread-pool
	executor so the asyncio event loop is never blocked.
	"""

	kind = 'video'

	def __init__(self, camObject):
		"""Initialize the track with a reference to the parent camera.

		Args:
			camObject: Camera instance providing frameDeque, condition,
				res_rows, res_cols, decorateFrame(), and calcFramerate().
		"""
		super().__init__()
		self.camObject = camObject

	def _wait_for_frame(self):
		"""Block until the next frame is ready (runs in a thread-pool executor)."""
		with self.camObject.condition:
			self.camObject.condition.wait(STREAM_MAX_WAIT_TIME_SEC)

	async def recv(self):
		"""Return the next video frame to the WebRTC peer.

		Called repeatedly by aiortc. Waits for a new camera frame without
		blocking the event loop, decorates it, converts it to an av.VideoFrame,
		and attaches the correct PTS/time_base before returning.
		"""
		pts, time_base = await self.next_timestamp()

		loop = asyncio.get_running_loop()
		await loop.run_in_executor(None, self._wait_for_frame)

		frame_array = np.frombuffer(
			self.camObject.getFrameCopy(), dtype=np.uint8
		).reshape(self.camObject.res_rows, self.camObject.res_cols, 3)
		self.camObject.decorateFrame(frame_array)

		# OpenCV is BGR; av expects RGB for 'rgb24' format
		frame_rgb = cv2.cvtColor(frame_array, cv2.COLOR_BGR2RGB)
		video_frame = VideoFrame.from_ndarray(frame_rgb, format='rgb24')
		video_frame.pts       = pts
		video_frame.time_base = time_base

		self.camObject.calcFramerate(self.camObject.fps['stream'], 'stream')
		return video_frame


class WebRTCStreamingServer:
	"""aiohttp-based signaling server and WebRTC peer manager.

	Serves the SDP offer/answer signaling endpoint over HTTPS and manages the
	lifecycle of RTCPeerConnection objects — one per connected client. Each
	connection receives its own CameraVideoTrack, which shares the camera's
	frameDeque source.

	Routes:
		GET  /webrtc  — built-in HTML page (signalingMode='html') or
		                JSON descriptor   (signalingMode='json')
		POST /offer   — SDP offer/answer exchange; returns JSON answer
	"""

	def __init__(self, camObject, signalingMode):
		"""Initialize the server.

		Args:
			camObject: Camera instance to stream from.
			signalingMode (str): 'html' to serve a built-in viewer page at
				GET /webrtc, or 'json' to return a JSON descriptor instead.
		"""
		self.camObject     = camObject
		self.signalingMode = signalingMode
		self.pcs           = set()   # active RTCPeerConnection objects

	def _is_blocked(self, ip):
		"""Return True if the given IP address should be denied access."""
		if self.camObject.ipAllowlist and ip not in self.camObject.ipAllowlist:
			return True
		if ip in self.camObject.ipBlocklist:
			return True
		return False

	async def _handle_webrtc_page(self, request):
		"""Serve GET /webrtc — HTML viewer page or JSON descriptor."""
		if self.signalingMode == 'json':
			return aiohttp.web.json_response({'offerUrl': '/offer'})
		return aiohttp.web.Response(content_type='text/html', text=_WEBRTC_HTML_PAGE)

	async def _handle_offer(self, request):
		"""Handle POST /offer — create a peer connection and return SDP answer."""
		client_ip = request.remote
		if self._is_blocked(client_ip):
			raise aiohttp.web.HTTPForbidden()

		params = await request.json()
		offer  = RTCSessionDescription(sdp=params['sdp'], type=params['type'])

		pc = RTCPeerConnection()
		self.pcs.add(pc)
		self.camObject.streamIncr(+1)

		@pc.on('connectionstatechange')
		async def on_connectionstatechange():
			if pc.connectionState in ('failed', 'closed'):
				if pc in self.pcs:
					self.pcs.discard(pc)
					self.camObject.streamIncr(-1)
				await pc.close()

		pc.addTrack(CameraVideoTrack(self.camObject))

		await pc.setRemoteDescription(offer)
		answer = await pc.createAnswer()
		await pc.setLocalDescription(answer)

		return aiohttp.web.json_response({
			'sdp':  pc.localDescription.sdp,
			'type': pc.localDescription.type,
		})

	async def serve(self, port, ssl_context):
		"""Start the signaling server and run until keepStreaming goes False.

		Args:
			port (int): TCP port to listen on.
			ssl_context (ssl.SSLContext): TLS context for https:// connections.
		"""
		app = aiohttp.web.Application()
		app.router.add_get( '/webrtc', self._handle_webrtc_page)
		app.router.add_post('/offer',  self._handle_offer)

		runner = aiohttp.web.AppRunner(app)
		await runner.setup()
		site = aiohttp.web.TCPSite(runner, '', port, ssl_context=ssl_context)
		await site.start()

		try:
			while self.camObject.keepStreaming:
				await asyncio.sleep(0.5)
		finally:
			for pc in list(self.pcs):
				await pc.close()
			self.pcs.clear()
			await runner.cleanup()


