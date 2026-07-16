# olab_camera Streaming and Deployment

Streaming protocols, custom TLS certificates, and reverse-proxy deployment
for zero browser TLS warnings.

## Streaming Protocols

`olab_camera` supports three streaming protocols. All use TLS so they can be
embedded in `https://` pages without mixed-content errors.

| Protocol | Extra install | Typical latency | Browser endpoint | Multi-client |
|---|---|---|---|---|
| **MJPEG** (default) | None | 200–500 ms | `https://host:PORT/stream.mjpg` | Yes |
| **WebSocket + JPEG** | `olab-camera[websocket]` | 100–300 ms | See snippet below | Yes |
| **WebRTC** | `olab-camera[webrtc]` | 50–150 ms | `https://host:PORT/webrtc` | Yes |

### MJPEG (default)

No extra dependencies. All existing code continues to work unchanged.

```python
camera.startStream(port=8000)                    # default
camera.startStream(port=8000, protocol='mjpeg')  # explicit
# Visit https://host:8000/stream.mjpg
```

### WebSocket + JPEG

```bash
pip install "olab-camera[websocket]"
```

```python
camera.startStream(port=8001, protocol='websocket')
```

Embed in a web page:

```html
<canvas id="cam" width="640" height="480"></canvas>
<script>
  const canvas = document.getElementById('cam');
  const ctx    = canvas.getContext('2d');
  const ws     = new WebSocket('wss://camera-host:8001');
  ws.binaryType = 'arraybuffer';
  ws.onmessage = (e) => {
    const blob = new Blob([e.data], { type: 'image/jpeg' });
    createImageBitmap(blob).then(bmp => ctx.drawImage(bmp, 0, 0));
  };
</script>
```

### WebRTC

```bash
pip install "olab-camera[webrtc]"
```

```python
camera.startStream(port=8002, protocol='webrtc')
# Built-in viewer: https://host:8002/webrtc
```

`GET /webrtc` serves a self-contained HTML+JS page — open it directly in a
browser with no additional setup. The media stream uses WebRTC DTLS and never
triggers a TLS certificate warning, regardless of whether the signaling
endpoint uses a self-signed or trusted cert.

For integration into your own web page, use `signalingMode='json'`:

```python
camera.startStream(port=8002, protocol='webrtc', signalingMode='json')
# GET  /webrtc  →  {"offerUrl": "/offer"}
# POST /offer   accepts {sdp, type}, returns {sdp, type}
```

### Switching protocols

Only one protocol is active at a time per camera. To switch without stopping
first, pass `force=True`:

```python
camera.startStream(port=8002, protocol='webrtc', force=True)
```

---

## Using Custom SSL Certificates

`olab_camera` requires TLS for every streaming protocol, including the MJPEG
default. If you don't specify `sslPath`, `olab_camera` auto-generates a
fresh, machine-local self-signed certificate the first time you actually
start a stream (not when you construct a `Camera` — capture-only use never
touches the filesystem for TLS), cached at `~/.olab_camera/ssl/`
(`ca.crt`/`ca.key`, the directory and key written with owner-only
permissions) and reused on later runs. **Unlike the old `ub_camera`
package, this certificate is never shipped as installable package data**
— every machine gets its own, generated locally via the `cryptography`
library (works identically on Linux, macOS, and Windows; no `openssl` or
other platform tooling required). Regenerate or inspect it explicitly
with:

```bash
olab-camera-generate-cert                  # generate if missing
olab-camera-generate-cert --force          # regenerate unconditionally
olab-camera-generate-cert --common-name my-camera.local --days 825
```

