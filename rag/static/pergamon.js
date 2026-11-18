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
    return signWith(k.sec, unsigned);
  }

  function signWith(secHex, unsigned) {
    const ev = {
      pubkey: unsigned.pubkey, created_at: unsigned.created_at, kind: unsigned.kind,
      tags: unsigned.tags, content: unsigned.content,
    };
    ev.id = eventId(ev);
    const aux = new Uint8Array(32);
    global.crypto.getRandomValues(aux);
    ev.sig = N.bytesToHex(N.schnorr.sign(ev.id, secHex, aux));
    return ev;
  }

  function verifyEvent(ev) {
    try {
      return eventId(ev) === ev.id && N.schnorr.verify(ev.sig, ev.id, ev.pubkey);
    } catch (e) { return false; }
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

  // ── NIP-44 v2: encryption inside private messages ─────────────────────
  // Spec: https://github.com/nostr-protocol/nips/blob/master/44.md
  const NIP44 = (function () {
    const enc = new TextEncoder(), dec = new TextDecoder("utf-8", { fatal: true });
    const b64 = (u8) => { let s = ""; for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]); return btoa(s); };
    const unb64 = (str) => { const s = atob(str); const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u; };
    function conversationKey(secHex, pubHex) {
      const shared = N.secp256k1.getSharedSecret(secHex, "02" + pubHex);
      return N.hkdfExtract(N.sha256, shared.subarray(1, 33), enc.encode("nip44-v2"));
    }
    function messageKeys(ck, nonce) {
      const okm = N.hkdfExpand(N.sha256, ck, nonce, 76);
      return { key: okm.subarray(0, 32), nonce: okm.subarray(32, 44), hmac: okm.subarray(44, 76) };
    }
    function paddedLen(n) {
      if (n <= 32) return 32;
      const next = 1 << (Math.floor(Math.log2(n - 1)) + 1);
      const chunk = next <= 256 ? 32 : next / 8;
      return chunk * (Math.floor((n - 1) / chunk) + 1);
    }
    function pad(text) {
      const raw = enc.encode(text);
      if (raw.length < 1 || raw.length > 65535) throw new Error("message must be 1-65535 bytes");
      const out = new Uint8Array(2 + paddedLen(raw.length));
      out[0] = raw.length >> 8; out[1] = raw.length & 255; out.set(raw, 2);
      return out;
    }
    function unpad(p) {
      const n = (p[0] << 8) | p[1];
      if (n < 1 || p.length !== 2 + paddedLen(n)) throw new Error("invalid padding");
      return dec.decode(p.subarray(2, 2 + n));
    }
    function encrypt(text, ck, nonce) {
      nonce = nonce || N.randomBytes(32);
      const k = messageKeys(ck, nonce);
      const ct = N.chacha20(k.key, k.nonce, pad(text));
      const mac = N.hmac(N.sha256, k.hmac, N.concatBytes(nonce, ct));
      return b64(N.concatBytes(Uint8Array.of(2), nonce, ct, mac));
    }
    function decrypt(payload, ck) {
      if (!payload || payload[0] === "#") throw new Error("unknown encryption version");
      if (payload.length < 132 || payload.length > 87472) throw new Error("invalid payload length");
      let d;
      try { d = unb64(payload); } catch (e) { throw new Error("invalid base64"); }
      if (d.length < 99 || d.length > 65603 || d[0] !== 2) throw new Error("invalid payload");
      const nonce = d.subarray(1, 33), ct = d.subarray(33, d.length - 32), mac = d.subarray(d.length - 32);
      const k = messageKeys(ck, nonce);
      const want = N.hmac(N.sha256, k.hmac, N.concatBytes(nonce, ct));
      let diff = 0;
      for (let i = 0; i < 32; i++) diff |= want[i] ^ mac[i];
      if (diff) throw new Error("invalid MAC");
      return unpad(N.chacha20(k.key, k.nonce, ct));
    }
    return { conversationKey, messageKeys, paddedLen, encrypt, decrypt };
  })();

  // ── NIP-17 private messages, gift-wrapped (NIP-59) ─────────────────────
  // rumor (kind 14, unsigned) → seal (kind 13, signed by the sender, encrypted
  // to the recipient) → gift wrap (kind 1059, signed by a throwaway key, only a
  // "p" tag for the recipient). Hubs, relays and sync sticks see who a wrap is
  // for, never who sent it or what it says. Times are blurred up to 2 days.
  const Messages = (function () {
    const now = () => Math.floor(Date.now() / 1000);
    const blurred = () => now() - Math.floor(Math.random() * 2 * 86400);
    function wrapFor(rumor, senderSec, recipient) {
      const seal = signWith(senderSec, {
        pubkey: rumor.pubkey, created_at: blurred(), kind: 13, tags: [],
        content: NIP44.encrypt(JSON.stringify(rumor), NIP44.conversationKey(senderSec, recipient)) });
      const eph = N.bytesToHex(N.randomBytes(32));
      return signWith(eph, {
        pubkey: N.bytesToHex(N.schnorr.getPublicKey(eph)), created_at: blurred(), kind: 1059,
        tags: [["p", recipient]],
        content: NIP44.encrypt(JSON.stringify(seal), NIP44.conversationKey(eph, recipient)) });
    }
    /* Gift wraps for the recipient and for yourself (so your other devices
     * show what you sent). */
    function build(recipient, text) {
      const k = load();
      if (!k) throw new Error("sign in to send messages");
      if (!/^[0-9a-f]{64}$/.test(recipient)) throw new Error("bad recipient");
      const rumor = { pubkey: k.pub, created_at: now(), kind: 14, tags: [["p", recipient]], content: text };
      rumor.id = eventId(rumor);
      const wraps = [wrapFor(rumor, k.sec, recipient)];
      if (recipient !== k.pub) wraps.push(wrapFor(rumor, k.sec, k.pub));
      return wraps;
    }
    /* Open a gift wrap addressed to this device's key. Throws if anything is
     * off, including a seal whose signer isn't the rumor's author (forgery). */
    function open(wrap) {
      const k = load();
      if (!k) throw new Error("no key on this device");
      if (wrap.kind !== 1059 || !verifyEvent(wrap)) throw new Error("not a valid gift wrap");
      const seal = JSON.parse(NIP44.decrypt(wrap.content, NIP44.conversationKey(k.sec, wrap.pubkey)));
      if (seal.kind !== 13 || !verifyEvent(seal)) throw new Error("seal signature doesn't verify");
      const rumor = JSON.parse(NIP44.decrypt(seal.content, NIP44.conversationKey(k.sec, seal.pubkey)));
      if (rumor.pubkey !== seal.pubkey) throw new Error("sender mismatch: forged message");
      if (rumor.kind !== 14 || eventId(rumor) !== rumor.id) throw new Error("not a chat message");
      const to = rumor.tags.filter((t) => t[0] === "p").map((t) => t[1]);
      return { id: rumor.id, from: rumor.pubkey, to: to, text: rumor.content, created_at: rumor.created_at,
               mine: rumor.pubkey === k.pub };
    }
    async function send(recipient, text) {
      for (const w of build(recipient, text)) await postJSON("/api/publish", { event: w });
    }
    /* Everything addressed to this key that the hub has. The hub asks for a
     * fresh signed note first, so no one else can list your wraps. */
    async function inbox() {
      const k = load();
      if (!k) return [];
      const proof = sign({ pubkey: k.pub, created_at: now(), kind: 27235,
                           tags: [["purpose", "inbox"]], content: "pergamon inbox" });
      const res = await postJSON("/api/messages/inbox", { proof: proof });
      const seen = {}, out = [];
      for (const w of res.wraps) {
        try {
          const m = open(w);
          if (!seen[m.id]) { seen[m.id] = true; out.push(m); }
        } catch (e) { /* not for us, damaged, or forged: skip */ }
      }
      return out.sort((a, b) => a.created_at - b.created_at);
    }
    return { build, open, send, inbox };
  })();

  global.Pergamon = { Keys, Account, NIP49, NIP44, Messages, verifyEvent, signWith, sign, eventId, signAndPublish, postJSON,
                      bech32Encode, bech32Decode };
})(typeof window !== "undefined" ? window : globalThis);
