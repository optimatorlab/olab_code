"""A lab-private CA and per-device leaf certificates for fleet TLS deployment.

`tls.py`'s auto-generated per-machine certificate (`ensure_local_cert()`)
fixed a real security bug -- olab_camera no longer ships one hardcoded
private key as installable package data -- but it is fundamentally a
*self-signed* cert: nothing ever installs it as a trusted CA anywhere, so
browsers always show the self-signed warning, and (until `tls.py` gained
`ip_addresses`/`dns_names` support) browsing to a camera by its raw IP also
triggered a *second*, name-mismatch warning on top of that.

This module is for deployments (e.g. a lab's vehicle fleet) that want to
eliminate the browser warning entirely, fleet-wide, without reintroducing a
shared private key across every device:

- `generate_ca_cert()` / `olab-camera-generate-ca`: creates ONE root CA
  keypair, run once, by hand, by whoever administers the fleet -- typically
  on a machine that is not itself a deployed camera. The CA private key
  (`ca.key`) must never be committed to a repo, bundled into a package, or
  copied onto a camera device. Only the CA's *public* certificate
  (`ca.crt`) gets distributed -- installed into each viewer machine's
  browser/OS trust store. That installation step (not per-device SAN
  tuning) is what actually removes the browser warning.

- `issue_leaf_cert()` / `olab-camera-issue-cert`: given the CA keypair,
  issues a distinct leaf certificate + private key for ONE device, with
  that device's actual IP and/or DNS name(s) as Subject Alternative Names.
  Each issued leaf pair is copied to that device's own protected local
  directory and passed to `Camera(..., sslPath=...)` -- the explicit-
  `sslPath` true-no-op behavior in `tls.py`/`Camera._ensureSslPath()`
  (olab_camera never touches, locks, chmods, or regenerates a caller-
  supplied cert directory) is exactly the mechanism this is designed to
  feed. A leaf private key leaking compromises only that one device, not
  the whole fleet -- unlike sharing one cert/key pair across every camera,
  which was the exact bug the `ub_camera` -> `olab_camera` migration fixed
  and must not be reintroduced here.

See `docs/deployment.md`'s "Fleet Deployment with a Camera CA" section for
the end-to-end procedure.
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from .tls import _generation_lock, _write_private_file, build_san_list


def _require_shared_parent(cert_path: Path, key_path: Path) -> Path:
	"""Return `cert_path`/`key_path`'s shared parent directory, or raise `ValueError`.

	Both generation functions in this module serialize with a single
	directory-level lock (`tls._generation_lock`) covering *both* the cert
	and key output files -- that only actually protects the pair if both
	files live in the same directory, so mismatched parents are rejected
	up front rather than silently locking (and checking) only one of them.
	"""
	cert_path, key_path = Path(cert_path), Path(key_path)
	if cert_path.parent != key_path.parent:
		raise ValueError(
			f"cert_path and key_path must share the same parent directory so generation "
			f"can be serialized with a single directory-level lock (got {cert_path.parent} "
			f"and {key_path.parent})"
		)
	return cert_path.parent


def generate_ca_cert(
	cert_path: Path,
	key_path: Path,
	common_name: str = "olab-camera Lab CA",
	days: int = 3650,
) -> None:
	"""Write a fresh, self-signed CA certificate and private key to the given paths.

	The resulting cert has `CA:TRUE` and `keyCertSign`/`cRLSign` key usage,
	so it can sign leaf certificates via `issue_leaf_cert()`. It has no
	Subject Alternative Names of its own -- a CA cert is never presented
	directly to a browser as a server cert, only installed into trust
	stores, so SANs on it are meaningless.

	**The private key this writes must never be committed to a repo,
	packaged, or copied onto a deployed camera device.** Run this once, by
	hand, on a machine you control (ideally offline / air-gapped from the
	fleet), and keep `key_path` somewhere protected.

	**Refuses to overwrite an existing `cert_path`/`key_path`.** This check
	happens under the same cross-process directory lock
	(`tls._generation_lock`) `tls.py` uses for its own auto-generated
	certs, so two concurrent `generate_ca_cert()` calls against the same
	directory can never both pass a check-then-write race and interleave
	their key/cert writes -- exactly one wins; the rest see `FileExistsError`
	before generating anything.
	"""
	from cryptography import x509
	from cryptography.hazmat.primitives import hashes, serialization
	from cryptography.hazmat.primitives.asymmetric import rsa
	from cryptography.x509.oid import NameOID

	if days <= 0:
		raise ValueError(f"days must be a positive integer, got {days}")

	cert_path, key_path = Path(cert_path), Path(key_path)
	ca_dir = _require_shared_parent(cert_path, key_path)

	with _generation_lock(ca_dir):
		if cert_path.exists() or key_path.exists():
			raise FileExistsError(
				f"{cert_path} or {key_path} already exists -- generate_ca_cert() refuses to "
				"overwrite an existing CA (replacing it invalidates every leaf cert it "
				"previously issued). Remove the existing pair by hand first if you really "
				"mean to replace it."
			)

		key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
		name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
		now = datetime.datetime.now(datetime.timezone.utc)
		cert = (
			x509.CertificateBuilder()
			.subject_name(name)
			.issuer_name(name)
			.public_key(key.public_key())
			.serial_number(x509.random_serial_number())
			.not_valid_before(now)
			.not_valid_after(now + datetime.timedelta(days=days))
			.add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
			.add_extension(
				x509.KeyUsage(
					digital_signature=False,
					content_commitment=False,
					key_encipherment=False,
					data_encipherment=False,
					key_agreement=False,
					key_cert_sign=True,
					crl_sign=True,
					encipher_only=False,
					decipher_only=False,
				),
				critical=True,
			)
			.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
			.sign(key, hashes.SHA256())
		)

		key_bytes = key.private_bytes(
			encoding=serialization.Encoding.PEM,
			format=serialization.PrivateFormat.TraditionalOpenSSL,
			encryption_algorithm=serialization.NoEncryption(),
		)
		_write_private_file(key_path, key_bytes, mode=0o600)
		_write_private_file(cert_path, cert.public_bytes(serialization.Encoding.PEM), mode=0o644)


def _validate_issuer_for_signing(ca_cert, ca_key, requested_not_valid_after) -> None:
	"""Refuse to sign a leaf with `ca_cert`/`ca_key` unless all of the following hold:

	- `ca_cert` actually has `BasicConstraints(ca=True)`.
	- `ca_cert`'s `KeyUsage` permits certificate signing.
	- `ca_key` is the private key matching `ca_cert`'s public key (not some
	  unrelated key that merely happens to be readable).
	- `ca_cert` is currently within its own validity window.
	- `requested_not_valid_after` does not extend past `ca_cert`'s own expiration
	  -- a leaf that outlives its issuer is nonsensical and usually a sign the
	  caller passed the wrong `--days`.

	Raises `ValueError` describing the first failure found. Called before any
	leaf key is generated or any file is written.
	"""
	from cryptography import x509

	try:
		constraints = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints).value
	except x509.ExtensionNotFound as exc:
		raise ValueError(
			"issuer certificate has no BasicConstraints extension -- refusing to sign "
			"a leaf with it"
		) from exc
	if not constraints.ca:
		raise ValueError(
			"issuer certificate is not a CA (BasicConstraints ca=False) -- refusing to "
			"sign a leaf with it"
		)

	try:
		key_usage = ca_cert.extensions.get_extension_for_class(x509.KeyUsage).value
	except x509.ExtensionNotFound as exc:
		raise ValueError(
			"issuer certificate has no KeyUsage extension -- refusing to sign a leaf with it"
		) from exc
	if not key_usage.key_cert_sign:
		raise ValueError(
			"issuer certificate's KeyUsage does not permit certificate signing "
			"(keyCertSign=False) -- refusing to sign a leaf with it"
		)

	if ca_cert.public_key().public_numbers() != ca_key.public_key().public_numbers():
		raise ValueError(
			"the supplied CA private key does not match the CA certificate's public key"
		)

	now = datetime.datetime.now(datetime.timezone.utc)
	if now < ca_cert.not_valid_before_utc:
		raise ValueError(
			f"issuer certificate is not yet valid (not valid before {ca_cert.not_valid_before_utc})"
		)
	if now > ca_cert.not_valid_after_utc:
		raise ValueError(f"issuer certificate has expired (not valid after {ca_cert.not_valid_after_utc})")

	if requested_not_valid_after > ca_cert.not_valid_after_utc:
		raise ValueError(
			f"requested leaf expiration ({requested_not_valid_after}) exceeds the issuer "
			f"certificate's own expiration ({ca_cert.not_valid_after_utc}) -- reduce --days "
			"or reissue the CA with a longer validity period first"
		)


def issue_leaf_cert(
	ca_cert_path: Path,
	ca_key_path: Path,
	cert_path: Path,
	key_path: Path,
	common_name: str,
	ip_addresses=None,
	dns_names=None,
	days: int = 825,
) -> None:
	"""Issue a leaf certificate for one device, signed by the given CA.

	`ip_addresses`/`dns_names` become the leaf's Subject Alternative Names
	(via `tls.build_san_list()`) -- pass the device's actual, stable
	address(es) here (e.g. its reserved fleet IP). `days` defaults to 825
	(~2.25 years, the historical CA/Browser Forum maximum leaf lifetime)
	rather than `tls.py`'s 10-year self-signed default, since a
	fleet-managed leaf is expected to be reissued periodically, not
	installed once and forgotten.

	Before generating anything, the issuer material is validated (see
	`_validate_issuer_for_signing()`): `ca_cert` must actually be a CA cert
	with signing key usage, `ca_key` must match it, it must be currently
	valid, and the requested leaf expiration must not exceed the issuer's
	own. `cert_path`/`key_path` are also rejected if they resolve to the
	same files as `ca_cert_path`/`ca_key_path` -- issuing "over" the CA's
	own files would destroy it.

	**This function unconditionally refuses to run if `cert_path` or
	`key_path` already exists** -- there is no overwrite/force option here,
	deliberately: writing a new key and a new cert are two separate file
	writes, and no amount of per-file atomicity makes that pair atomic as a
	unit. A crash, or a stream startup reading `sslPath` mid-replacement,
	between those two writes can leave (or observe) a cert/key pair that
	doesn't match -- and unlike `tls.py`'s auto-managed
	`~/.olab_camera/ssl/`, an explicit `sslPath` is intentionally a
	true no-op (see `Camera._ensureSslPath()`): nothing here self-heals a
	broken pair for you. To rotate a device's certificate: call this again
	with a **new, empty** `cert_path`/`key_path` directory (e.g. suffixed
	with a version or date), validate the result there, then deploy it
	through a deliberate maintenance procedure -- e.g. stop the device's
	camera stream, copy the new pair over the old one (safe now, since
	nothing is reading `sslPath` while the stream is stopped), then restart.

	The existence check happens under the same cross-process directory lock
	`generate_ca_cert()` uses (`tls._generation_lock`, scoped to
	`cert_path`/`key_path`'s shared parent directory -- both must live in
	the same directory, see `_require_shared_parent()`), so concurrent
	`issue_leaf_cert()` calls targeting the same output directory (multiple
	threads or processes) can never all pass the check and race to write:
	exactly one wins, the rest see `FileExistsError` before generating
	anything.

	The resulting `cert_path`/`key_path` pair is meant to be copied onto
	the target device and referenced via `Camera(..., sslPath=<their
	parent directory>)` -- write them directly to a `ca.crt`/`ca.key` pair
	in the directory you intend to deploy, since that is the exact
	filename pair `Camera._ensureSslPath()` expects.
	"""
	from cryptography import x509
	from cryptography.hazmat.primitives import hashes, serialization
	from cryptography.hazmat.primitives.asymmetric import rsa
	from cryptography.x509.oid import NameOID

	if days <= 0:
		raise ValueError(f"days must be a positive integer, got {days}")

	ca_cert_path, ca_key_path = Path(ca_cert_path), Path(ca_key_path)
	cert_path, key_path = Path(cert_path), Path(key_path)
	out_dir = _require_shared_parent(cert_path, key_path)
	if cert_path.resolve() == ca_cert_path.resolve() or key_path.resolve() == ca_key_path.resolve():
		raise ValueError(
			f"cert_path/key_path ({cert_path}, {key_path}) overlap the CA's own "
			f"ca_cert_path/ca_key_path ({ca_cert_path}, {ca_key_path}) -- issuing a leaf "
			"there would overwrite the CA itself. Choose separate output paths."
		)

	with _generation_lock(out_dir):
		if cert_path.exists() or key_path.exists():
			raise FileExistsError(
				f"{cert_path} or {key_path} already exists -- issue_leaf_cert() refuses to "
				"overwrite an existing leaf pair (there is no safe way to replace a cert and "
				"its key as a single atomic operation). To rotate a certificate, issue into a "
				"new, empty directory and deploy it via a deliberate maintenance procedure "
				"(e.g. stop the stream, copy the new pair into place, restart) -- see "
				"docs/deployment.md's 'Rotating a device's leaf certificate' section."
			)

		ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
		ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)

		now = datetime.datetime.now(datetime.timezone.utc)
		requested_not_valid_after = now + datetime.timedelta(days=days)
		_validate_issuer_for_signing(ca_cert, ca_key, requested_not_valid_after)

		leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
		subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
		sans = build_san_list(common_name, ip_addresses=ip_addresses, dns_names=dns_names)
		cert = (
			x509.CertificateBuilder()
			.subject_name(subject)
			.issuer_name(ca_cert.subject)
			.public_key(leaf_key.public_key())
			.serial_number(x509.random_serial_number())
			.not_valid_before(now)
			.not_valid_after(requested_not_valid_after)
			.add_extension(x509.SubjectAlternativeName(sans), critical=False)
			.add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
			.add_extension(
				x509.KeyUsage(
					digital_signature=True,
					content_commitment=False,
					key_encipherment=True,
					data_encipherment=False,
					key_agreement=False,
					key_cert_sign=False,
					crl_sign=False,
					encipher_only=False,
					decipher_only=False,
				),
				critical=True,
			)
			.add_extension(
				x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
				critical=False,
			)
			.add_extension(
				x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
				critical=False,
			)
			.sign(ca_key, hashes.SHA256())
		)

		key_bytes = leaf_key.private_bytes(
			encoding=serialization.Encoding.PEM,
			format=serialization.PrivateFormat.TraditionalOpenSSL,
			encryption_algorithm=serialization.NoEncryption(),
		)
		_write_private_file(key_path, key_bytes, mode=0o600)
		_write_private_file(cert_path, cert.public_bytes(serialization.Encoding.PEM), mode=0o644)


def generate_ca_main(argv: list[str] | None = None) -> None:
	parser = argparse.ArgumentParser(
		description="Generate a new lab-private root CA for issuing per-camera TLS leaf "
		"certificates. Run this ONCE, by hand, on a machine you control -- not on a "
		"deployed camera. NEVER commit the resulting private key to a repo or package."
	)
	parser.add_argument(
		"--ca-dir",
		type=Path,
		required=True,
		help="Directory to write ca.key/ca.crt into. Choose a protected, non-repo, "
		"non-package location -- e.g. an encrypted USB drive or the fleet admin's "
		"own home directory, not anywhere shared with students or committed to git.",
	)
	parser.add_argument(
		"--common-name",
		default="olab-camera Lab CA",
		help="CA certificate common name (shown as the issuer in leaf certs). "
		"Defaults to 'olab-camera Lab CA'.",
	)
	parser.add_argument(
		"--days",
		type=int,
		default=3650,
		help="CA certificate validity period in days. Defaults to 3650 (10 years).",
	)
	args = parser.parse_args(argv)

	if args.days <= 0:
		raise SystemExit(f"--days must be a positive integer, got {args.days}")

	cert_path = args.ca_dir / "ca.crt"
	key_path = args.ca_dir / "ca.key"
	# generate_ca_cert() itself is the authoritative check (under a
	# cross-process lock) -- no separate pre-check here, since a pre-check
	# outside that lock would just reintroduce the same check-then-write
	# race between two concurrent invocations of this CLI.
	try:
		generate_ca_cert(cert_path, key_path, common_name=args.common_name, days=args.days)
	except FileExistsError:
		raise SystemExit(
			f"{args.ca_dir} already contains a ca.crt or ca.key -- refusing to overwrite "
			"an existing CA. Remove it by hand first if you really mean to replace it "
			"(replacing it invalidates every leaf cert it previously issued)."
		) from None
	print(f"Generated a new lab CA in {args.ca_dir}")
	print(f"  CA cert (distribute this):        {cert_path}")
	print(f"  CA private key (KEEP OFFLINE):     {key_path}")
	print()
	print("Next steps:")
	print(f"  1. Install {cert_path} into each viewer machine's browser/OS trust store.")
	print(f"     This is what actually removes the browser warning -- see docs/deployment.md.")
	print(f"  2. Issue a leaf cert per camera device with olab-camera-issue-cert.")
	print(f"  3. Protect {key_path} -- anyone who obtains it can impersonate any camera")
	print(f"     a viewer machine trusts this CA for.")


def issue_cert_main(argv: list[str] | None = None) -> None:
	parser = argparse.ArgumentParser(
		description="Issue a per-device TLS leaf certificate signed by a lab CA created "
		"with olab-camera-generate-ca. Run once per camera device (typically once per "
		"vehicle/Pi, shared by every camera that device streams)."
	)
	parser.add_argument(
		"--ca-dir",
		type=Path,
		required=True,
		help="Directory containing the CA's ca.key/ca.crt (from olab-camera-generate-ca).",
	)
	parser.add_argument(
		"--out-dir",
		type=Path,
		required=True,
		help="Directory to write the issued ca.crt/ca.key leaf pair into. Deploy this "
		"directory to the target device and pass it as Camera(..., sslPath=...).",
	)
	parser.add_argument(
		"--common-name",
		required=True,
		help="Leaf certificate common name, e.g. olab-107 (a fleet hostname) or the "
		"device's primary IP.",
	)
	parser.add_argument(
		"--ip-address",
		action="append",
		dest="ip_addresses",
		metavar="IP",
		required=True,
		help="Device IP SAN (repeatable) -- e.g. --ip-address 192.168.0.107, the "
		"device's stable, fleet-reserved address. At least one is required.",
	)
	parser.add_argument(
		"--dns-name",
		action="append",
		dest="dns_names",
		metavar="NAME",
		help="Extra DNS SAN (repeatable), e.g. --dns-name olab-107 for a fleet hostname.",
	)
	parser.add_argument(
		"--days",
		type=int,
		default=825,
		help="Leaf certificate validity period in days. Defaults to 825 (~2.25 years).",
	)
	args = parser.parse_args(argv)

	if args.days <= 0:
		raise SystemExit(f"--days must be a positive integer, got {args.days}")

	ca_cert_path = args.ca_dir / "ca.crt"
	ca_key_path = args.ca_dir / "ca.key"
	if not (ca_cert_path.exists() and ca_key_path.exists()):
		raise SystemExit(
			f"No CA found at {args.ca_dir} -- run olab-camera-generate-ca first, or point "
			"--ca-dir at the directory it wrote ca.crt/ca.key into."
		)

	if args.ca_dir.resolve() == args.out_dir.resolve() or (
		args.ca_dir.resolve() in args.out_dir.resolve().parents
		or args.out_dir.resolve() in args.ca_dir.resolve().parents
	):
		raise SystemExit(
			f"--out-dir ({args.out_dir}) overlaps --ca-dir ({args.ca_dir}) -- issuing a leaf "
			"cert there risks overwriting the CA's own ca.crt/ca.key. Choose a separate, "
			"non-overlapping output directory."
		)

	out_cert_path = args.out_dir / "ca.crt"
	out_key_path = args.out_dir / "ca.key"
	# issue_leaf_cert() itself is the authoritative check (under a
	# cross-process lock) -- no separate pre-check here, since a pre-check
	# outside that lock would just reintroduce the same check-then-write
	# race between two concurrent invocations of this CLI.
	try:
		issue_leaf_cert(
			ca_cert_path,
			ca_key_path,
			out_cert_path,
			out_key_path,
			common_name=args.common_name,
			ip_addresses=args.ip_addresses,
			dns_names=args.dns_names,
			days=args.days,
		)
	except FileExistsError:
		raise SystemExit(
			f"{args.out_dir} already contains a ca.crt or ca.key -- refusing to overwrite an "
			"existing leaf certificate (no in-place-overwrite option is offered here: a cert "
			"and its key can't be replaced as a single atomic operation). To rotate this "
			"device's certificate, point --out-dir at a new, empty directory (e.g. suffixed "
			"with a version or date), then deploy it via a deliberate maintenance procedure -- "
			"see docs/deployment.md's 'Rotating a device's leaf certificate' section."
		) from None
	print(f"Issued a leaf certificate for '{args.common_name}' in {args.out_dir}")
	print(f"  cert: {out_cert_path}")
	print(f"  key:  {out_key_path}")
	print(f"Deploy this directory to the target device and pass sslPath='{args.out_dir}' "
		f"(or wherever you copy it to on-device) to its Camera(...) constructor.")


if __name__ == "__main__":
	generate_ca_main()