To use your own certificate instead (e.g., a trusted one from a university
subdomain or a Let's Encrypt reverse proxy) — no code changes needed beyond
passing `sslPath`:

```python
camera = olab_camera.CameraUSB(
    paramDict={'res_rows': 480, 'res_cols': 640, 'fps_target': 30},
    sslPath='/path/to/your/ssl/directory'
)
```

Your SSL directory should contain:
- `ca.crt` — SSL certificate file
- `ca.key` — SSL private key file

For deployments where users should not see a browser security warning at
all, see "Reverse Proxy Deployment" below rather than trying to distribute a
single trusted cert to every camera.

---

## Reverse Proxy Deployment (Zero Browser Warnings)

For multi-user deployments where students access camera streams from personal
machines, a reverse proxy with a trusted certificate eliminates the self-signed
cert browser warning entirely — with no client-side setup required.

**Key principle:** For WebRTC, the proxy handles only the small signaling
messages (`GET /webrtc`, `POST /offer`). The actual video stream flows directly
from the camera device to the browser via WebRTC DTLS — it never touches the
proxy. Latency is unaffected.

### Architecture

```
Student browser
    │  HTTPS signaling (small JSON messages only)
    ▼
Reverse proxy   ←── your domain, trusted cert, public/campus network
    │  forwards to
    ▼
Camera device   ←── local network, self-signed cert or plain HTTP internally
    │  WebRTC media (UDP, direct)
    └──────────────────────────────────────► Student browser
```

### Caddy (recommended — automatic HTTPS)

[Caddy](https://caddyserver.com) automatically provisions and renews
Let's Encrypt certificates. Install it on any internet-accessible server
(a university VM, cloud instance, etc.).

**`Caddyfile`** — one block per camera device:

```
cameras.yourdomain.com {
    # Route each camera to its own subdirectory
    handle /camera1/* {
        uri strip_prefix /camera1
        reverse_proxy camera1-hostname:8002
    }
    handle /camera2/* {
        uri strip_prefix /camera2
        reverse_proxy camera2-hostname:8002
    }
}
```

Start Caddy:
```bash
caddy run --config Caddyfile
```

Students access the built-in WebRTC viewer at:
```
https://cameras.yourdomain.com/camera1/webrtc
```

The camera devices themselves need no configuration change. Caddy terminates
TLS on behalf of the camera; internally it can proxy to either HTTP or HTTPS
(the camera's self-signed cert only needs to be trusted by the proxy, not
by the student's browser).

To allow Caddy to proxy to the camera's self-signed HTTPS endpoint:
```
handle /camera1/* {
    uri strip_prefix /camera1
    reverse_proxy camera1-hostname:8002 {
        transport http {
            tls_insecure_skip_verify   # proxy trusts camera's self-signed cert
        }
    }
}
```

Alternatively, run the camera on plain HTTP internally (no SSL) and let
Caddy provide TLS at the edge — students still get `https://`, and the
internal hop stays on a trusted LAN:

```python
# Camera side: serve without SSL (LAN-only, behind the proxy)
# Not yet supported — use sslPath with a self-signed cert for now,
# and set tls_insecure_skip_verify on the Caddy block above.
```

### Apache

Required modules (enable with `a2enmod` on Debian/Ubuntu):

```bash
sudo a2enmod ssl proxy proxy_http proxy_wstunnel rewrite
sudo systemctl reload apache2
```

**VirtualHost config** (`/etc/apache2/sites-available/cameras.conf`):

```apache
<VirtualHost *:443>
    ServerName cameras.yourdomain.com

    SSLEngine               On
    SSLCertificateFile      /etc/ssl/certs/yourdomain.crt
    SSLCertificateKeyFile   /etc/ssl/private/yourdomain.key
    # SSLCertificateChainFile /etc/ssl/certs/chain.crt   # if required by your CA

    # Allow proxying to the camera's self-signed HTTPS cert
    SSLProxyEngine          On
    SSLProxyVerify          none
    SSLProxyCheckPeerCN     Off
    SSLProxyCheckPeerName   Off

    # ---------------------------------------------------------------
    # Camera 1 — WebRTC (signaling only; media flows direct via UDP)
    # ---------------------------------------------------------------
    ProxyPass        /camera1/  https://camera1-hostname:8002/
    ProxyPassReverse /camera1/  https://camera1-hostname:8002/

    # ---------------------------------------------------------------
    # Camera 1 — WebSocket streaming (wss://)
    # ---------------------------------------------------------------
    RewriteEngine On
    RewriteCond   %{HTTP:Upgrade} websocket [NC]
    RewriteCond   %{HTTP:Connection} upgrade [NC]
    RewriteRule   ^/camera1-ws/(.*)  wss://camera1-hostname:8001/$1  [P,L]

    ProxyPass        /camera1-ws/  wss://camera1-hostname:8001/
    ProxyPassReverse /camera1-ws/  wss://camera1-hostname:8001/

    # Repeat the above blocks for additional cameras
    # (camera2 → port 8002, etc.)
</VirtualHost>

# Redirect plain HTTP to HTTPS
<VirtualHost *:80>
    ServerName cameras.yourdomain.com
    Redirect permanent / https://cameras.yourdomain.com/
</VirtualHost>
```

Enable and reload:
```bash
sudo a2ensite cameras.conf
sudo systemctl reload apache2
```

Students access the built-in WebRTC viewer at:
```
https://cameras.yourdomain.com/camera1/webrtc
```

> **Note on WebSocket path:** Because Apache proxies `/camera1-ws/` to the
> camera's WebSocket port (8001), the browser's `WebSocket()` URL must use
> that path prefix:
> ```js
> const ws = new WebSocket('wss://cameras.yourdomain.com/camera1-ws/');
> ```
> Adjust accordingly if you use a different URL scheme.

### Nginx

For environments where Nginx is already deployed:

```nginx
server {
    listen 443 ssl;
    server_name cameras.yourdomain.com;

    ssl_certificate     /etc/ssl/certs/yourdomain.crt;
    ssl_certificate_key /etc/ssl/private/yourdomain.key;

    location /camera1/ {
        rewrite ^/camera1/(.*) /$1 break;
        proxy_pass https://camera1-hostname:8002;
        proxy_ssl_verify off;          # camera uses self-signed cert

        # Required for WebSocket upgrade (websocket protocol)
        proxy_http_version  1.1;
        proxy_set_header    Upgrade    $http_upgrade;
        proxy_set_header    Connection "upgrade";
    }
}
```

### Access control with the proxy

The camera's `ipAllowlist` / `ipBlocklist` will see the **proxy's IP**, not
the student's IP, when traffic is forwarded. If per-student IP filtering is
needed, either:
- Apply access control at the proxy level (Apache `Require ip`, Caddy
  `basicauth`, Nginx `allow`/`deny`), or
- Pass the student's real IP via `X-Forwarded-For` and update the camera
  access-control logic to read that header (not currently implemented).

---

