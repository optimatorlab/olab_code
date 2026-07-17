"""Tests for the lab-CA / per-device leaf-cert issuance flow in ca.py."""

import datetime
import multiprocessing
import ssl
import sys
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from olab_camera.ca import generate_ca_cert, generate_ca_main, issue_cert_main, issue_leaf_cert
from olab_camera.tls import _pair_matches, generate_self_signed_cert


def _make_ca(tmp_path):
    ca_dir = tmp_path / "ca"
    generate_ca_cert(ca_dir / "ca.crt", ca_dir / "ca.key")
    return ca_dir


def _write_key(key, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _write_cert(cert, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _self_signed_ca_shaped_cert(
    tmp_path,
    name,
    *,
    ca=True,
    key_cert_sign=True,
    not_valid_before=None,
    not_valid_after=None,
    key=None,
):
    """Build a custom cert (not via generate_ca_cert) so tests can flip exactly
    one CA-validity property at a time -- BasicConstraints, KeyUsage, or the
    validity window -- while leaving the rest correct."""
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_valid_before or now - datetime.timedelta(days=1))
        .not_valid_after(not_valid_after or now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=key_cert_sign,
                crl_sign=key_cert_sign,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    cert = builder.sign(key, hashes.SHA256())
    cert_path = tmp_path / f"{name}.crt"
    key_path = tmp_path / f"{name}.key"
    _write_cert(cert, cert_path)
    _write_key(key, key_path)
    return cert_path, key_path, key


# Top-level (picklable) targets for multiprocessing.Process, required under
# the "spawn" start method (the default on macOS/Windows). Each exits 0 on
# success, 1 on the expected FileExistsError contention outcome, 2 on
# anything else -- so the parent test can assert exact win/lose counts from
# exit codes alone, mirroring test_tls.py's cross-process convention.


def _mp_generate_ca_worker(ca_dir_str):
    from olab_camera.ca import generate_ca_cert

    ca_dir = Path(ca_dir_str)
    try:
        generate_ca_cert(ca_dir / "ca.crt", ca_dir / "ca.key")
    except FileExistsError:
        sys.exit(1)
    except BaseException:  # pragma: no cover - surfaced via exitcode
        sys.exit(2)
    sys.exit(0)


def _mp_issue_leaf_worker(ca_dir_str, out_dir_str):
    from olab_camera.ca import issue_leaf_cert

    ca_dir = Path(ca_dir_str)
    out_dir = Path(out_dir_str)
    try:
        issue_leaf_cert(
            ca_dir / "ca.crt",
            ca_dir / "ca.key",
            out_dir / "ca.crt",
            out_dir / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
        )
    except FileExistsError:
        sys.exit(1)
    except BaseException:  # pragma: no cover - surfaced via exitcode
        sys.exit(2)
    sys.exit(0)


def test_generate_ca_cert_is_a_ca_with_no_sans(tmp_path):
    ca_dir = _make_ca(tmp_path)
    cert = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())

    constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert constraints.ca is True

    key_usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert key_usage.key_cert_sign is True

    with pytest.raises(x509.ExtensionNotFound):
        cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)


def test_issue_leaf_cert_has_ip_and_dns_sans(tmp_path):
    ca_dir = _make_ca(tmp_path)
    leaf_dir = tmp_path / "leaf"
    issue_leaf_cert(
        ca_dir / "ca.crt",
        ca_dir / "ca.key",
        leaf_dir / "ca.crt",
        leaf_dir / "ca.key",
        common_name="olab-107",
        ip_addresses=["192.168.0.107"],
        dns_names=["olab-107"],
    )

    cert = x509.load_pem_x509_certificate((leaf_dir / "ca.crt").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(ip) for ip in san.get_values_for_type(x509.IPAddress)] == ["192.168.0.107"]
    assert san.get_values_for_type(x509.DNSName) == ["olab-107"]

    constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert constraints.ca is False


