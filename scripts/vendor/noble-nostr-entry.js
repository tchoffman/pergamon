// Entry point for rag/static/vendor/noble-nostr.js (built by scripts/build-vendor-bundle.sh).
import { schnorr } from "@noble/curves/secp256k1";
import { sha256 } from "@noble/hashes/sha256";
import { scrypt } from "@noble/hashes/scrypt";
import { bytesToHex, hexToBytes, utf8ToBytes, randomBytes } from "@noble/hashes/utils";
import { xchacha20poly1305 } from "@noble/ciphers/chacha";

window.nobleNostr = { schnorr, sha256, bytesToHex, hexToBytes, utf8ToBytes,
                      scrypt, randomBytes, xchacha20poly1305 };
