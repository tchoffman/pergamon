// pergamon.js NIP-44 against the official vectors (tests/fixtures/nip44.vectors.json).
// Prints {passed, failed: [...]}.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { webcrypto } = require("crypto");

const store = {};
const ctx = {
  crypto: webcrypto, btoa, atob, TextEncoder, TextDecoder,
  localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
                  removeItem: (k) => { delete store[k]; } },
  Uint8Array, JSON, Date, Math, Error, Array, Object, Number, String,
};
ctx.globalThis = ctx; ctx.window = ctx;
vm.createContext(ctx);
const root = path.join(__dirname, "..", "rag", "static");
vm.runInContext(fs.readFileSync(path.join(root, "vendor", "noble-nostr.js"), "utf8"), ctx);
vm.runInContext(fs.readFileSync(path.join(root, "pergamon.js"), "utf8"), ctx);
const P = ctx.Pergamon, N = ctx.nobleNostr, X = P.NIP44;
const hex = (b) => N.bytesToHex(b), unhex = (h) => N.hexToBytes(h);
const pubOf = (sec) => hex(N.schnorr.getPublicKey(sec));
const V = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "nip44.vectors.json"), "utf8")).v2;

let passed = 0; const failed = [];
const check = (name, ok) => { if (ok) passed++; else failed.push(name); };
const throws = (f) => { try { f(); return false; } catch (e) { return true; } };

V.valid.get_conversation_key.forEach((v, i) =>
  check("ck " + i, hex(X.conversationKey(v.sec1, v.pub2)) === v.conversation_key));
const mk = V.valid.get_message_keys;
mk.keys.forEach((k, i) => {
  const m = X.messageKeys(unhex(mk.conversation_key), unhex(k.nonce));
  check("mk " + i, hex(m.key) === k.chacha_key && hex(m.nonce) === k.chacha_nonce && hex(m.hmac) === k.hmac_key);
});
V.valid.calc_padded_len.forEach(([n, e]) => check("pad " + n, X.paddedLen(n) === e));
V.valid.encrypt_decrypt.forEach((v, i) => {
  const ck = X.conversationKey(v.sec1, pubOf(v.sec2));
  check("ed ck " + i, hex(ck) === v.conversation_key);
  check("enc " + i, X.encrypt(v.plaintext, ck, unhex(v.nonce)) === v.payload);
  check("dec " + i, X.decrypt(v.payload, ck) === v.plaintext);
});
V.valid.encrypt_decrypt_long_msg.forEach((v, i) => {
  const text = v.pattern.repeat(v.repeat);
  const payload = X.encrypt(text, unhex(v.conversation_key), unhex(v.nonce));
  check("long " + i, hex(N.sha256(N.utf8ToBytes(payload))) === v.payload_sha256);
});
V.invalid.decrypt.forEach((v, i) => check("bad dec " + i, throws(() => X.decrypt(v.payload, unhex(v.conversation_key)))));
V.invalid.encrypt_msg_lengths.forEach((n) => check("bad len " + n, throws(() => X.encrypt("a".repeat(n), new Uint8Array(32)))));
V.invalid.get_conversation_key.forEach((v, i) => check("bad ck " + i, throws(() => X.conversationKey(v.sec1, v.pub2))));
console.log(JSON.stringify({ passed, failed }));