def test_issue_leaf_cert_is_signed_by_the_ca(tmp_path):
    ca_dir = _make_ca(tmp_path)
    leaf_dir = tmp_path / "leaf"
    issue_leaf_cert(
        ca_dir / "ca.crt",
        ca_dir / "ca.key",
        leaf_dir / "ca.crt",
        leaf_dir / "ca.key",
        common_name="olab-107",
        ip_addresses=["192.168.0.107"],
    )

    ca_cert = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())
    leaf_cert = x509.load_pem_x509_certificate((leaf_dir / "ca.crt").read_bytes())

    assert leaf_cert.issuer == ca_cert.subject
    ca_cert.public_key().verify(
        leaf_cert.signature,
        leaf_cert.tbs_certificate_bytes,
        padding.PKCS1v15(),
        leaf_cert.signature_hash_algorithm,
    )  # raises InvalidSignature on failure


def test_issue_leaf_cert_pair_loads_as_a_valid_tls_server_context(tmp_path):
    ca_dir = _make_ca(tmp_path)
    leaf_dir = tmp_path / "leaf"
    issue_leaf_cert(
        ca_dir / "ca.crt",
        ca_dir / "ca.key",
        leaf_dir / "ca.crt",
        leaf_dir / "ca.key",
        common_name="olab-107",
        ip_addresses=["192.168.0.107"],
    )

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(leaf_dir / "ca.crt"), keyfile=str(leaf_dir / "ca.key"))


def test_generate_ca_main_refuses_to_overwrite_an_existing_ca(tmp_path):
    ca_dir = tmp_path / "ca"
    generate_ca_main(["--ca-dir", str(ca_dir)])

    with pytest.raises(SystemExit):
        generate_ca_main(["--ca-dir", str(ca_dir)])


def test_issue_cert_main_requires_an_existing_ca(tmp_path):
    with pytest.raises(SystemExit):
        issue_cert_main(
            [
                "--ca-dir",
                str(tmp_path / "no-such-ca"),
                "--out-dir",
                str(tmp_path / "leaf"),
                "--common-name",
                "olab-107",
                "--ip-address",
                "192.168.0.107",
            ]
        )


def test_issue_cert_main_end_to_end(tmp_path):
    ca_dir = tmp_path / "ca"
    leaf_dir = tmp_path / "leaf"
    generate_ca_main(["--ca-dir", str(ca_dir)])
    issue_cert_main(
        [
            "--ca-dir",
            str(ca_dir),
            "--out-dir",
            str(leaf_dir),
            "--common-name",
            "olab-107",
            "--ip-address",
            "192.168.0.107",
            "--dns-name",
            "olab-107",
        ]
    )

    assert (leaf_dir / "ca.crt").exists()
    assert (leaf_dir / "ca.key").exists()


# --- Issuer-validation failure paths -----------------------------------------


def test_issue_leaf_cert_rejects_a_non_ca_issuer(tmp_path):
    cert_path, key_path, _ = _self_signed_ca_shaped_cert(tmp_path, "not-a-ca", ca=False)
    with pytest.raises(ValueError, match="not a CA"):
        issue_leaf_cert(
            cert_path,
            key_path,
            tmp_path / "leaf" / "ca.crt",
            tmp_path / "leaf" / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
        )


def test_issue_leaf_cert_rejects_an_issuer_without_cert_sign_key_usage(tmp_path):
    cert_path, key_path, _ = _self_signed_ca_shaped_cert(tmp_path, "no-cert-sign", key_cert_sign=False)
    with pytest.raises(ValueError, match="keyCertSign"):
        issue_leaf_cert(
            cert_path,
            key_path,
            tmp_path / "leaf" / "ca.crt",
            tmp_path / "leaf" / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
        )


def test_issue_leaf_cert_rejects_a_mismatched_key(tmp_path):
    ca_dir = _make_ca(tmp_path)
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_key_path = tmp_path / "wrong.key"
    _write_key(wrong_key, wrong_key_path)

    with pytest.raises(ValueError, match="does not match"):
        issue_leaf_cert(
            ca_dir / "ca.crt",
            wrong_key_path,
            tmp_path / "leaf" / "ca.crt",
            tmp_path / "leaf" / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
        )


