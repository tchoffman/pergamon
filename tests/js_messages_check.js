// pergamon.js messaging under Node. Modes:
//   build <sender nsec> <recipient hex> <text>   → prints wraps JSON
//   open  <my nsec> <wrap JSON>                  → prints the opened message (or {error})
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
const P = ctx.Pergamon;
const [mode, nsec, a, b] = process.argv.slice(2);
P.Keys.importNsec(nsec, "");
if (mode === "build") console.log(JSON.stringify(P.Messages.build(a, b)));
else { try { console.log(JSON.stringify(P.Messages.open(JSON.parse(a)))); } catch (e) { console.log(JSON.stringify({ error: e.message })); } }
