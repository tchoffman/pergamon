"""HTTPS for the hub with no internet: a local certificate authority.

Phones only allow home-screen install, camera and notifications on HTTPS
pages, and public certificates need the internet. So each hub makes its own
root certificate once (valid for years) and signs a server certificate for
whatever LAN addresses it has right now. A phone trusts the root once, on the
/trust page; after that, server certificates can be reissued (new IP, nearing
expiry) without the phone noticing.

Files live in build/tls/ (gitignored). The root's private key never leaves
the hub.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import plistlib
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from rag.config import REPO_ROOT

TLS_DIR = REPO_ROOT / "build" / "tls"
CA_YEARS = 10
# Apple rejects TLS server certificates valid for more than 398 days.
SERVER_DAYS = 397
RENEW_WITHIN_DAYS = 30


@dataclass
class ServerCert:
    cert_path: Path
    key_path: Path
    names: list[str]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    path.chmod(0o600)


def _hub_label() -> str:
    return socket.gethostname().split(".")[0] or "hub"


def ensure_ca(tls_dir: Path | None = None) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Load the hub's root certificate, creating it on first use."""
    tls_dir = tls_dir or TLS_DIR
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = tls_dir / "ca.crt", tls_dir / "ca.key"
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        return cert, key

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # A random suffix keeps two hubs' roots distinguishable on a phone that
    # trusts both.
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"Pergamon hub {_hub_label()} ({uuid.uuid4().hex[:6]})"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Pergamon"),
    ])
    now = _now()
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365 * CA_YEARS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=True, crl_sign=True,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write_key(key_path, key)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key


def hub_names(extra: list[str] | None = None) -> list[str]:
    """Names and addresses a phone might use to reach this hub."""
    from rag.cards import lan_ip
    names = ["localhost", "127.0.0.1", "pergamon.local", f"{_hub_label()}.local"]
    ip = lan_ip()
    if ip:
        names.append(ip)
    for n in extra or []:
        if n and n not in names:
            names.append(n)
    return names


def _san(names: list[str]) -> x509.SubjectAlternativeName:
    entries: list[x509.GeneralName] = []
    for n in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            entries.append(x509.DNSName(n))
    return x509.SubjectAlternativeName(entries)


def _needs_new_server_cert(tls_dir: Path, names: list[str], ca: x509.Certificate) -> bool:
    cert_path, meta_path = tls_dir / "server.crt", tls_dir / "server.json"
    if not cert_path.exists() or not (tls_dir / "server.key").exists() or not meta_path.exists():
        return True
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    if cert.issuer != ca.subject:
        return True
    if cert.not_valid_after_utc - _now() < dt.timedelta(days=RENEW_WITHIN_DAYS):
        return True
    return sorted(json.loads(meta_path.read_text()).get("names", [])) != sorted(names)


def ensure_server_cert(names: list[str] | None = None, tls_dir: Path | None = None) -> ServerCert:
    """A server certificate for `names`, reissued when names change or expiry nears."""
    tls_dir = tls_dir or TLS_DIR
    names = names or hub_names()
    ca_cert, ca_key = ensure_ca(tls_dir)
    out = ServerCert(tls_dir / "server.crt", tls_dir / "server.key", names)
    if not _needs_new_server_cert(tls_dir, names, ca_cert):
        return out

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = _now()
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=SERVER_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=True, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(_san(names), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(out.key_path, key)
    out.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tls_dir / "server.json").write_text(json.dumps({"names": names}))
    return out


def ca_der(tls_dir: Path | None = None) -> bytes:
    """Root certificate in DER form (what Android's certificate installer expects)."""
    tls_dir = tls_dir or TLS_DIR
    cert, _ = ensure_ca(tls_dir)
    return cert.public_bytes(serialization.Encoding.DER)


def ca_fingerprint(tls_dir: Path | None = None) -> str:
    """SHA-256 fingerprint, grouped for reading aloud and comparing on a phone."""
    tls_dir = tls_dir or TLS_DIR
    cert, _ = ensure_ca(tls_dir)
    h = cert.fingerprint(hashes.SHA256()).hex().upper()
    return " ".join(h[i:i + 4] for i in range(0, len(h), 4))


def ca_name(tls_dir: Path | None = None) -> str:
    tls_dir = tls_dir or TLS_DIR
    cert, _ = ensure_ca(tls_dir)
    return cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


def mobileconfig(tls_dir: Path | None = None) -> bytes:
    """An iPhone/iPad configuration profile that installs the root certificate.

    Unsigned: iOS shows it as "Not Verified", which is expected for a profile
    made offline. The user still has to switch on full trust afterwards.
    """
    tls_dir = tls_dir or TLS_DIR
    cert, _ = ensure_ca(tls_dir)
    name = ca_name(tls_dir)
    payload_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, cert.fingerprint(hashes.SHA256()).hex()))
    profile = {
        "PayloadContent": [{
            "PayloadType": "com.apple.security.root",
            "PayloadVersion": 1,
            "PayloadIdentifier": f"org.pergamon.ca.{payload_uuid}",
            "PayloadUUID": payload_uuid,
            "PayloadDisplayName": name,
            "PayloadContent": ca_der(tls_dir),
        }],
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": f"org.pergamon.profile.{payload_uuid}",
        "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, payload_uuid)),
        "PayloadDisplayName": f"Pergamon secure connection ({_hub_label()})",
        "PayloadDescription": "Lets this device open the Pergamon hub over HTTPS without warnings.",
        "PayloadOrganization": "Pergamon",
    }
    return plistlib.dumps(profile)
