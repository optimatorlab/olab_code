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

If you're browsing to the camera by its raw IP rather than `localhost` (or
a name matching `--common-name`), add IP and/or DNS Subject Alternative
Names (repeatable) so the browser doesn't *also* show a name-mismatch
warning on top of the expected self-signed one:

```bash
olab-camera-generate-cert --ip-address 192.168.0.107 --dns-name olab-107
```

**This is a development convenience only.** It removes the name-mismatch
warning, not the self-signed warning itself — nothing installs this cert
as a trusted CA anywhere, so every viewer still sees "not secure" the
first time. For a fleet deployment that needs the browser warning gone
entirely, see "Fleet Deployment with a Camera CA" below — do not try to
work around that by handing out one `--ip-address`-tuned self-signed cert
to every camera; distributing the same cert file (and thus the same
private key) to multiple devices reintroduces exactly the shared-key
problem the `ub_camera` → `olab_camera` migration removed.

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
all, see "Fleet Deployment with a Camera CA" or "Reverse Proxy Deployment"
below rather than trying to distribute a single trusted cert to every
camera.

---

## Fleet Deployment with a Camera CA (Zero Browser Warnings, No Shared Key)

For a fleet of devices reached **directly** by IP (e.g. vehicles on a lab
network, browsed straight to `https://192.168.0.107:8000/stream.mjpg` with
no reverse proxy in front) — as opposed to the "Reverse Proxy Deployment"
case below, where one proxy hostname fronts everything — the cert-per-device
model above can't give every viewer a warning-free connection: a self-signed
cert is only ever trusted by machines it was explicitly installed on, and
installing a *different* cert per device onto every viewer's machine doesn't
scale. It's tempting to instead hand every device the *same* cert/key pair
tuned with all their IPs as SANs, but **don't** — that recreates the exact
shared-private-key problem `olab_camera`'s TLS migration fixed (see the
module docstring in `tls.py`), just scoped to your fleet instead of to
every `ub_camera` install worldwide. A single compromised device would leak
a key that impersonates the whole fleet.

The fix used here mirrors ordinary web PKI at lab scale: one CA, installed
once per viewer machine as a trusted root; a distinct leaf cert per device,
signed by that CA, carrying only that device's own SANs.

### 1. Generate the CA (once, offline, by the fleet administrator)

```bash
olab-camera-generate-ca --ca-dir /path/to/protected/camera-ca
```

This writes `ca.crt` (the CA's public certificate) and `ca.key` (its
private key) to `--ca-dir`. **`ca.key` must never be committed to a repo,
bundled into a package, or copied onto a camera device.** Anyone who
obtains it can mint a certificate a trusted viewer will accept for *any*
device in the fleet. Generate it on a machine you control — ideally one
that stays offline / disconnected from the fleet entirely — and store
`ca.key` somewhere protected (an encrypted drive, a password manager's
file storage, etc.), not on a laptop students use.