def test_issue_leaf_cert_rejects_a_not_yet_valid_issuer(tmp_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    cert_path, key_path, _ = _self_signed_ca_shaped_cert(
        tmp_path,
        "future-ca",
        not_valid_before=now + datetime.timedelta(days=1),
        not_valid_after=now + datetime.timedelta(days=3650),
    )
    with pytest.raises(ValueError, match="not yet valid"):
        issue_leaf_cert(
            cert_path,
            key_path,
            tmp_path / "leaf" / "ca.crt",
            tmp_path / "leaf" / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
        )


def test_issue_leaf_cert_rejects_an_expired_issuer(tmp_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    cert_path, key_path, _ = _self_signed_ca_shaped_cert(
        tmp_path,
        "expired-ca",
        not_valid_before=now - datetime.timedelta(days=730),
        not_valid_after=now - datetime.timedelta(days=1),
    )
    with pytest.raises(ValueError, match="expired"):
        issue_leaf_cert(
            cert_path,
            key_path,
            tmp_path / "leaf" / "ca.crt",
            tmp_path / "leaf" / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
        )


def test_issue_leaf_cert_rejects_a_leaf_expiration_past_the_issuers(tmp_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    cert_path, key_path, _ = _self_signed_ca_shaped_cert(
        tmp_path,
        "short-lived-ca",
        not_valid_before=now - datetime.timedelta(days=1),
        not_valid_after=now + datetime.timedelta(days=30),
    )
    with pytest.raises(ValueError, match="exceeds the issuer"):
        issue_leaf_cert(
            cert_path,
            key_path,
            tmp_path / "leaf" / "ca.crt",
            tmp_path / "leaf" / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
            days=825,  # far longer than the issuer's remaining 30 days
        )


def test_issue_leaf_cert_rejects_output_paths_overlapping_the_ca(tmp_path):
    ca_dir = _make_ca(tmp_path)
    with pytest.raises(ValueError, match="overlap"):
        issue_leaf_cert(
            ca_dir / "ca.crt",
            ca_dir / "ca.key",
            ca_dir / "ca.crt",  # same file as the CA's own cert
            ca_dir / "ca.key",  # same file as the CA's own key
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
        )
    # The CA must survive the rejected call untouched.
    ca_cert = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())
    assert ca_cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True


def test_issue_leaf_cert_requires_cert_and_key_in_the_same_directory(tmp_path):
    """A single directory-level lock protects the check-then-write race for
    BOTH output files together -- that only actually covers the pair if
    they live in one directory, so a split location is rejected outright."""
    ca_dir = _make_ca(tmp_path)
    with pytest.raises(ValueError, match="same parent directory"):
        issue_leaf_cert(
            ca_dir / "ca.crt",
            ca_dir / "ca.key",
            tmp_path / "leaf-cert-dir" / "ca.crt",
            tmp_path / "leaf-key-dir" / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
        )


def test_generate_ca_cert_requires_cert_and_key_in_the_same_directory(tmp_path):
    with pytest.raises(ValueError, match="same parent directory"):
        generate_ca_cert(
            tmp_path / "ca-cert-dir" / "ca.crt",
            tmp_path / "ca-key-dir" / "ca.key",
        )


# --- Concurrency: check-then-write races across threads and processes -------


def test_concurrent_issue_leaf_cert_calls_yield_exactly_one_success_threads(tmp_path):
    """Reproduces the reviewer's finding directly: several threads racing to
    issue the same leaf pair must not all pass the exists() check. The
    per-output-directory lock (shared with generate_ca_cert()) must
    serialize them so exactly one call succeeds and every other call sees
    FileExistsError -- never two callers each writing half a pair."""
    ca_dir = _make_ca(tmp_path)
    out_dir = tmp_path / "leaf"
    outcomes = []
    lock = threading.Lock()

    def worker():
        try:
            issue_leaf_cert(
                ca_dir / "ca.crt",
                ca_dir / "ca.key",
                out_dir / "ca.crt",
                out_dir / "ca.key",
                common_name="olab-107",
                ip_addresses=["192.168.0.107"],
            )
            outcome = "success"
        except FileExistsError:
            outcome = "exists"
        except Exception as exc:  # pragma: no cover - surfaced via assertion below
            outcome = exc
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert outcomes.count("success") == 1, outcomes
    assert outcomes.count("exists") == 7, outcomes
    assert _pair_matches(out_dir / "ca.crt", out_dir / "ca.key") is True


def test_concurrent_issue_leaf_cert_calls_yield_exactly_one_success_processes(tmp_path):
    """Same guarantee as the thread test above, but across real separate
    processes -- the actual cross-process claim `tls._generation_lock`
    makes, reused here via issue_leaf_cert()."""
    ca_dir = _make_ca(tmp_path)
    out_dir = tmp_path / "leaf"

    processes = [
        multiprocessing.Process(target=_mp_issue_leaf_worker, args=(str(ca_dir), str(out_dir)))
        for _ in range(6)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)

    exit_codes = [p.exitcode for p in processes]
    assert exit_codes.count(0) == 1, exit_codes
    assert exit_codes.count(1) == 5, exit_codes
    assert all(code in (0, 1) for code in exit_codes), exit_codes
    assert _pair_matches(out_dir / "ca.crt", out_dir / "ca.key") is True


def test_concurrent_generate_ca_cert_calls_yield_exactly_one_success_threads(tmp_path):
    ca_dir = tmp_path / "ca"
    outcomes = []
    lock = threading.Lock()

    def worker():
        try:
            generate_ca_cert(ca_dir / "ca.crt", ca_dir / "ca.key")
            outcome = "success"
        except FileExistsError:
            outcome = "exists"
        except Exception as exc:  # pragma: no cover - surfaced via assertion below
            outcome = exc
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert outcomes.count("success") == 1, outcomes
    assert outcomes.count("exists") == 7, outcomes
    assert _pair_matches(ca_dir / "ca.crt", ca_dir / "ca.key") is True


def test_concurrent_generate_ca_cert_calls_yield_exactly_one_success_processes(tmp_path):
    ca_dir = tmp_path / "ca"

    processes = [
        multiprocessing.Process(target=_mp_generate_ca_worker, args=(str(ca_dir),)) for _ in range(6)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)

    exit_codes = [p.exitcode for p in processes]
    assert exit_codes.count(0) == 1, exit_codes
    assert exit_codes.count(1) == 5, exit_codes
    assert all(code in (0, 1) for code in exit_codes), exit_codes
    assert _pair_matches(ca_dir / "ca.crt", ca_dir / "ca.key") is True


def test_issue_leaf_cert_refuses_an_existing_output_pair_and_leaves_it_untouched(tmp_path):
    """Regression test for the reissuance-safety finding: issue_leaf_cert() itself
    -- not just the CLI wrapper -- must refuse to touch a pre-existing cert_path/
    key_path, and the rejected call must not perturb the files on disk at all
    (not even re-write them with identical bytes)."""
    ca_dir = _make_ca(tmp_path)
    leaf_dir = tmp_path / "leaf"
    issue_leaf_cert(
        ca_dir / "ca.crt",
        ca_dir / "ca.key",
        leaf_dir / "ca.crt",
        leaf_dir / "ca.key",
        common_name="olab-107",
        ip_addresses=["192.168.0.107"],
    )
    cert_bytes_before = (leaf_dir / "ca.crt").read_bytes()
    key_bytes_before = (leaf_dir / "ca.key").read_bytes()
    cert_mtime_before = (leaf_dir / "ca.crt").stat().st_mtime_ns
    key_mtime_before = (leaf_dir / "ca.key").stat().st_mtime_ns

    with pytest.raises(FileExistsError, match="already exists"):
        issue_leaf_cert(
            ca_dir / "ca.crt",
            ca_dir / "ca.key",
            leaf_dir / "ca.crt",
            leaf_dir / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],  # same device, would-be "reissue"
        )

    assert (leaf_dir / "ca.crt").read_bytes() == cert_bytes_before
    assert (leaf_dir / "ca.key").read_bytes() == key_bytes_before
    assert (leaf_dir / "ca.crt").stat().st_mtime_ns == cert_mtime_before
    assert (leaf_dir / "ca.key").stat().st_mtime_ns == key_mtime_before


def test_issue_leaf_cert_refuses_when_only_the_cert_file_preexists(tmp_path):
    """Even a partial pre-existing pair (as if a prior attempt got partway
    through some other tool's writes) must be refused, not silently completed."""
    ca_dir = _make_ca(tmp_path)
    leaf_dir = tmp_path / "leaf"
    leaf_dir.mkdir()
    (leaf_dir / "ca.crt").write_bytes(b"not a real cert")

    with pytest.raises(FileExistsError, match="already exists"):
        issue_leaf_cert(
            ca_dir / "ca.crt",
            ca_dir / "ca.key",
            leaf_dir / "ca.crt",
            leaf_dir / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
        )
    assert not (leaf_dir / "ca.key").exists()
    assert (leaf_dir / "ca.crt").read_bytes() == b"not a real cert"


def test_leaf_rotation_via_a_new_versioned_directory_and_deliberate_swap(tmp_path):
    """Exercises the documented safe rotation procedure: issue_leaf_cert()
    itself never overwrites a live pair (see the tests above); rotation
    instead issues into a brand-new, empty directory, and only *that*
    validated result is deliberately deployed over the live sslPath -- as a
    maintenance step performed with the stream stopped, so nothing is ever
    reading the live directory while it's being replaced."""
    ca_dir = _make_ca(tmp_path)
    live_dir = tmp_path / "deployed" / "olab-107"  # simulates the on-device sslPath

    # Initial issuance, directly into the "live" directory.
    issue_leaf_cert(
        ca_dir / "ca.crt",
        ca_dir / "ca.key",
        live_dir / "ca.crt",
        live_dir / "ca.key",
        common_name="olab-107",
        ip_addresses=["192.168.0.107"],
    )
    v1_key_bytes = (live_dir / "ca.key").read_bytes()

    # Rotation: issue the replacement into a fresh, separate versioned
    # directory -- never touching live_dir. issue_leaf_cert() succeeds here
    # precisely because this directory doesn't exist yet.
    staged_dir = tmp_path / "deployed" / "olab-107.v2"
    issue_leaf_cert(
        ca_dir / "ca.crt",
        ca_dir / "ca.key",
        staged_dir / "ca.crt",
        staged_dir / "ca.key",
        common_name="olab-107",
        ip_addresses=["192.168.0.107"],
    )

    # Validate the staged pair before deploying it (what an operator would
    # do -- e.g. load it as a real TLS server context -- prior to touching
    # the live directory).
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(staged_dir / "ca.crt"), keyfile=str(staged_dir / "ca.key"))

    # Live pair is still the original -- staging never touched it.
    assert (live_dir / "ca.key").read_bytes() == v1_key_bytes

    # Deliberate maintenance-window deploy step: two separate renames, NOT
    # one atomic exchange -- there's a moment in between where live_dir
    # doesn't exist at all. Each individual rename is atomic on POSIX (no
    # reader ever sees a half-written directory from either one alone),
    # but the two-step sequence as a whole is not, and is only safe here
    # because the stream is stopped for the entire window, so nothing is
    # reading live_dir while it briefly doesn't exist.
    backup_dir = tmp_path / "deployed" / "olab-107.previous"
    live_dir.rename(backup_dir)
    staged_dir.rename(live_dir)

    assert (live_dir / "ca.key").read_bytes() != v1_key_bytes
    assert (backup_dir / "ca.key").read_bytes() == v1_key_bytes

    # The now-live pair still loads as a valid TLS server context post-swap.
    ctx2 = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx2.load_cert_chain(certfile=str(live_dir / "ca.crt"), keyfile=str(live_dir / "ca.key"))


