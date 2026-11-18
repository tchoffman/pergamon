"""NIP-44 v2: the encryption used inside Nostr private messages (NIP-17).

secp256k1 key agreement → HKDF-SHA256 → ChaCha20 with HMAC-SHA256, and the
message padded to a size bucket so its length leaks little.
Spec: https://github.com/nostr-protocol/nips/blob/master/44.md
Checked against the official vectors in tests/fixtures/nip44.vectors.json.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os

from coincurve import PrivateKey, PublicKey
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

VERSION = 2
MIN_PLAINTEXT, MAX_PLAINTEXT = 1, 65535


class DecryptError(ValueError):
    pass


def conversation_key(sec_hex: str, pub_hex: str) -> bytes:
    """Same key from either side: (my secret, their public)."""
    shared = PublicKey(b"\x02" + bytes.fromhex(pub_hex)).multiply(bytes.fromhex(sec_hex))
    shared_x = shared.format(compressed=True)[1:33]
    return hmac.new(b"nip44-v2", shared_x, hashlib.sha256).digest()  # HKDF-extract


def message_keys(conv_key: bytes, nonce: bytes) -> tuple[bytes, bytes, bytes]:
    if len(conv_key) != 32 or len(nonce) != 32:
        raise ValueError("conversation key and nonce must be 32 bytes")
    okm = HKDFExpand(algorithm=hashes.SHA256(), length=76, info=nonce).derive(conv_key)
    return okm[:32], okm[32:44], okm[44:76]


def calc_padded_len(n: int) -> int:
    if n <= 32:
        return 32
    next_power = 1 << (math.floor(math.log2(n - 1)) + 1)
    chunk = 32 if next_power <= 256 else next_power // 8
    return chunk * ((n - 1) // chunk + 1)


def _pad(text: str) -> bytes:
    raw = text.encode()
    if not MIN_PLAINTEXT <= len(raw) <= MAX_PLAINTEXT:
        raise ValueError("message must be 1-65535 bytes")
    return len(raw).to_bytes(2, "big") + raw + bytes(calc_padded_len(len(raw)) - len(raw))


def _unpad(padded: bytes) -> str:
    n = int.from_bytes(padded[:2], "big")
    raw = padded[2:2 + n]
    if n < MIN_PLAINTEXT or len(raw) != n or len(padded) != 2 + calc_padded_len(n):
        raise DecryptError("invalid padding")
    return raw.decode()


def _chacha(key: bytes, nonce12: bytes, data: bytes) -> bytes:
    c = Cipher(algorithms.ChaCha20(key, b"\x00\x00\x00\x00" + nonce12), mode=None).encryptor()
    return c.update(data) + c.finalize()


def encrypt(plaintext: str, conv_key: bytes, nonce: bytes | None = None) -> str:
    nonce = nonce or os.urandom(32)
    ck, cn, hk = message_keys(conv_key, nonce)
    ct = _chacha(ck, cn, _pad(plaintext))
    mac = hmac.new(hk, nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(bytes([VERSION]) + nonce + ct + mac).decode()


def decrypt(payload: str, conv_key: bytes) -> str:
    if not payload or payload[0] == "#":
        raise DecryptError("unknown encryption version")
    if not 132 <= len(payload) <= 87472:
        raise DecryptError("invalid payload length")
    try:
        data = base64.b64decode(payload, validate=True)
    except ValueError:
        raise DecryptError("invalid base64")
    if not 99 <= len(data) <= 65603:
        raise DecryptError("invalid data length")
    if data[0] != VERSION:
        raise DecryptError("unknown encryption version")
    nonce, ct, mac = data[1:33], data[33:-32], data[-32:]
    ck, cn, hk = message_keys(conv_key, nonce)
    if not hmac.compare_digest(mac, hmac.new(hk, nonce + ct, hashlib.sha256).digest()):
        raise DecryptError("invalid MAC")
    return _unpad(_chacha(ck, cn, ct))


def pubkey_of(sec_hex: str) -> str:
    return PrivateKey(bytes.fromhex(sec_hex)).public_key.format(compressed=True)[1:].hex()
