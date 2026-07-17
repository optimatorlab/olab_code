"""Cross-platform TLS-generation tests -- locking, pair-matching,
self-healing, and the CLI. Runs on Linux, macOS, and Windows alike; see
test_tls_permissions.py for the POSIX-only permission-bit assertions."""

import multiprocessing
import threading
from pathlib import Path

from cryptography import x509

from olab_camera.tls import (
    _pair_matches,
    build_san_list,
    ensure_local_cert,
    generate_cert_main,
    generate_self_signed_cert,
)


def test_ensure_local_cert_generates_once_and_reuses(tmp_path):
    ssl_dir = tmp_path / "ssl"

    ensure_local_cert(ssl_dir)
    key_bytes_first = (ssl_dir / "ca.key").read_bytes()

    ensure_local_cert(ssl_dir)  # second call: must not regenerate
    key_bytes_second = (ssl_dir / "ca.key").read_bytes()

    assert key_bytes_first == key_bytes_second


def test_ensure_local_cert_returns_ssl_dir(tmp_path):
    ssl_dir = tmp_path / "ssl"
    assert ensure_local_cert(ssl_dir) == ssl_dir


def test_generate_self_signed_cert_never_leaves_a_partial_pair_on_disk(tmp_path):
    """A same-directory temp file + atomic rename means a reader never sees a
    half-written key or cert -- either the old pair (if any) or the fully
    written new one."""
    ssl_dir = tmp_path / "ssl"
    key_path = ssl_dir / "ca.key"
    cert_path = ssl_dir / "ca.crt"

    generate_self_signed_cert(cert_path, key_path)
    files_after = sorted(p.name for p in ssl_dir.iterdir())

    # No leftover .ca.key.<random> / .ca.crt.<random> temp files.
    assert files_after == ["ca.crt", "ca.key"]


def test_pair_matches_true_for_a_freshly_generated_pair(tmp_path):
    ssl_dir = tmp_path / "ssl"
    key_path = ssl_dir / "ca.key"
    cert_path = ssl_dir / "ca.crt"

    generate_self_signed_cert(cert_path, key_path)

    assert _pair_matches(cert_path, key_path) is True


def test_pair_matches_false_when_files_are_missing(tmp_path):
    ssl_dir = tmp_path / "ssl"
    assert _pair_matches(ssl_dir / "ca.crt", ssl_dir / "ca.key") is False