Re-running this command against the same `--ca-dir` refuses to overwrite
an existing CA (replacing the CA invalidates every leaf cert it already
issued, breaking every already-deployed device until it's reissued).

### 2. Install only the CA's public cert into every viewer's trust store

Distribute `ca.crt` (never `ca.key`) to every machine that should see a
warning-free connection — instructor and student laptops alike — and
install it as a trusted root CA. This is the step that actually removes
the browser warning; per-device SAN tuning alone (the dev-only flow above)
never does. OFM's `config/ssl/setup.sh` already automates exactly this
kind of install (Chromium NSS DB + system trust store) for its own,
separate Caddy front-end certificate — the same technique applies here,
pointed at this CA's `ca.crt` instead. See OFM's own docs for the
lab's actual procedure and IP/hostname inventory.

### 3. Issue a leaf certificate per device

One leaf cert covers one physical device (e.g. one vehicle's Pi) and every
camera that device streams from, since they all share that Pi's IP/host
identity:

```bash
olab-camera-issue-cert \
    --ca-dir /path/to/protected/camera-ca \
    --out-dir /path/to/leaf-certs/olab-107 \
    --common-name olab-107 \
    --ip-address 192.168.0.107 \
    --dns-name olab-107
```

`--ip-address` is required (repeatable) and should be the device's actual,
stable address — not a placeholder. `--dns-name` is optional and repeatable
too, for deployments that also resolve devices by hostname. Leaf certs
default to an 825-day (~2.25 year) validity period, shorter than the
self-signed dev flow's 10-year default, since a fleet-managed leaf is
expected to be reissued periodically rather than generated once and
forgotten.

### 4. Deploy each leaf pair and point `sslPath` at it

Copy the `--out-dir` from step 3 onto the target device, into a directory
only that device's `olab_camera` process needs read access to, then pass
it explicitly:

```python
camera = olab_camera.CameraUSB(
    paramDict={'res_rows': 480, 'res_cols': 640, 'fps_target': 30},
    sslPath='/home/pi/.olab_camera/fleet-ssl',   # the deployed leaf cert dir
)
```

An explicit `sslPath` is never touched, locked, chmod'd, or regenerated by
`olab_camera` itself — see `Camera._ensureSslPath()` — so responsibility
for protecting that directory's permissions on-device is yours (e.g. the
same `0600`/`0700` scheme `olab_camera`'s own auto-generated certs use).

### Rotating a device's leaf certificate

`issue_leaf_cert()` / `olab-camera-issue-cert` **always refuse to run if the
output cert or key already exists** — there is no `--force` or overwrite
option, on purpose. A certificate and its private key are two separate
files; writing each one atomically (which this module does) does not make
*replacing a live pair* atomic as a unit. A crash between the two writes,
or a stream start reading `sslPath` mid-replacement, can leave (or observe)
a mismatched cert/key pair — and because an explicit `sslPath` is
intentionally a true no-op (see above), nothing here self-heals that for
you the way `tls.py`'s auto-managed `~/.olab_camera/ssl/` does.

To rotate a device's certificate safely:

1. **Issue the replacement into a brand-new, empty directory** — never the
   device's live `sslPath` directory:

   ```bash
   olab-camera-issue-cert \
       --ca-dir /path/to/protected/camera-ca \
       --out-dir /path/to/leaf-certs/olab-107.v2 \
       --common-name olab-107 \
       --ip-address 192.168.0.107 \
       --dns-name olab-107
   ```

2. **Validate the staged pair** before deploying it — e.g. load it with
   `ssl.SSLContext.load_cert_chain()`, or spin up a test `Camera` against
   it, so a bad reissue is caught before it ever reaches the device.

3. **Deploy it during a deliberate maintenance step, with the stream
   stopped.** This is a **downtime procedure, not a live or atomic swap**:
   it's two separate renames, with a moment in between where
   `live-ssl-dir` doesn't exist at all. It's safe anyway, but only because
   the stream is stopped for the whole window — `Camera` only reads
   `sslPath` when a stream actually starts (`startStream()`), so as long
   as nothing tries to start a stream against `live-ssl-dir` between these
   two commands, there is no reader to ever observe the gap. Do not
   restart the stream, or start a second one against the same `sslPath`,
   until both renames below have completed:

   ```bash
   # with the camera's stream stopped -- do not restart it until both
   # of the following renames have completed:
   mv /path/to/live-ssl-dir /path/to/live-ssl-dir.previous
   mv /path/to/staged-v2-dir /path/to/live-ssl-dir
   # only now, restart the stream
   ```

   (Each individual `mv` is atomic on POSIX when both paths share a
   filesystem, so neither rename by itself can leave a half-written
   directory behind. That is not the same claim as the two-step sequence
   being atomic as a whole — it isn't, and during the gap between them
   `live-ssl-dir` briefly doesn't exist at all. The procedure is safe
   solely because no reader is active during that gap, not because the
   swap itself is atomic.)

4. Once the new pair is confirmed working, remove `live-ssl-dir.previous`.

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

