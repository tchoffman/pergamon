"""Lightweight web UI for Pergamon.

Run with:
    bin/pergamon web                 # :8090
    bin/pergamon web --port 9000     # custom port

Single-page UI (Jinja2 + HTMX, no SPA framework). SSE streams the RAG answer
token-by-token. Links out to kiwix-serve for ZIM browse.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import chromadb
import ollama
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from fastapi import HTTPException

from rag.config import REPO_ROOT, load_config
from rag.domains import guard
from rag.identity import (
    get_active,
    list_identities,
    load_identity,
    load_nsec,
    set_active,
)
from rag.nostr import (
    _load_relays_from_config,
    _publish_many,
    build_event,
    sign_event,
)
from rag.plan import PLAN_SYSTEM_PROMPT
from rag.query import SYSTEM_PROMPT, _format_source, build_context, retrieve

TEMPLATES_DIR = REPO_ROOT / "rag" / "templates"
STATIC_DIR = REPO_ROOT / "rag" / "static"

app = FastAPI(title="Pergamon", docs_url=None, redoc_url=None)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_cfg = load_config()


def _zim_list() -> list[dict]:
    """ZIMs available for browse (links go to the kiwix-serve process)."""
    zim_dir = REPO_ROOT / "build" / "corpus" / "zims"
    if not zim_dir.exists():
        return []
    out = []
    for z in sorted(zim_dir.glob("*.zim")):
        stem = z.stem
        out.append({"name": z.name, "stem": stem, "size_mb": z.stat().st_size // (1024 * 1024)})
    return out


def _status() -> dict:
    """Light status pulled from the current state."""
    cfg = load_config()
    try:
        c = chromadb.PersistentClient(path=cfg["chroma_dir"])
        chunks = c.get_or_create_collection(cfg["chroma_collection"]).count()
    except Exception:
        chunks = -1
    zims = _zim_list()
    return {
        "chunks": chunks,
        "zims": len(zims),
        "model": cfg["llm_model"],
        "embedding_model": cfg["embedding_model"],
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "status": _status(),
            "zims": _zim_list(),
            "kiwix_available": shutil.which("kiwix-serve") is not None
            or (REPO_ROOT / "build" / "bin" / "kiwix-serve").exists()
            or Path("/Applications/Kiwix.app").exists(),
        },
    )


@app.get("/status")
def status():
    return _status()


def _stream_rag(q: str, system: str, user_prefix: str):
    """Shared SSE generator for ask / plan. `user_prefix` labels the user slot
    (QUESTION or GOAL) so the LLM reads the prompt correctly."""
    cfg = load_config()
    hits = retrieve(cfg, q)

    guarded_system, domains = guard(q, system)

    async def event_gen():
        for d in domains:
            yield {"event": "notice", "data": d.banner}
        if hits:
            for i, h in enumerate(hits, 1):
                label = _format_source(h["meta"])
                yield {"event": "source", "data": f"[{i}] {label}"}
            context, _ = build_context(hits, cfg["max_context_chars"])
            user_prompt = f"CONTEXT:\n{context}\n\n{user_prefix}: {q}"
        else:
            yield {"event": "source", "data": "(no corpus — answering from general knowledge)"}
            user_prompt = f"{user_prefix}: {q}"

        client_ollama = ollama.Client(host=cfg["ollama_host"])
        stream = client_ollama.chat(
            model=cfg["llm_model"],
            messages=[
                {"role": "system", "content": guarded_system},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
            options={"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]},
        )
        for chunk in stream:
            piece = chunk["message"]["content"]
            if piece:
                yield {"event": "token", "data": piece}
            await asyncio.sleep(0)

        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_gen())


@app.get("/ask")
async def ask(q: str):
    return _stream_rag(q, SYSTEM_PROMPT, "QUESTION")


@app.get("/plan")
async def plan(q: str):
    return _stream_rag(q, PLAN_SYSTEM_PROMPT, "GOAL")


@app.get("/identity", response_class=HTMLResponse)
def identity_page(request: Request):
    """Simple HTML page for viewing + switching + editing identity."""
    idents = list_identities()
    active = get_active()
    return templates.TemplateResponse(
        request,
        "identity.html",
        {
            "identities": [i.meta() for i in idents],
            "active": active.meta() if active else None,
            "status": _status(),
            "kiwix_available": False,   # unused on this page
            "zims": [],
        },
    )


@app.get("/api/identity/active")
def api_identity_active():
    active = get_active()
    if not active:
        return {"active": None}
    return {"active": active.meta()}


@app.post("/api/identity/use")
async def api_identity_use(request: Request):
    body = await request.json()
    name = body.get("name")
    if not name:
        raise HTTPException(400, "name required")
    try:
        set_active(name)
    except FileNotFoundError:
        raise HTTPException(404, f"no identity: {name}")
    ident = load_identity(name)
    return {"active": ident.meta()}


@app.post("/api/identity/profile")
async def api_identity_profile(request: Request):
    """Publish a NIP-01 kind-0 metadata event for the active identity.

    Body (any subset): {display_name, about, picture, nip05, website, lud16}
    Clients subscribe to kind-0 to render profiles; this is how you update yours.
    """
    body = await request.json()
    ident = get_active()
    if not ident:
        raise HTTPException(400, "no active identity")

    allowed_keys = {"display_name", "name", "about", "picture", "nip05", "website", "lud16", "banner"}
    metadata = {k: v for k, v in body.items() if k in allowed_keys and v}
    if not metadata:
        raise HTTPException(400, "no valid profile fields in request")

    # nip05 default from the identity, if not supplied.
    if ident.nip05 and "nip05" not in metadata:
        metadata["nip05"] = ident.nip05
    if ident.display_name and "display_name" not in metadata and "name" not in metadata:
        metadata["display_name"] = ident.display_name

    import json as _json
    event = sign_event(
        build_event(kind=0, content=_json.dumps(metadata, separators=(",", ":")),
                    pubkey_hex=ident.hex_pub),
        load_nsec(ident.name),
    )
    relays = _load_relays_from_config()
    results = await _publish_many(event, relays)
    return {
        "event_id": event.id,
        "published_to": {u: {"ok": ok, "message": msg} for u, (ok, msg) in results.items()},
        "metadata": metadata,
    }


@app.get("/.well-known/nostr.json")
def nip05(name: str | None = None):
    """NIP-05 verifier.

    Nostr clients hit this endpoint when a user puts e.g. `trenton@pergamon.local`
    in their profile. We return the mapping from local name → hex pubkey so the
    client can verify ownership.

    Spec: https://github.com/nostr-protocol/nips/blob/master/05.md
    """
    names: dict[str, str] = {}
    for ident in list_identities():
        # Local-part of their NIP-05 if set, else their identity name.
        local = ident.nip05_name
        names[local] = ident.hex_pub
    if name:
        if name not in names:
            return {"names": {}}
        return {"names": {name: names[name]}}
    return {"names": names}


@app.get("/search")
def search_endpoint(q: str, k: int = 6):
    """Raw semantic hits — no LLM. Returns JSON for the UI."""
    cfg = load_config()
    cfg_k = dict(cfg)
    cfg_k["retrieve_k"] = k
    hits = retrieve(cfg_k, q)
    return {
        "hits": [
            {
                "label": _format_source(h["meta"]),
                "preview": h["text"].strip()[:600],
                "dist": h.get("dist"),
            }
            for h in hits
        ]
    }


# ─────────────────────────────── classifieds ───────────────────────────────
# Sync endpoints on purpose: FastAPI runs them in a worker thread, so the
# blocking relay helpers (which use asyncio.run) are safe to call.

from rag import classifieds as _cl  # noqa: E402


@app.get("/classifieds", response_class=HTMLResponse)
def classifieds_page(request: Request):
    active = get_active()
    return templates.TemplateResponse(
        request, "classifieds.html",
        {"active": active.meta() if active else None, "soft_cap": _cl.SOFT_CAP},
    )


@app.get("/api/classifieds")
def api_classifieds(q: str = "", side: str | None = None, tag: str | None = None,
                    all: bool = False, limit: int = 100):
    relays = _load_relays_from_config()
    events = _cl.fetch_listings(relays, tag=tag or None, limit=limit, live_only=not all)
    return {"listings": _cl.search_infos(events, query=q, side=side or None),
            "relays": relays}


@app.get("/api/classifieds/mine")
def api_classifieds_mine():
    ident = get_active()
    if not ident:
        raise HTTPException(400, "no active identity")
    rows = [_cl.summarize(_cl._event_from_json(r)) for r in _cl._load_mine().values()
            if r["pubkey"] == ident.hex_pub]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return {"listings": rows, "live": sum(r["is_live"] for r in rows),
            "soft_cap": _cl.SOFT_CAP}


def _num(v):
    return float(v) if v not in (None, "") else None


@app.post("/api/classifieds")
def api_classifieds_post(body: dict):
    ident = get_active()
    if not ident:
        raise HTTPException(400, "no active identity — create one on /identity")
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    side = body.get("side") or "offering"
    if side not in _cl.SIDES:
        raise HTTPException(400, f"side must be one of {_cl.SIDES}")
    accepts = body.get("accepts") or []
    if isinstance(accepts, str):
        accepts = [a.strip() for a in accepts.split(",") if a.strip()]
    tags = body.get("hashtags") or []
    if isinstance(tags, str):
        tags = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()]
    try:
        ev, results = _cl.post_listing(
            ident, load_nsec(ident.name), force=bool(body.get("force")),
            title=title, description=(body.get("description") or "").strip(),
            side=side, price_oz_ag=_num(body.get("price_oz_ag")),
            price_usd=_num(body.get("price_usd")), accepts=accepts or None,
            location=(body.get("location") or None), qty=(body.get("qty") or None),
            hashtags=tags, ttl_days=_num(body.get("ttl_days")) or _cl.DEFAULT_TTL_DAYS,
        )
    except _cl.SoftCapExceeded as e:
        raise HTTPException(409, str(e))
    return {"listing": _cl.summarize(ev),
            "published_to": {u: {"ok": ok, "message": m} for u, (ok, m) in results.items()}}


def _republish(d: str, status: str | None):
    ident = get_active()
    if not ident:
        raise HTTPException(400, "no active identity")
    try:
        ev = _cl.republish(d, load_nsec(ident.name), status=status)
    except KeyError:
        raise HTTPException(404, f"no local listing {d}")
    results = _cl.publish(ev, _load_relays_from_config())
    _cl._remember(ev)
    return {"listing": _cl.summarize(ev),
            "published_to": {u: {"ok": ok, "message": m} for u, (ok, m) in results.items()}}


@app.post("/api/classifieds/{d}/renew")
def api_classifieds_renew(d: str):
    return _republish(d, None)


@app.post("/api/classifieds/{d}/close")
def api_classifieds_close(d: str):
    return _republish(d, "sold")


# ─────────────────────────── community board ───────────────────────────

from rag import board as _bd  # noqa: E402


@app.get("/board", response_class=HTMLResponse)
def board_page(request: Request):
    active = get_active()
    return templates.TemplateResponse(
        request, "board.html",
        {"active": active.meta() if active else None, "categories": _bd.CATEGORIES},
    )


@app.get("/api/board")
def api_board(category: str | None = None, days: float = _bd.DEFAULT_DAYS):
    if category and category not in _bd.CATEGORIES:
        raise HTTPException(400, "unknown category")
    return {"posts": _bd.fetch_posts(_load_relays_from_config(), category or None, days)}


@app.post("/api/board")
def api_board_post(body: dict):
    ident = get_active()
    if not ident:
        raise HTTPException(400, "no active identity — create one on /identity")
    try:
        ev, results = _bd.post(ident, load_nsec(ident.name), body.get("text") or "",
                               body.get("category") or "notice",
                               (body.get("location") or "").strip() or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"post": _bd.summarize(ev),
            "published_to": {u: {"ok": ok, "message": m} for u, (ok, m) in results.items()}}


@app.get("/bulletin", response_class=HTMLResponse)
def bulletin(request: Request, days: float = 14):
    """Printable sheet for the physical board at a host site."""
    import time as _t
    relays = _load_relays_from_config()
    listings = _cl.search_infos(_cl.fetch_listings(relays, limit=200))
    posts = _bd.fetch_posts(relays, days=days)
    urls = _cards.lan_urls(request.url.port or 8090)
    join_url = urls[0] if urls else str(request.base_url)
    return templates.TemplateResponse(request, "bulletin.html", {
        "offers": [l for l in listings if l["side"] == "offering"],
        "wanted": [l for l in listings if l["side"] == "looking-for"],
        "posts": posts,
        "categories": _bd.CATEGORIES,
        "printed": _t.strftime("%A %B %-d, %Y %H:%M"),
        "host": get_active().meta() if get_active() else None,
        "join_url": join_url,
        "join_qr": _cards.qr_svg(join_url, scale=3),
    })



# ─────────────────────────── member cards ───────────────────────────

from rag import cards as _cards  # noqa: E402


@app.get("/identity/card", response_class=HTMLResponse)
def identity_card():
    ident = get_active()
    if not ident:
        raise HTTPException(400, "no active identity")
    return HTMLResponse(_cards.card_html(ident))
