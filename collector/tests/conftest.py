"""Fixtures des tests hors appareil : un certificat auto-signé et un faux réveil."""
from __future__ import annotations

import subprocess

import pytest

from fake_device import FakeDevice


@pytest.fixture(scope="session")
def cert(tmp_path_factory):
    """Certificat auto-signé, généré une fois par session (openssl requis)."""
    d = tmp_path_factory.mktemp("cert")
    certfile, keyfile = d / "cert.pem", d / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(keyfile),
         "-out", str(certfile), "-days", "2", "-nodes", "-subj", "/CN=fake-somneo"],
        check=True, capture_output=True,
    )
    return str(certfile), str(keyfile)


@pytest.fixture
def fake(cert):
    with FakeDevice(cert[0], cert[1]) as dev:
        yield dev