def test_generate_ca_cert_rejects_non_positive_days(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        generate_ca_cert(tmp_path / "ca.crt", tmp_path / "ca.key", days=0)


def test_issue_leaf_cert_rejects_non_positive_days(tmp_path):
    ca_dir = _make_ca(tmp_path)
    with pytest.raises(ValueError, match="positive"):
        issue_leaf_cert(
            ca_dir / "ca.crt",
            ca_dir / "ca.key",
            tmp_path / "leaf" / "ca.crt",
            tmp_path / "leaf" / "ca.key",
            common_name="olab-107",
            ip_addresses=["192.168.0.107"],
            days=-1,
        )


# --- Key/authority identifier extensions -------------------------------------


def test_generate_ca_cert_has_subject_key_identifier(tmp_path):
    ca_dir = _make_ca(tmp_path)
    cert = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())
    cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)  # raises if absent


def test_issue_leaf_cert_has_authority_key_identifier(tmp_path):
    ca_dir = _make_ca(tmp_path)
    leaf_dir = tmp_path / "leaf"
    issue_leaf_cert(
        ca_dir / "ca.crt",
        ca_dir / "ca.key",
        leaf_dir / "ca.crt",
        leaf_dir / "ca.key",
        common_name="olab-107",
        ip_addresses=["192.168.0.107"],
    )
    leaf_cert = x509.load_pem_x509_certificate((leaf_dir / "ca.crt").read_bytes())
    aki = leaf_cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value

    ca_cert = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())
    ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    assert aki.key_identifier == ski.digest


