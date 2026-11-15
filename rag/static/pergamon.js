/* Browser-held Nostr keys for Pergamon.
 *
 * The private key is generated on this device and kept in localStorage. The
 * server builds unsigned events (all validation stays server-side); this file
 * computes the NIP-01 id, signs it with BIP-340 Schnorr, and sends the signed
 * event back to /api/publish, which verifies the signature before relaying.
 *
 * Requires /static/vendor/noble-nostr.js (window.nobleNostr).
 */
(function (global) {
  "use strict";
  const N = global.nobleNostr;
  const STORE = "pergamon.key.v1";

  // ── bech32 (BIP-173) — just enough for nsec / npub ──────────────────
  const CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";
  const GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  function polymod(values) {
    let chk = 1;
    for (const v of values) {
      const top = chk >>> 25;
      chk = ((chk & 0x1ffffff) << 5) ^ v;
      for (let i = 0; i < 5; i++) if ((top >>> i) & 1) chk ^= GEN[i];
    }
    return chk >>> 0;
  }
  const hrpExpand = (hrp) =>
    [...hrp].map((c) => c.charCodeAt(0) >> 5).concat([0], [...hrp].map((c) => c.charCodeAt(0) & 31));
  function convertBits(data, from, to, pad) {
    let acc = 0, bits = 0;
    const out = [], maxv = (1 << to) - 1;
    for (const v of data) {
      acc = (acc << from) | v;
      bits += from;
      while (bits >= to) { bits -= to; out.push((acc >> bits) & maxv); }
    }
    if (pad && bits > 0) out.push((acc << (to - bits)) & maxv);
    else if (!pad && (bits >= from || ((acc << (to - bits)) & maxv))) throw new Error("bad padding");
    return out;
  }
  function bech32Encode(hrp, bytes) {
    const data = convertBits(bytes, 8, 5, true);
    const mod = polymod(hrpExpand(hrp).concat(data, [0, 0, 0, 0, 0, 0])) ^ 1;
    const checksum = [0, 1, 2, 3, 4, 5].map((i) => (mod >> (5 * (5 - i))) & 31);
    return hrp + "1" + data.concat(checksum).map((d) => CHARSET[d]).join("");
  }
  function bech32Decode(str, expectHrp) {
    str = str.trim().toLowerCase();
    const pos = str.lastIndexOf("1");
    const hrp = str.slice(0, pos);
    if (hrp !== expectHrp) throw new Error("expected a " + expectHrp + " key");
    const data = [...str.slice(pos + 1)].map((c) => {
      const d = CHARSET.indexOf(c);
      if (d < 0) throw new Error("invalid character in key");
      return d;
    });
    if (polymod(hrpExpand(hrp).concat(data)) !== 1) throw new Error("key checksum failed — check for typos");
    return Uint8Array.from(convertBits(data.slice(0, -6), 5, 8, false));
  }

  // ── key storage ─────────────────────────────────────────────────────
  function load() {
    try {
      const raw = global.localStorage.getItem(STORE);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }
  function save(secHex, name) {
    const pub = N.bytesToHex(N.schnorr.getPublicKey(N.hexToBytes(secHex)));
    const rec = { sec: secHex, pub: pub, name: name || "", created: Date.now() };
    global.localStorage.setItem(STORE, JSON.stringify(rec));
    return rec;
  }

  const Keys = {
    get: load,
    has: () => !!load(),
    pubkey: () => (load() || {}).pub || null,
    npub: () => { const k = load(); return k ? bech32Encode("npub", N.hexToBytes(k.pub)) : null; },
    nsec: () => { const k = load(); return k ? bech32Encode("nsec", N.hexToBytes(k.sec)) : null; },
    create(name) {
      const sec = new Uint8Array(32);
      global.crypto.getRandomValues(sec);
      return save(N.bytesToHex(sec), name);
    },
    importNsec(nsec, name) {
      const bytes = bech32Decode(nsec, "nsec");
      if (bytes.length !== 32) throw new Error("not a 32-byte key");
      return save(N.bytesToHex(bytes), name);
    },
    forget() { global.localStorage.removeItem(STORE); },
  };

  // ── signing ─────────────────────────────────────────────────────────
  function eventId(ev) {
    const serial = JSON.stringify([0, ev.pubkey, ev.created_at, ev.kind, ev.tags, ev.content]);
    return N.bytesToHex(N.sha256(N.utf8ToBytes(serial)));
  }

  function sign(unsigned) {
    const k = load();
    if (!k) throw new Error("no key on this device");
    if (unsigned.pubkey !== k.pub) throw new Error("event is not for this device's key");
    const ev = {
      pubkey: unsigned.pubkey, created_at: unsigned.created_at, kind: unsigned.kind,
      tags: unsigned.tags, content: unsigned.content,
    };
    ev.id = eventId(ev);
    const aux = new Uint8Array(32);
    global.crypto.getRandomValues(aux);
    ev.sig = N.bytesToHex(N.schnorr.sign(ev.id, k.sec, aux));
    return ev;
  }

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* non-JSON error */ }
    if (!r.ok) {
      const err = new Error(data.detail || r.statusText);
      err.status = r.status;
      throw err;
    }
    return data;
  }

  /* Ask the server to build an event for this device's key, sign it here,
   * and publish. Returns {draft: <extra fields from the draft endpoint>,
   * published_to, event}. */
  async function signAndPublish(draftUrl, body) {
    const draft = await postJSON(draftUrl, Object.assign({}, body, { pubkey: Keys.pubkey() }));
    const signed = sign(draft.event);
    const res = await postJSON("/api/publish", { event: signed });
    return Object.assign({ draft: draft, event: signed }, res);
  }

  global.Pergamon = { Keys, sign, eventId, signAndPublish, postJSON, bech32Encode, bech32Decode };
})(typeof window !== "undefined" ? window : globalThis);
