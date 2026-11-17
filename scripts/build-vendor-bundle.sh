#!/usr/bin/env bash
# Rebuild rag/static/vendor/noble-nostr.js: one offline file with the crypto
# the browser needs (Schnorr signing, sha256, and NIP-49 password-locked keys:
# scrypt + XChaCha20-Poly1305). Versions are pinned; the output is committed.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
WORK="$REPO/build/vendor-build"
rm -rf "$WORK" && mkdir -p "$WORK"
cp scripts/vendor/noble-nostr-entry.js "$WORK/entry.js"
cd "$WORK"
npm init -y >/dev/null
npm install --silent --no-audit --no-fund \
  @noble/curves@1.4.0 @noble/hashes@1.4.0 @noble/ciphers@0.5.3 esbuild@0.21.5
{
  echo "/*! Bundled for Pergamon from @noble/curves@1.4.0 + @noble/hashes@1.4.0 + @noble/ciphers@0.5.3 (MIT, (c) Paul Miller, paulmillr.com). Rebuild: scripts/build-vendor-bundle.sh */"
  npx --no-install esbuild entry.js --bundle --minify --format=iife --target=es2019
} > "$REPO/rag/static/vendor/noble-nostr.js"
echo "wrote rag/static/vendor/noble-nostr.js ($(wc -c < "$REPO/rag/static/vendor/noble-nostr.js") bytes)"
