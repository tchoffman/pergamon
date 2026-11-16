import asyncio
import datetime as dt
import json
import plistlib
import socket
import ssl
import subprocess
import threading
import time

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID
from fastapi.testclient import TestClient

from rag import tls


@pytest.fixture
def tdir(tmp_path):
    return tmp_path / "tls"


def _cert(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_ca_is_created_once_and_reused(tdir):
    a, _ = tls.ensure_ca(tdir)
    b, _ = tls.ensure_ca(tdir)
    assert a == b
    bc = a.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is True
    assert (tdir / "ca.key").stat().st_mode & 0o777 == 0o600


def test_server_cert_covers_names_and_is_phone_acceptable(tdir):
    sc = tls.ensure_server_cert(["192.168.1.20", "pergamon.local", "localhost"], tdir)
    cert = _cert(sc.cert_path)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "pergamon.local" in san.get_values_for_type(x509.DNSName)
    assert [str(i) for i in san.get_values_for_type(x509.IPAddress)] == ["192.168.1.20"]
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    life = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert life <= dt.timedelta(days=398)  # Apple's limit
    ca, _ = tls.ensure_ca(tdir)
    assert cert.issuer == ca.subject


def test_server_cert_verifies_with_openssl(tdir):
    sc = tls.ensure_server_cert(["10.0.0.5", "pergamon.local"], tdir)
    out = subprocess.run(["openssl", "verify", "-CAfile", str(tdir / "ca.crt"),
                          "-purpose", "sslserver", str(sc.cert_path)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_server_cert_reissued_when_ip_changes_but_ca_stays(tdir):
    tls.ensure_server_cert(["10.0.0.5"], tdir)
    first = _cert(tdir / "server.crt").serial_number
    tls.ensure_server_cert(["10.0.0.5"], tdir)
    assert _cert(tdir / "server.crt").serial_number == first  # unchanged names: reuse
    ca_before = (tdir / "ca.crt").read_bytes()
    tls.ensure_server_cert(["10.0.0.9"], tdir)
    assert _cert(tdir / "server.crt").serial_number != first
    assert (tdir / "ca.crt").read_bytes() == ca_before  # phones keep trusting


def test_server_cert_renewed_near_expiry(tdir, monkeypatch):
    tls.ensure_server_cert(["10.0.0.5"], tdir)
    first = _cert(tdir / "server.crt").serial_number
    real_now = tls._now
    monkeypatch.setattr(tls, "_now", lambda: real_now() + dt.timedelta(days=tls.SERVER_DAYS - 10))
    tls.ensure_server_cert(["10.0.0.5"], tdir)
    assert _cert(tdir / "server.crt").serial_number != first


def test_mobileconfig_carries_the_root(tdir):
    prof = plistlib.loads(tls.mobileconfig(tdir))
    [payload] = prof["PayloadContent"]
    assert payload["PayloadType"] == "com.apple.security.root"
    assert payload["PayloadContent"] == tls.ca_der(tdir)
    assert prof["PayloadUUID"] != payload["PayloadUUID"]


def test_fingerprint_is_grouped_sha256(tdir):
    fp = tls.ca_fingerprint(tdir)
    assert len(fp.replace(" ", "")) == 64 and fp.count(" ") == 15


# ───────────────────────────── web endpoints ─────────────────────────────

@pytest.fixture
def client(monkeypatch, tdir):
    from rag import web
    monkeypatch.setattr(tls, "TLS_DIR", tdir)
    monkeypatch.setattr(web, "_https_settings", lambda: (True, 8443))
    return TestClient(web.app)


def test_trust_page_and_downloads(client, tdir):
    ios = client.get("/trust", headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)"})
    assert ios.status_code == 200 and "Certificate Trust Settings" in ios.text
    assert "https://testserver:8443/trust/check" in ios.text
    assert tls.ca_fingerprint() in ios.text
    crt = client.get("/trust/pergamon-ca.crt")
    assert crt.headers["content-type"] == "application/x-x509-ca-cert"
    assert x509.load_der_x509_certificate(crt.content)
    mc = client.get("/trust/pergamon.mobileconfig")
    assert mc.headers["content-type"] == "application/x-apple-aspen-config"


def test_trust_check_allows_cross_origin(client):
    r = client.get("/trust/check")
    assert r.json() == {"secure": True} and r.headers["access-control-allow-origin"] == "*"


def test_welcome_offers_setup_only_over_http(client):
    assert "Set up a secure connection" in client.get("/welcome").text
    assert "Set up a secure connection" not in client.get("https://testserver/welcome").text


def test_trust_404_when_https_off(client, monkeypatch):
    from rag import web
    monkeypatch.setattr(web, "_https_settings", lambda: (False, 8443))
    assert client.get("/trust").status_code == 404
    assert "Set up a secure connection" not in client.get("/welcome").text


# ─────────────────────── real TLS handshake, end to end ───────────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_https_server_handshake_trusts_only_with_hub_ca(tdir):
    import uvicorn
    sc = tls.ensure_server_cert(["127.0.0.1", "pergamon.local", "localhost"], tdir)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config("rag.web:app", host="127.0.0.1", port=port,
                                           ssl_certfile=str(sc.cert_path),
                                           ssl_keyfile=str(sc.key_path), log_level="error"))
    t = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        trusting = ssl.create_default_context(cafile=str(tdir / "ca.crt"))
        for name in ("pergamon.local", "localhost"):
            with socket.create_connection(("127.0.0.1", port)) as raw:
                with trusting.wrap_socket(raw, server_hostname=name) as s:
                    s.sendall(b"GET /trust/check HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                    assert b'"secure": true' in s.recv(4096) + s.recv(4096)
        stranger = ssl.create_default_context()  # system roots only
        with socket.create_connection(("127.0.0.1", port)) as raw:
            with pytest.raises(ssl.SSLCertVerificationError):
                stranger.wrap_socket(raw, server_hostname="pergamon.local")
    finally:
        server.should_exit = True
        t.join(5)
