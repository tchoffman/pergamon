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
  function save(secHex, name, extra) {
    const pub = N.bytesToHex(N.schnorr.getPublicKey(N.hexToBytes(secHex)));
    const rec = Object.assign({ sec: secHex, pub: pub, name: name || "", created: Date.now() }, extra || {});
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
    /* The signed-in account's username, if this key came from an account. */
    username: () => (load() || {}).username || null,
    /* Set the key from raw bytes (after unlocking a password-locked key). */
    setSecret(secBytes, name, extra) { return save(N.bytesToHex(secBytes), name, extra); },
  };

  // ── NIP-49: a private key locked with a password ("ncryptsec1...") ────
  // scrypt(password) → key; XChaCha20-Poly1305 encrypts the 32-byte secret.
  // Pure JavaScript, so it works on plain-HTTP pages where the browser's own
  // crypto is unavailable. Spec: https://github.com/nostr-protocol/nips/blob/master/49.md
  const NIP49_LOG_N = 16;   // 64 MiB of memory, ~1 s on a phone: slow for guessers
  function normPassword(pw) { return N.utf8ToBytes(String(pw).normalize("NFKC")); }
  function scryptKey(password, salt, logN) {
    return N.scrypt(normPassword(password), salt, { N: 2 ** logN, r: 8, p: 1, dkLen: 32 });
  }
  /* Lock `secBytes` with `password`. Returns {ncryptsec, key, salt, logN}; `key`
   * (the scrypt output) also derives the account sign-in token. */
  function lockKey(secBytes, password, opts) {
    const logN = (opts && opts.logN) || NIP49_LOG_N;
    const salt = (opts && opts.salt) || N.randomBytes(16);
    const nonce = N.randomBytes(24);
    const key = scryptKey(password, salt, logN);
    const ksb = Uint8Array.of(0x01);  // key security: never known to be handled insecurely
    const ct = N.xchacha20poly1305(key, nonce, ksb).encrypt(secBytes);
    const payload = new Uint8Array(1 + 1 + 16 + 24 + 1 + ct.length);
    payload.set([0x02, logN], 0); payload.set(salt, 2); payload.set(nonce, 18);
    payload.set(ksb, 42); payload.set(ct, 43);
    return { ncryptsec: bech32Encode("ncryptsec", payload), key: key, salt: salt, logN: logN };
  }
  function parseNcryptsec(ncryptsec) {
    const b = bech32Decode(ncryptsec, "ncryptsec");
    if (b.length !== 91 || b[0] !== 0x02) throw new Error("not a version-2 locked key");
    return { logN: b[1], salt: b.slice(2, 18), nonce: b.slice(18, 42), ksb: b.slice(42, 43), ct: b.slice(43) };
  }
  /* Unlock with a password, or with an already-derived scrypt `key`. */
  function unlockKey(ncryptsec, password, key) {
    const p = parseNcryptsec(ncryptsec);
    const k = key || scryptKey(password, p.salt, p.logN);
    try {
      return N.xchacha20poly1305(k, p.nonce, p.ksb).decrypt(p.ct);
    } catch (e) {
      throw new Error("wrong password");
    }
  }
  /* What the hub checks at sign-in: a one-way hash of the scrypt key, so the
   * password never leaves this device and the token can't unlock anything. */
  function authToken(key) {
    const tag = N.utf8ToBytes("pergamon-account-auth-v1");
    const both = new Uint8Array(tag.length + key.length);
    both.set(tag); both.set(key, tag.length);
    return N.bytesToHex(N.sha256(both));
  }
  const NIP49 = { lockKey, unlockKey, parseNcryptsec, authToken, scryptKey };

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

  // ── accounts: username + password, key underneath ───────────────────
  // The hub stores the password-locked key; the password never leaves here.
  const Account = {
    /* Create an account for the key on this device (making one if needed).
     * keepOnHub=false stores no key on the hub: sign-in elsewhere then needs
     * the downloaded key file. */
    async signUp(username, password, name, keepOnHub) {
      if (!Keys.has()) Keys.create(name);
      const k = Keys.get();
      const locked = lockKey(N.hexToBytes(k.sec), password);
      const proof = sign({ pubkey: k.pub, created_at: Math.floor(Date.now() / 1000), kind: 27235,
                           tags: [["username", username]], content: "pergamon account sign-up" });
      const res = await postJSON("/api/account/signup", {
        username: username, name: name || k.name || username,
        ncryptsec: keepOnHub === false ? null : locked.ncryptsec,
        salt: N.bytesToHex(locked.salt), log_n: locked.logN,
        auth: authToken(locked.key), proof: proof });
      save(k.sec, name || k.name, { username: res.username, ncryptsec: locked.ncryptsec,
                                   keyOnHub: res.key_on_hub });
      return res;
    },
    async signIn(username, password) {
      const c = await (await fetch("/api/account/salt?username=" + encodeURIComponent(username))).json();
      if (!c.salt) throw new Error(c.detail || "No account by that name on this hub.");
      const key = scryptKey(password, N.hexToBytes(c.salt), c.log_n);
      const res = await postJSON("/api/account/signin", { username: username, auth: authToken(key) });
      if (!res.ncryptsec) {
        const err = new Error("This account keeps its key only on its owner's phone. " +
                              "Use your downloaded key file to sign in here.");
        err.keyOnPhone = true;
        throw err;
      }
      const sec = unlockKey(res.ncryptsec, null, key);
      const rec = save(N.bytesToHex(sec), res.name || username, { username: res.username, ncryptsec: res.ncryptsec, keyOnHub: true });
      if (rec.pub !== res.pubkey) { Keys.forget(); throw new Error("the key didn't match this account"); }
      return rec;
    },
    /* Unlock a downloaded key file (ncryptsec) with its password, on any hub. */
    useKeyFile(ncryptsec, password, name) {
      const sec = unlockKey(ncryptsec, password);
      return save(N.bytesToHex(sec), name, { ncryptsec: ncryptsec.trim() });
    },
    /* The password-locked key to download; locks it now if needed. */
    keyFile(password) {
      const k = Keys.get();
      if (!k) throw new Error("no key on this device");
      if (k.ncryptsec) return k.ncryptsec;
      if (!password) throw new Error("choose a password to lock the file with");
      const locked = lockKey(N.hexToBytes(k.sec), password);
      save(k.sec, k.name, { username: k.username, ncryptsec: locked.ncryptsec, keyOnHub: k.keyOnHub });
      return locked.ncryptsec;
    },
    signOut() { Keys.forget(); },
  };

  global.Pergamon = { Keys, Account, NIP49, sign, eventId, signAndPublish, postJSON,
                      bech32Encode, bech32Decode };
})(typeof window !== "undefined" ? window : globalThis);
