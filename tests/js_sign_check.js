// Loads the vendored bundle + pergamon.js in a Node vm with stub browser
// globals, creates a key, signs the event given on argv[2] (JSON with the
// unsigned fields; pubkey is filled in), and prints {npub, nsec, event}.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { webcrypto } = require("crypto");

const store = {};
const ctx = {
  crypto: webcrypto, btoa, atob, TextDecoder,
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

const P = ctx.Pergamon;
const mode = process.argv[3] || "create";
if (mode === "import") P.Keys.importNsec(process.argv[4], "imported");
else P.Keys.create("tester");
const unsigned = JSON.parse(process.argv[2]);
unsigned.pubkey = P.Keys.pubkey();
const ev = P.sign(unsigned);
let badImport = null;
try { P.Keys.importNsec(P.Keys.nsec().slice(0, -1) + "q", "x"); } catch (e) { badImport = e.message; }
console.log(JSON.stringify({ npub: P.Keys.npub(), nsec: P.Keys.nsec(), event: ev, badImport }));
