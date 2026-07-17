"""POSIX permission-bit assertions, split out from test_tls.py so the rest
of that file's tests (locking, pair-matching, self-healing, CLI) still run
on Windows -- one of the platforms this module specifically supports for
students, even though the lab's own development happens on Linux."""

import stat
import sys

import pytest

from olab_camera.ca import generate_ca_cert, issue_leaf_cert
from olab_camera.tls import generate_self_signed_cert

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits (0600/0700) aren't meaningful on Windows",
)


def test_generate_self_signed_cert_sets_restrictive_permissions(tmp_path):
    ssl_dir = tmp_path / "ssl"
    key_path = ssl_dir / "ca.key"
    cert_path = ssl_dir / "ca.crt"

    generate_self_signed_cert(cert_path, key_path)

    assert stat.S_IMODE(ssl_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    # The cert is not secret -- normal read-only-for-others permissions are fine.
    assert stat.S_IMODE(cert_path.stat().st_mode) == 0o644


def test_generate_self_signed_cert_permissions_survive_a_permissive_umask(tmp_path):
    import os

    old_umask = os.umask(0o000)  # most permissive possible
    try:
        ssl_dir = tmp_path / "ssl"
        key_path = ssl_dir / "ca.key"
        cert_path = ssl_dir / "ca.crt"

        generate_self_signed_cert(cert_path, key_path)

        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    finally:
        os.umask(old_umask)


def test_generate_ca_cert_sets_restrictive_permissions(tmp_path):
    ca_dir = tmp_path / "ca"
    generate_ca_cert(ca_dir / "ca.crt", ca_dir / "ca.key")

    assert stat.S_IMODE(ca_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((ca_dir / "ca.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((ca_dir / "ca.crt").stat().st_mode) == 0o644


def test_issue_leaf_cert_sets_restrictive_permissions(tmp_path):
    ca_dir = tmp_path / "ca"
    generate_ca_cert(ca_dir / "ca.crt", ca_dir / "ca.key")

    leaf_dir = tmp_path / "leaf"
    issue_leaf_cert(
        ca_dir / "ca.crt",
        ca_dir / "ca.key",
        leaf_dir / "ca.crt",
        leaf_dir / "ca.key",
        common_name="olab-107",
        ip_addresses=["192.168.0.107"],
    )

    assert stat.S_IMODE(leaf_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((leaf_dir / "ca.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((leaf_dir / "ca.crt").stat().st_mode) == 0o644
