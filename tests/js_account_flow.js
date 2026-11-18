// Drives Pergamon.Account in pergamon.js against a running hub (argv[2] = base
// URL), as two separate "phones". Prints a JSON object of results.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { webcrypto } = require("crypto");
const BASE = process.argv[2];

function phone() {
  const store = {};
  const ctx = {
    crypto: webcrypto, btoa, atob, TextDecoder,
    fetch: (url, opts) => fetch(BASE + url, opts),
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    Uint8Array, TextEncoder, JSON, Date, Math, Error, Array, Object, Number, String, encodeURIComponent,
  };
  ctx.globalThis = ctx;
  ctx.window = ctx;
  vm.createContext(ctx);
  const root = path.join(__dirname, "..", "rag", "static");
  vm.runInContext(fs.readFileSync(path.join(root, "vendor", "noble-nostr.js"), "utf8"), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, "pergamon.js"), "utf8"), ctx);
  return ctx.Pergamon;
}
const err = async (f) => { try { await f(); return null; } catch (e) { return e.message; } };

(async () => {
  const out = {};
  const a = phone(), b = phone(), c = phone();
  await a.Account.signUp("rosa", "pea soup 42", "Rosa");
  out.signup_npub = a.Keys.npub();
  out.signup_username = a.Keys.username();
  out.taken = await err(() => b.Account.signUp("rosa", "x", "Imposter"));

  // Same account on a second phone.
  out.wrong_password = await err(() => b.Account.signIn("rosa", "pea soup 43"));
  await b.Account.signIn("rosa", "pea soup 42");
  out.second_phone_same_npub = b.Keys.npub() === out.signup_npub;
  out.unknown_user = await err(() => b.Account.signIn("nobody", "x"));

  // Key file: unlock it on a phone that never talked to this account.
  const file = a.Account.keyFile();
  out.keyfile_prefix = file.slice(0, 10);
  out.keyfile_wrong = await err(() => c.Account.useKeyFile(file, "nope", "Rosa"));
  c.Account.useKeyFile(file, "pea soup 42", "Rosa");
  out.keyfile_same_npub = c.Keys.npub() === out.signup_npub;

  // Key kept only on the phone: the hub can't hand it out.
  const d = phone(), e = phone();
  await d.Account.signUp("ben", "carrot cake", "Ben", false);
  out.phone_only = await err(() => e.Account.signIn("ben", "carrot cake"));

  // Signing works after sign-in.
  out.signed = !!b.sign({ pubkey: b.Keys.pubkey(), created_at: 1, kind: 1, tags: [], content: "hi" }).sig;
  b.Account.signOut();
  out.signed_out = b.Keys.has() === false;
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ crash: e.message })); });