# --- CLI-level safety checks --------------------------------------------------


def test_issue_cert_main_rejects_out_dir_equal_to_ca_dir(tmp_path):
    ca_dir = tmp_path / "ca"
    generate_ca_main(["--ca-dir", str(ca_dir)])

    with pytest.raises(SystemExit, match="overlap"):
        issue_cert_main(
            [
                "--ca-dir",
                str(ca_dir),
                "--out-dir",
                str(ca_dir),
                "--common-name",
                "olab-107",
                "--ip-address",
                "192.168.0.107",
            ]
        )
    # The CA must survive the rejected call untouched.
    ca_cert = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())
    assert ca_cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True


def test_issue_cert_main_rejects_out_dir_nested_inside_ca_dir(tmp_path):
    ca_dir = tmp_path / "ca"
    generate_ca_main(["--ca-dir", str(ca_dir)])

    with pytest.raises(SystemExit, match="overlap"):
        issue_cert_main(
            [
                "--ca-dir",
                str(ca_dir),
                "--out-dir",
                str(ca_dir / "nested-leaf"),
                "--common-name",
                "olab-107",
                "--ip-address",
                "192.168.0.107",
            ]
        )


def test_issue_cert_main_refuses_to_overwrite_an_existing_leaf_without_force(tmp_path):
    ca_dir = tmp_path / "ca"
    leaf_dir = tmp_path / "leaf"
    generate_ca_main(["--ca-dir", str(ca_dir)])
    issue_cert_main(
        [
            "--ca-dir",
            str(ca_dir),
            "--out-dir",
            str(leaf_dir),
            "--common-name",
            "olab-107",
            "--ip-address",
            "192.168.0.107",
        ]
    )
    first_key = (leaf_dir / "ca.key").read_bytes()

    with pytest.raises(SystemExit, match="refusing to overwrite"):
        issue_cert_main(
            [
                "--ca-dir",
                str(ca_dir),
                "--out-dir",
                str(leaf_dir),
                "--common-name",
                "olab-107",
                "--ip-address",
                "192.168.0.107",
            ]
        )
    assert (leaf_dir / "ca.key").read_bytes() == first_key