def test_pair_matches_false_for_a_mismatched_pair(tmp_path):
    """Simulates the exact failure mode a crash between the key-write and the
    cert-write would leave behind: two individually well-formed files that
    don't belong to the same generation."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    generate_self_signed_cert(dir_a / "ca.crt", dir_a / "ca.key")
    generate_self_signed_cert(dir_b / "ca.crt", dir_b / "ca.key")

    # Mix key from pair A with cert from pair B.
    assert _pair_matches(dir_b / "ca.crt", dir_a / "ca.key") is False


def test_ensure_local_cert_self_heals_a_mismatched_pair(tmp_path):
    """Reproduces the interruption scenario directly: a prior generation
    attempt left a key and cert on disk that don't match each other (as if
    the process crashed between writing the new key and writing the new
    cert). ensure_local_cert() must detect this via _pair_matches() --  not
    just check that both files exist -- and regenerate a consistent pair."""
    ssl_dir = tmp_path / "ssl"
    ssl_dir.mkdir()
    other_dir = tmp_path / "other"

    generate_self_signed_cert(ssl_dir / "ca.crt", ssl_dir / "ca.key")
    stale_key = (ssl_dir / "ca.key").read_bytes()

    # Overwrite just the key, as if a new key-write landed but the matching
    # cert-write never completed.
    generate_self_signed_cert(other_dir / "ca.crt", ssl_dir / "ca.key")
    assert (ssl_dir / "ca.key").read_bytes() != stale_key
    assert _pair_matches(ssl_dir / "ca.crt", ssl_dir / "ca.key") is False

    ensure_local_cert(ssl_dir)

    assert _pair_matches(ssl_dir / "ca.crt", ssl_dir / "ca.key") is True


def test_concurrent_ensure_local_cert_calls_never_produce_a_mismatched_pair_threads(tmp_path):
    """Spawns several threads all racing to generate the same pair. The
    lock in _generation_lock() must serialize the actual writes, so the
    pair left behind afterward is always internally consistent -- never a
    key from one generation attempt paired with a cert from another."""
    ssl_dir = tmp_path / "ssl"
    errors = []

    def worker():
        try:
            ensure_local_cert(ssl_dir)
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    assert _pair_matches(ssl_dir / "ca.crt", ssl_dir / "ca.key") is True


def _mp_worker(ssl_dir_str):
    # Top-level (picklable) function, required for multiprocessing.Process
    # targets under the "spawn" start method (the default on macOS/Windows).
    # An uncaught exception here makes the child process exit non-zero,
    # which the parent test asserts on -- not just "no exception recorded".
    ensure_local_cert(Path(ssl_dir_str))


def test_concurrent_ensure_local_cert_calls_never_produce_a_mismatched_pair_processes(tmp_path):
    """Same guarantee as the thread test above, but across real separate
    processes -- the actual "cross-process" claim in the module docstring."""
    ssl_dir = tmp_path / "ssl"

    processes = [
        multiprocessing.Process(target=_mp_worker, args=(str(ssl_dir),)) for _ in range(6)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)

    for p in processes:
        assert p.exitcode == 0, f"worker process exited with {p.exitcode}"

    assert _pair_matches(ssl_dir / "ca.crt", ssl_dir / "ca.key") is True


def test_generate_cert_main_force_regenerates_through_the_lock(tmp_path, capsys):
    ssl_dir = tmp_path / "ssl"
    generate_cert_main(["--ssl-dir", str(ssl_dir)])
    first_key = (ssl_dir / "ca.key").read_bytes()

    generate_cert_main(["--ssl-dir", str(ssl_dir), "--force"])
    second_key = (ssl_dir / "ca.key").read_bytes()

    assert first_key != second_key
    assert _pair_matches(ssl_dir / "ca.crt", ssl_dir / "ca.key") is True


def test_build_san_list_includes_common_name_ip_and_dns():
    sans = build_san_list(
        "localhost", ip_addresses=["192.168.0.107"], dns_names=["olab-107"]
    )
    assert x509.DNSName("localhost") in sans
    assert x509.DNSName("olab-107") in sans
    assert any(isinstance(s, x509.IPAddress) and str(s.value) == "192.168.0.107" for s in sans)


def test_build_san_list_deduplicates_repeated_entries():
    sans = build_san_list("localhost", dns_names=["localhost", "olab-107", "olab-107"])
    assert len(sans) == 2  # "localhost" (common name) + "olab-107", each once


def test_generate_self_signed_cert_with_ip_and_dns_sans(tmp_path):
    ssl_dir = tmp_path / "ssl"
    cert_path = ssl_dir / "ca.crt"
    key_path = ssl_dir / "ca.key"

    generate_self_signed_cert(
        cert_path,
        key_path,
        common_name="localhost",
        ip_addresses=["192.168.0.107"],
        dns_names=["olab-107"],
    )

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(ip) for ip in san.get_values_for_type(x509.IPAddress)] == ["192.168.0.107"]
    assert san.get_values_for_type(x509.DNSName) == ["localhost", "olab-107"]


def test_generate_cert_main_accepts_repeatable_ip_and_dns_flags(tmp_path):
    ssl_dir = tmp_path / "ssl"
    generate_cert_main(
        [
            "--ssl-dir",
            str(ssl_dir),
            "--ip-address",
            "192.168.0.107",
            "--ip-address",
            "192.168.0.207",
            "--dns-name",
            "olab-107",
        ]
    )

    cert = x509.load_pem_x509_certificate((ssl_dir / "ca.crt").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {str(ip) for ip in san.get_values_for_type(x509.IPAddress)} == {
        "192.168.0.107",
        "192.168.0.207",
    }
    assert "olab-107" in san.get_values_for_type(x509.DNSName)


def test_generate_cert_main_without_force_regenerates_a_mismatched_pair(tmp_path):
    """Even without --force, a stale/mismatched pair on disk must not be
    treated as "already exists, skip"."""
    ssl_dir = tmp_path / "ssl"
    other_dir = tmp_path / "other"
    generate_self_signed_cert(ssl_dir / "ca.crt", ssl_dir / "ca.key")
    generate_self_signed_cert(other_dir / "ca.crt", ssl_dir / "ca.key")  # mismatch the cert
    assert _pair_matches(ssl_dir / "ca.crt", ssl_dir / "ca.key") is False

    generate_cert_main(["--ssl-dir", str(ssl_dir)])

    assert _pair_matches(ssl_dir / "ca.crt", ssl_dir / "ca.key") is True
