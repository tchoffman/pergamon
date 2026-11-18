// Entry point for rag/static/vendor/noble-nostr.js (built by scripts/build-vendor-bundle.sh).
import { schnorr, secp256k1 } from "@noble/curves/secp256k1";
import { sha256 } from "@noble/hashes/sha256";
import { scrypt } from "@noble/hashes/scrypt";
import { hmac } from "@noble/hashes/hmac";
import { extract as hkdfExtract, expand as hkdfExpand } from "@noble/hashes/hkdf";
import { bytesToHex, hexToBytes, utf8ToBytes, randomBytes, concatBytes } from "@noble/hashes/utils";
import { xchacha20poly1305, chacha20 } from "@noble/ciphers/chacha";

window.nobleNostr = { schnorr, secp256k1, sha256, scrypt, hmac, hkdfExtract, hkdfExpand,
                      bytesToHex, hexToBytes, utf8ToBytes, randomBytes, concatBytes,
                      xchacha20poly1305, chacha20 };