def test_issue_cert_main_has_no_force_escape_hatch(tmp_path, capsys):
    """--force was deliberately removed: a two-file cert+key overwrite can't be
    made atomic as a pair, so this CLI offers no in-place-overwrite option at
    all -- see 'Rotating a device's leaf certificate' in docs/deployment.md.
    argparse rejects the unrecognized flag before issue_leaf_cert() ever runs."""
    ca_dir = tmp_path / "ca"
    leaf_dir = tmp_path / "leaf"
    generate_ca_main(["--ca-dir", str(ca_dir)])
    issue_cert_main(
        [
            "--ca-dir",
            str(ca_dir),
            "--out-dir",
            str(leaf_dir),
            "--common-name",
            "olab-107",
            "--ip-address",
            "192.168.0.107",
        ]
    )
    first_key = (leaf_dir / "ca.key").read_bytes()

    with pytest.raises(SystemExit):
        issue_cert_main(
            [
                "--ca-dir",
                str(ca_dir),
                "--out-dir",
                str(leaf_dir),
                "--common-name",
                "olab-107",
                "--ip-address",
                "192.168.0.107",
                "--force",
            ]
        )
    assert "unrecognized arguments" in capsys.readouterr().err


def test_generate_ca_main_rejects_non_positive_days(tmp_path):
    with pytest.raises(SystemExit, match="positive"):
        generate_ca_main(["--ca-dir", str(tmp_path / "ca"), "--days", "0"])


def test_issue_cert_main_rejects_non_positive_days(tmp_path):
    ca_dir = tmp_path / "ca"
    generate_ca_main(["--ca-dir", str(ca_dir)])

    with pytest.raises(SystemExit, match="positive"):
        issue_cert_main(
            [
                "--ca-dir",
                str(ca_dir),
                "--out-dir",
                str(tmp_path / "leaf"),
                "--common-name",
                "olab-107",
                "--ip-address",
                "192.168.0.107",
                "--days",
                "0",
            ]
        )
