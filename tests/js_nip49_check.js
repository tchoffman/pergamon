// NIP-49 checks for pergamon.js under Node: the spec's test vector, a
// lock/unlock round trip, a wrong password, and the sign-in token.
// Prints one JSON object of results.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { webcrypto } = require("crypto");

const store = {};
const ctx = {
  crypto: webcrypto,
  localStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  },
  Uint8Array, TextEncoder, JSON, Date, Math, Error, Array, Object, Number, String,
};
ctx.globalThis = ctx;
ctx.window = ctx;
vm.createContext(ctx);
const root = path.join(__dirname, "..", "rag", "static");
vm.runInContext(fs.readFileSync(path.join(root, "vendor", "noble-nostr.js"), "utf8"), ctx);
vm.runInContext(fs.readFileSync(path.join(root, "pergamon.js"), "utf8"), ctx);
const P = ctx.Pergamon, N = ctx.nobleNostr;
const hex = (b) => N.bytesToHex(b);

const out = {};
// Test vector from NIP-49.
const VECTOR = "ncryptsec1qgg9947rlpvqu76pj5ecreduf9jxhselq2nae2kghhvd5g7dgjtcxfqtd67p9m0w57lspw8gsq6yphnm8623nsl8xn9j4jdzz84zm3frztj3z7s35vpzmqf6ksu8r89qk5z2zxfmu5gv8th8wclt0h4p";
out.vector = hex(P.NIP49.unlockKey(VECTOR, "nostr"));

const sec = N.randomBytes(32);
const t0 = Date.now();
const locked = P.NIP49.lockKey(sec, "correct horse", { logN: Number(process.argv[2] || 16) });
out.lock_ms = Date.now() - t0;
out.roundtrip = hex(P.NIP49.unlockKey(locked.ncryptsec, "correct horse")) === hex(sec);
try { P.NIP49.unlockKey(locked.ncryptsec, "wrong"); out.wrong = "unlocked?!"; }
catch (e) { out.wrong = e.message; }
const again = P.NIP49.scryptKey("correct horse", locked.salt, locked.logN);
out.token_stable = P.NIP49.authToken(again) === P.NIP49.authToken(locked.key);
out.token_differs = P.NIP49.authToken(P.NIP49.scryptKey("wrong", locked.salt, locked.logN)) !== P.NIP49.authToken(locked.key);
out.ncryptsec_prefix = locked.ncryptsec.slice(0, 10);
// "Unicode normalization": the spec's example password, composed vs decomposed.
const a = P.NIP49.scryptKey("\u00C5", locked.salt, 4), b = P.NIP49.scryptKey("A\u030A", locked.salt, 4);
out.nfkc = hex(a) === hex(b);
console.log(JSON.stringify(out));
