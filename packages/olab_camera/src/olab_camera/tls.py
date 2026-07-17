"""Local self-signed TLS certificate generation for olab_camera's HTTPS/WSS streaming.

olab_camera's streaming servers (MJPEG, WebSocket, WebRTC) all serve over
TLS. Earlier versions of this code (as ub_camera, before this package
existed) shipped a single hardcoded self-signed certificate and private key
as installable package data — every installation shared the same private
key, which is a real security problem (anyone with the package could
impersonate any deployed camera's TLS endpoint).

This module generates a fresh, machine-local self-signed certificate
instead, using the `cryptography` library (ships prebuilt wheels for
Linux/macOS/Windows, so it installs without a local compiler) so the same
code path works without any
platform-specific tooling (`openssl`, etc.) on Linux -- the lab's primary
development platform -- as well as macOS and Windows, which students also
use. This is "managed generation," not literal pair-atomicity -- a reader
that bypasses `ensure_local_cert()`/`generate_cert_main()` entirely (e.g.
another process's already-open `load_cert_chain()` call, mid-`--force`)
can still observe an in-progress replacement. Every call that goes
*through* this module's own API is protected by three layers that keep a
concurrent or interrupted *managed* generation from ever leaving a broken
or mismatched pair in place for the *next* managed caller:

1. Each file is written atomically (same-directory temp file + rename) —
   a reader never observes a partially-written file.
2. Generation itself is serialized across processes/threads with a
   blocking OS-level file lock (`fcntl.flock` / `msvcrt.locking`), so two
   concurrent `ensure_local_cert()` calls (including `--force`) can never
   interleave their writes.
3. Before trusting an existing `ca.key`/`ca.crt` pair (skip regeneration),
   `ensure_local_cert()` cryptographically verifies the cert's public key
   actually matches the private key — not just that both files exist. This
   catches the one case locking alone can't prevent: a process crashing
   between the key-write and the cert-write, which would otherwise leave
   two individually well-formed but mutually mismatched files that
   `load_cert_chain()` would reject.

**Only olab_camera's own auto-managed default certificate directory
(`~/.olab_camera/ssl`) is ever touched by this module.** An administrator-
or student-supplied `sslPath` passed explicitly to a `Camera` is never
locked, chmod'd, parsed, or regenerated — see `Camera._ensureSslPath()`.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import os
import sys
import tempfile
from pathlib import Path


def _write_private_file(path: Path, data: bytes, mode: int) -> None:
	"""Atomically write `data` to `path` with the given POSIX permission `mode`.

	`tempfile.mkstemp` creates the temp file with 0600 from the moment it
	exists (ignoring umask) — there is never a window where the file is
	world- or group-readable before we lock down its final mode. The
	rename onto `path` is atomic on POSIX and Windows, so a reader either
	sees the old file (if any) or the fully-written new one, never a
	partial write of that individual file. (Pairing the key and cert
	together is handled separately -- see the module docstring.)
	"""
	path.parent.mkdir(parents=True, exist_ok=True)
	fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
	try:
		os.chmod(tmp_name, mode)
		with os.fdopen(fd, "wb") as f:
			f.write(data)
			f.flush()
			os.fsync(f.fileno())
		os.replace(tmp_name, path)
	except BaseException:
		try:
			os.unlink(tmp_name)
		except OSError:
			pass
		raise


@contextlib.contextmanager
def _generation_lock(ssl_dir: Path):
	"""Block until an exclusive, cross-process lock on `ssl_dir` is held.

	Serializes all cert (re)generation in `ssl_dir` -- concurrent callers
	(including two processes racing at startup, or a `--force` regenerate
	happening alongside another process's first-use generation) queue up
	instead of interleaving writes. Uses OS-level advisory locks
	(`fcntl.flock` on POSIX, `msvcrt.locking` on Windows), which the
	kernel automatically releases if the holding process dies or crashes
	while holding it -- no lock file can be left permanently stuck.
	"""
	ssl_dir.mkdir(parents=True, exist_ok=True)
	os.chmod(ssl_dir, 0o700)
	lock_path = ssl_dir / ".generate.lock"
	# The lock file's own permissions don't matter (it holds no secret data),
	# but keep it inside the owner-only directory regardless.
	lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
	try:
		if sys.platform == "win32":
			import msvcrt

			# msvcrt.locking locks based on the current file position and
			# needs at least one byte present to lock.
			if os.fstat(lock_fd).st_size == 0:
				os.write(lock_fd, b"0")
			os.lseek(lock_fd, 0, os.SEEK_SET)
			# Blocking (LK_LOCK) until the byte becomes available.
			msvcrt.locking(lock_fd, msvcrt.LK_LOCK, 1)
			try:
				yield
			finally:
				os.lseek(lock_fd, 0, os.SEEK_SET)
				msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
		else:
			import fcntl

			fcntl.flock(lock_fd, fcntl.LOCK_EX)  # blocks until acquired
			try:
				yield
			finally:
				fcntl.flock(lock_fd, fcntl.LOCK_UN)
	finally:
		os.close(lock_fd)


def _pair_matches(cert_path: Path, key_path: Path) -> bool:
	"""Return True iff both files exist and the cert's public key matches the private key's.

	Existence of both files alone is not sufficient -- a process crashing
	between the key-write and the cert-write in a prior generation attempt
	would leave two individually well-formed but mismatched files. Treating
	that as "already generated" would produce a pair `load_cert_chain()`
	rejects. This check makes that state self-healing: a mismatch is
	treated the same as "missing" and triggers regeneration.
	"""
	if not (cert_path.exists() and key_path.exists()):
		return False
	try:
		from cryptography import x509
		from cryptography.hazmat.primitives import serialization

		cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
		key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
		return cert.public_key().public_numbers() == key.public_key().public_numbers()
	except Exception:
		return False


def build_san_list(common_name: str, ip_addresses=None, dns_names=None):
	"""Build a de-duplicated `x509.SubjectAlternativeName` entry list.

	`common_name` is always included as a `DNSName` (matching this module's
	historical behavior). Each string in `ip_addresses` is parsed with
	`ipaddress.ip_address()` and added as an `x509.IPAddress` -- a plain
	`DNSName` entry containing an IP literal does not satisfy browsers'
	hostname-vs-IP SAN matching, which is the whole reason this helper
	exists. Each string in `dns_names` is added as an `x509.DNSName`.
	Shared by the dev-only self-signed flow here and the CA leaf-issuance
	flow in `ca.py`.
	"""
	import ipaddress

	from cryptography import x509

	seen = set()
	sans: list = []

	def _add(entry):
		key = (type(entry), str(entry))
		if key not in seen:
			seen.add(key)
			sans.append(entry)

	_add(x509.DNSName(common_name))
	for dns_name in dns_names or ():
		_add(x509.DNSName(dns_name))
	for ip in ip_addresses or ():
		_add(x509.IPAddress(ipaddress.ip_address(ip)))
	return sans


def generate_self_signed_cert(
	cert_path: Path,
	key_path: Path,
	common_name: str = "localhost",
	days: int = 3650,
	ip_addresses=None,
	dns_names=None,
) -> None:
	"""Write a fresh self-signed certificate and private key to the given paths.

	`ip_addresses` and `dns_names` add extra `x509.IPAddress`/`x509.DNSName`
	Subject Alternative Names beyond `common_name` -- useful so a browser
	visiting a camera by its raw IP doesn't also hit a name-mismatch
	warning on top of the expected self-signed warning. **This flow is a
	development convenience only**: it does not remove the browser's
	self-signed warning, because nothing installs this cert as a trusted
	CA anywhere. For deployments that need warning-free browsing across a
	fleet of devices, see `ca.py` -- a real CA whose public cert gets
	installed into browser/OS trust stores, plus per-device leaf certs it
	signs.

	`key_path`'s parent directory is created owner-only (0700) if it does
	not already exist; the key itself is written owner-read/write-only
	(0600). Each file is written atomically (see `_write_private_file`).
	Callers that need the key+cert pair itself to stay consistent under
	concurrent/interrupted generation should go through `ensure_local_cert()`
	instead, which adds cross-process locking and pair verification.
	"""
	from cryptography import x509
	from cryptography.hazmat.primitives import hashes, serialization
	from cryptography.hazmat.primitives.asymmetric import rsa
	from cryptography.x509.oid import NameOID

	key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
	name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
	now = datetime.datetime.now(datetime.timezone.utc)
	sans = build_san_list(common_name, ip_addresses=ip_addresses, dns_names=dns_names)
	cert = (
		x509.CertificateBuilder()
		.subject_name(name)
		.issuer_name(name)
		.public_key(key.public_key())
		.serial_number(x509.random_serial_number())
		.not_valid_before(now)
		.not_valid_after(now + datetime.timedelta(days=days))
		.add_extension(x509.SubjectAlternativeName(sans), critical=False)
		.sign(key, hashes.SHA256())
	)

	key_path.parent.mkdir(parents=True, exist_ok=True)
	os.chmod(key_path.parent, 0o700)

	key_bytes = key.private_bytes(
		encoding=serialization.Encoding.PEM,
		format=serialization.PrivateFormat.TraditionalOpenSSL,
		encryption_algorithm=serialization.NoEncryption(),
	)
	_write_private_file(key_path, key_bytes, mode=0o600)
	_write_private_file(cert_path, cert.public_bytes(serialization.Encoding.PEM), mode=0o644)


def ensure_local_cert(ssl_dir: Path, common_name: str = "localhost") -> Path:
	"""Return `ssl_dir`, generating a self-signed ca.key/ca.crt pair in it if missing or mismatched.

	Each machine gets its own cert on first use — nothing is shared across
	installations. Generation is serialized across processes/threads (see
	`_generation_lock`) and an existing pair is only trusted if it's
	cryptographically consistent (see `_pair_matches`), so concurrent or
	interrupted generation can never leave a broken pair in place.

	Callers should invoke this only when actually about to serve TLS (e.g.
	inside `startStream()`, not in `Camera.__init__`), so that capture-only
	use of a `Camera` (e.g. in a container, a service account, or a
	read-only-home environment) never touches the filesystem or requires a
	home directory.
	"""
	ssl_dir = Path(ssl_dir)
	cert_path = ssl_dir / "ca.crt"
	key_path = ssl_dir / "ca.key"
	with _generation_lock(ssl_dir):
		if not _pair_matches(cert_path, key_path):
			try:
				generate_self_signed_cert(cert_path, key_path, common_name=common_name)
			except ImportError as exc:
				raise RuntimeError(
					"olab-camera needs the 'cryptography' package to generate a local "
					"TLS certificate for streaming. Install with: pip install olab-camera"
				) from exc
	return ssl_dir


def generate_cert_main(argv: list[str] | None = None) -> None:
	parser = argparse.ArgumentParser(
		description="Generate (or regenerate) a local self-signed TLS certificate for olab_camera streaming."
	)
	parser.add_argument(
		"--ssl-dir",
		type=Path,
		default=Path.home() / ".olab_camera" / "ssl",
		help="Directory to write ca.key/ca.crt into. Defaults to ~/.olab_camera/ssl.",
	)
	parser.add_argument(
		"--common-name",
		default="localhost",
		help="Certificate common name / SAN DNS entry. Defaults to 'localhost'.",
	)
	parser.add_argument(
		"--days",
		type=int,
		default=3650,
		help="Certificate validity period in days. Defaults to 3650 (10 years).",
	)
	parser.add_argument(
		"--force",
		action="store_true",
		help="Regenerate even if a valid certificate already exists at --ssl-dir.",
	)
	parser.add_argument(
		"--ip-address",
		action="append",
		dest="ip_addresses",
		metavar="IP",
		help="Extra IP SAN to add (repeatable), e.g. --ip-address 192.168.0.107. "
		"DEVELOPMENT CONVENIENCE ONLY: this stops a browser hitting a name-mismatch "
		"warning when visiting by IP, but does NOT remove the self-signed warning "
		"itself -- nothing installs this cert as a trusted CA. For a deployment "
		"that needs warning-free browsing across a fleet of devices, use the CA "
		"and per-device leaf certs from `olab-camera-generate-ca` / "
		"`olab-camera-issue-cert` instead (see ca.py / docs/deployment.md).",
	)
	parser.add_argument(
		"--dns-name",
		action="append",
		dest="dns_names",
		metavar="NAME",
		help="Extra DNS SAN to add (repeatable), e.g. --dns-name my-camera.local. "
		"Same development-only caveat as --ip-address.",
	)
	args = parser.parse_args(argv)

	cert_path = args.ssl_dir / "ca.crt"
	key_path = args.ssl_dir / "ca.key"

	with _generation_lock(args.ssl_dir):
		if not args.force and _pair_matches(cert_path, key_path):
			print(f"Certificate already exists at {args.ssl_dir} (use --force to regenerate).")
			return

		generate_self_signed_cert(
			cert_path,
			key_path,
			common_name=args.common_name,
			days=args.days,
			ip_addresses=args.ip_addresses,
			dns_names=args.dns_names,
		)
		print(f"Generated a local self-signed certificate in {args.ssl_dir}")
		print(f"  cert: {cert_path}")
		print(f"  key:  {key_path}")


if __name__ == "__main__":
	generate_cert_main()
