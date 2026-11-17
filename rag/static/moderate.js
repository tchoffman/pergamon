/* Host-only "Hide" and "Block author" links on board posts, listings and skills.
 * The moderation API answers only for the hub's host, so guests never see them. */
(function (global) {
  "use strict";
  let hostCheck = null;
  function isHost() {
    if (!hostCheck) {
      hostCheck = fetch("/api/moderation", { cache: "no-store" }).then((r) => r.ok).catch(() => false);
    }
    return hostCheck;
  }
  async function post(action, body) {
    const r = await fetch("/api/moderation/" + action, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(((await r.json().catch(() => ({}))).detail) || r.statusText);
    return r.json();
  }
  /* item: {id, pubkey, kind?, d?, preview}. onBlock(pubkey) runs after a block,
   * so the page can drop that person's other cards too. */
  async function controls(card, item, onBlock) {
    if (!(await isHost())) return;
    const row = document.createElement("div");
    row.className = "mod-row small";
    const hide = document.createElement("button");
    hide.type = "button"; hide.className = "linklike"; hide.textContent = "Hide";
    hide.addEventListener("click", async () => {
      if (!confirm("Hide this from everyone on this hub? It also won't be passed on by sync. " +
                   "You can undo this on the Moderation page.")) return;
      try { await post("hide", item); card.remove(); } catch (e) { alert(e.message); }
    });
    const block = document.createElement("button");
    block.type = "button"; block.className = "linklike"; block.textContent = "Block author";
    block.addEventListener("click", async () => {
      if (!confirm("Block this person on this hub? Everything they've posted disappears here, " +
                   "and they can't post here again. Other hubs aren't affected.")) return;
      try { await post("block", { pubkey: item.pubkey, note: (item.preview || "").slice(0, 80) });
            if (onBlock) onBlock(item.pubkey); else card.remove(); } catch (e) { alert(e.message); }
    });
    row.append("Host: ", hide, " · ", block);
    card.appendChild(row);
  }
  global.PergamonModerate = { controls, isHost };
})(window);
