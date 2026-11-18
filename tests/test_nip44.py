"""NIP-44 against the official test vectors."""
import json
from pathlib import Path

import pytest

from rag import nip44

V = json.loads((Path(__file__).parent / "fixtures" / "nip44.vectors.json").read_text())["v2"]


@pytest.mark.parametrize("v", V["valid"]["get_conversation_key"])
def test_conversation_key(v):
    assert nip44.conversation_key(v["sec1"], v["pub2"]).hex() == v["conversation_key"]


def test_message_keys():
    mk = V["valid"]["get_message_keys"]
    for k in mk["keys"]:
        ck, cn, hk = nip44.message_keys(bytes.fromhex(mk["conversation_key"]), bytes.fromhex(k["nonce"]))
        assert (ck.hex(), cn.hex(), hk.hex()) == (k["chacha_key"], k["chacha_nonce"], k["hmac_key"])


@pytest.mark.parametrize("n,expected", V["valid"]["calc_padded_len"])
def test_padded_len(n, expected):
    assert nip44.calc_padded_len(n) == expected


@pytest.mark.parametrize("v", V["valid"]["encrypt_decrypt"])
def test_encrypt_decrypt(v):
    ck = nip44.conversation_key(v["sec1"], nip44.pubkey_of(v["sec2"]))
    assert ck.hex() == v["conversation_key"]
    assert nip44.encrypt(v["plaintext"], ck, bytes.fromhex(v["nonce"])) == v["payload"]
    assert nip44.decrypt(v["payload"], ck) == v["plaintext"]
    # and from the other side
    assert nip44.conversation_key(v["sec2"], nip44.pubkey_of(v["sec1"])) == ck


@pytest.mark.parametrize("v", V["valid"]["encrypt_decrypt_long_msg"])
def test_long_messages(v):
    import hashlib
    text = v["pattern"] * v["repeat"]
    assert hashlib.sha256(text.encode()).hexdigest() == v["plaintext_sha256"]
    payload = nip44.encrypt(text, bytes.fromhex(v["conversation_key"]), bytes.fromhex(v["nonce"]))
    assert hashlib.sha256(payload.encode()).hexdigest() == v["payload_sha256"]


@pytest.mark.parametrize("v", V["invalid"]["decrypt"])
def test_invalid_payloads_are_rejected(v):
    with pytest.raises(ValueError):
        nip44.decrypt(v["payload"], bytes.fromhex(v["conversation_key"]))


@pytest.mark.parametrize("n", V["invalid"]["encrypt_msg_lengths"])
def test_invalid_lengths_are_rejected(n):
    with pytest.raises(ValueError):
        nip44.encrypt("a" * n, bytes(32))


@pytest.mark.parametrize("v", V["invalid"]["get_conversation_key"])
def test_invalid_keys_are_rejected(v):
    with pytest.raises(Exception):
        nip44.conversation_key(v["sec1"], v["pub2"])
