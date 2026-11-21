"""Lightweight web UI for Pergamon.

Run with:
    bin/pergamon web                 # :8090
    bin/pergamon web --port 9000     # custom port

Single-page UI (Jinja2 + HTMX, no SPA framework). SSE streams the RAG answer
token-by-token. Links out to kiwix-serve for ZIM browse.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

import chromadb
import ollama
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from fastapi import HTTPException

from rag import situation as _situation
from rag.storage import chroma_client
from rag.config import REPO_ROOT, load_config
from rag.domains import frame_question, guard
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
    afetch_events as _nostr_afetch,
    build_event,
    sign_event,
)
from rag.plan import PLAN_SYSTEM_PROMPT
from rag.query import (CONTEXT_HEADER, SYSTEM_PROMPT, _format_source, build_context,
                       relevant_hits, retrieve, system_prompt)

TEMPLATES_DIR = REPO_ROOT / "rag" / "templates"
STATIC_DIR = REPO_ROOT / "rag" / "static"

app = FastAPI(title="Pergamon", docs_url=None, redoc_url=None)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_cfg = load_config()


def _zim_list() -> list[dict]:
    """ZIMs available for browse (links go to the kiwix-serve process)."""
    from rag.storage import data_dir
    zim_dir = data_dir(check=False) / "corpus" / "zims"
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
        c = chroma_client(cfg)
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


# ─────────────────────────────── app shell ───────────────────────────────
# One home screen of apps; every page shares templates/_base.html.

APPS = [
    # (name, path, icon, who sees it: None = everyone, "household", "host")
    ("Ask", "/chat", "💬", None),
    ("Messages", "/messages", "✉️", None),
    ("Library", "/library", "📚", None),
    ("Guide", "/guide", "🧭", None),
    ("Maps", "/maps", "🗺️", None),
    ("Board", "/board", "📌", None),
    ("Classifieds", "/classifieds", "🏷️", None),
    ("Skills", "/skills", "🛠️", None),
    ("Trades", "/receipts", "🤝", None),
    ("Household", "/food", "🏠", "household"),
    ("Me", "/identity", "👤", None),
    ("Bulletin", "/bulletin", "🖨️", None),
    ("Forms", "/forms", "📝", None),
    ("Moderation", "/moderation", "🛡️", "host"),
    ("Hubs", "/hubs", "🔗", "host"),
    ("Radios", "/radios", "📻", "host"),
]


def _apps(request: Request) -> list[dict]:
    host = _is_host(request)
    visible = {None: True, "host": host,
               "household": host or ((_local_cfg().get("food") or {}).get("lan_access")) is True}
    return [{"name": n, "href": h, "icon": i} for n, h, i, who in APPS if visible[who]]


def _home(request: Request, welcome: bool):
    https_on, _ = _https_settings()
    return templates.TemplateResponse(request, "home.html", {
        "apps": _apps(request),
        "welcome": welcome,
        # phones need the one-time setup; the hub's own browser doesn't
        "offer_https": (https_on and request.url.scheme != "https"
                        and request.url.hostname not in ("localhost", "127.0.0.1", "::1")),
        "situation": _situation.load(),
        "situation_presets": _situation.PRESETS,
        "is_host": _is_host(request),
    })


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _home(request, welcome=False)


@app.get("/chat", response_class=HTMLResponse)
def ask_page(request: Request):
    return templates.TemplateResponse(request, "ask.html", {
        "status": _status(),
        "situation": _situation.load(),
        "situation_presets": _situation.PRESETS,
        "is_host": _is_host(request),
    })


@app.get("/library", response_class=HTMLResponse)
def library_page(request: Request):
    return templates.TemplateResponse(request, "library.html", {
        "zims": _zim_list(),
        "kiwix_url": f"http://{request.url.hostname or 'localhost'}:8080",
        "kiwix_available": shutil.which("kiwix-serve") is not None
        or (REPO_ROOT / "build" / "bin" / "kiwix-serve").exists()
        or Path("/Applications/Kiwix.app").exists(),
    })


@app.get("/offline", response_class=HTMLResponse)
def offline_page(request: Request):
    return templates.TemplateResponse(request, "offline.html", {})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    from fastapi.responses import JSONResponse
    icons = [{"src": f"/static/icons/icon-{s}.png", "sizes": f"{s}x{s}", "type": "image/png",
              "purpose": "any"} for s in (192, 512)]
    icons.append({"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512",
                  "type": "image/png", "purpose": "maskable"})
    return JSONResponse({
        "name": "Pergamon", "short_name": "Pergamon", "id": "/", "start_url": "/",
        "scope": "/", "display": "standalone", "background_color": "#fafaf7",
        "theme_color": "#8b4513", "description": "Your community's apps, running nearby.",
        "icons": icons,
    }, media_type="application/manifest+json")


def _sw_source() -> str:
    """The service worker, stamped with a version that changes with the static
    files so phones pick up new styles and scripts."""
    import hashlib
    files = sorted(p for p in STATIC_DIR.rglob("*") if p.is_file() and p.name != "sw.js")
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(STATIC_DIR).as_posix().encode())
        h.update(p.read_bytes())
    precache = ["/offline"] + [f"/static/{p.relative_to(STATIC_DIR).as_posix()}" for p in files]
    return ((STATIC_DIR / "sw.js").read_text()
            .replace("__VERSION__", h.hexdigest()[:12])
            .replace("__PRECACHE__", json.dumps(precache)))


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    from fastapi.responses import Response
    return Response(_sw_source(), media_type="text/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.get("/api/situation")
def api_situation_get():
    return _situation.load()


@app.post("/api/situation")
def api_situation_set(request: Request, body: dict):
    """Host only: it changes how every answer on this hub is framed."""
    if not _is_host(request):
        raise HTTPException(403, "Only the host can change the hub's current situation.")
    return _situation.save(str((body or {}).get("text") or ""))


@app.get("/status")
def status():
    return _status()


MAX_QUESTION_CHARS = 2000


class ModelGate:
    """Keeps one LAN device from monopolizing the single local model.

    Each device (by IP) gets one answer in flight at a time and, unless it is
    the host, a capped number of questions per hour. Over the limit, the
    request gets a 429 with a plain explanation instead of a model call.
    """

    def __init__(self, per_hour: int = 30, window_s: float = 3600):
        self.per_hour, self.window_s = per_hour, window_s
        self.in_flight: set[str] = set()
        self.recent: dict[str, list[float]] = {}

    def acquire(self, client: str, is_host: bool, now: float | None = None) -> None:
        now = now or time.time()
        if client in self.in_flight:
            raise HTTPException(429, "Still answering your last question; wait for it to finish.")
        if not is_host:
            times = [t for t in self.recent.get(client, []) if now - t < self.window_s]
            if len(times) >= self.per_hour:
                raise HTTPException(429, f"That's {self.per_hour} questions this hour from this device; "
                                         "the library is shared, so please wait a bit.")
            self.recent[client] = times + [now]
        self.in_flight.add(client)

    def release(self, client: str) -> None:
        self.in_flight.discard(client)


_gate = ModelGate()


def _admit(request: Request, q: str):
    """(client, None) if the question may run, else (client, an SSE refusal)."""
    client = request.client.host if request.client else "?"
    try:
        if not q:
            raise HTTPException(400, "Type a question first.")
        if len(q) > MAX_QUESTION_CHARS:
            raise HTTPException(413, f"That's too long (max {MAX_QUESTION_CHARS} characters).")
        _gate.acquire(client, _is_host(request))
    except HTTPException as e:
        # EventSource can't read an error response's body, so explain in-stream.
        reason = str(e.detail)

        async def refused():
            yield {"event": "failed", "data": reason}
        return client, EventSourceResponse(refused())
    return client, None


def _stream_agent(request: Request, q: str, sid: str = ""):
    """Tool-calling answer over SSE: notices, tool use, sources, tokens.

    `sid` names this browser tab's conversation, so follow-ups ("a bread
    oven") are read with the question before. Keyed by client too, so one
    device can't read another's conversation by guessing its id.
    """
    from rag import agent
    from rag.conversations import store as conversations
    q = (q or "").strip()
    client, refusal = _admit(request, q)
    if refusal is not None:
        return refusal
    cfg = load_config()
    is_host = _is_host(request)
    key = f"web:{client}:{sid[:64]}" if sid else None
    history = conversations.history(key) if key else []

    async def event_gen():
        answer_text: list[str] = []
        ok = False
        try:
            async for kind, data in agent.astream(q, cfg, is_host=is_host, history=history):
                if kind == "token":
                    answer_text.append(data)
                yield {"event": kind, "data": data}
            ok = True
        except Exception as e:
            print(f"[pergamon] answer failed: {e!r}", file=sys.stderr)
        finally:
            # Runs before the page hears "done", and also when the page cut the
            # answer short for a new question: remember what was said (a partial
            # answer is still context) and free the device for its follow-up.
            if key and answer_text:
                conversations.add(key, q, "".join(answer_text))
            _gate.release(client)
        if ok:
            yield {"event": "done", "data": ""}
        else:
            yield {"event": "failed", "data": "Sorry, the library couldn't answer just now."}

    return EventSourceResponse(event_gen())


def _stream_rag(request: Request, q: str, system: str, user_prefix: str,
                use_domain_guards: bool = True):
    """Shared SSE generator for ask / plan / cook. `user_prefix` labels the user
    slot (QUESTION, GOAL, PANTRY) so the LLM reads the prompt correctly.

    Everything slow (embedding, passage check, generation) runs off the event
    loop, so one answer in progress doesn't freeze every other page.
    """
    q = (q or "").strip()
    client, refusal = _admit(request, q)
    if refusal is not None:
        return refusal

    cfg = load_config()
    system = system_prompt(cfg, system)
    guarded_system, domains = guard(q, system) if use_domain_guards else (system, [])
    framed = frame_question(q, domains)

    async def event_gen():
        try:
            for d in domains:
                yield {"event": "notice", "data": d.banner}
            hits = await asyncio.to_thread(relevant_hits, cfg, q)
            if hits:
                for i, h in enumerate(hits, 1):
                    label = _format_source(h["meta"])
                    yield {"event": "source", "data": f"[{i}] {label}"}
                context, _ = build_context(hits, cfg["max_context_chars"])
                user_prompt = f"{CONTEXT_HEADER}\n{context}\n\n{user_prefix}: {framed}"
            else:
                yield {"event": "source", "data": "(no matching library passages — answered from the model's own knowledge)"}
                user_prompt = f"{user_prefix}: {framed}"

            stream = await ollama.AsyncClient(host=cfg["ollama_host"]).chat(
                model=cfg["llm_model"],
                messages=[
                    {"role": "system", "content": guarded_system},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True,
                think=cfg.get("think", False),
                options={"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]},
            )
            async for chunk in stream:
                piece = chunk["message"]["content"]
                if piece:
                    yield {"event": "token", "data": piece}
            ok = True
        except Exception as e:
            print(f"[pergamon] answer failed: {e!r}", file=sys.stderr)
            ok = False
        finally:
            _gate.release(client)  # before "done", so an immediate next question is admitted
        if ok:
            yield {"event": "done", "data": ""}
        else:
            yield {"event": "failed", "data": "Sorry, the library couldn't answer just now."}

    return EventSourceResponse(event_gen())


@app.get("/ask")
async def ask(request: Request, q: str, sid: str = ""):
    if load_config().get("answer_mode") == "agent":
        return _stream_agent(request, q, sid)
    return _stream_rag(request, q, SYSTEM_PROMPT, "QUESTION")


@app.get("/plan")
async def plan(request: Request, q: str):
    return _stream_rag(request, q, PLAN_SYSTEM_PROMPT, "GOAL")


@app.get("/identity", response_class=HTMLResponse)
def identity_page(request: Request):
    """Simple HTML page for viewing + switching + editing identity."""
    idents = list_identities()
    active = _active_for(request)
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
    _host_only(request)
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
    _host_only(request)
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


def _is_hex_pub(v: str | None) -> bool:
    return bool(v) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


_LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "testclient")


def _local_cfg() -> dict:
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    return load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}


def _is_host(request: Request) -> bool:
    """True for requests from this machine, or from anywhere when the owner set
    hub: {trust_lan: true} (a home network where everyone is household)."""
    host = request.client.host if request.client else ""
    if host in _LOCAL_HOSTS:
        return True
    return ((_local_cfg().get("hub") or {}).get("trust_lan")) is True


def _host_only(request: Request) -> None:
    if not _is_host(request):
        raise HTTPException(403, "This action signs with the hub's own identity, so it only works "
                                 "on the hub machine. On your phone, create your own key on "
                                 "/welcome and post with it.")


def _active_for(request: Request):
    """The machine's identity, but only for the host — guests never post as it."""
    return get_active() if _is_host(request) else None


def _with_authors(rows: list[dict], relays: list[str] | None = None) -> list[dict]:
    """Attach a human 'author' name (from kind-0 profiles) to each row."""
    from rag import profiles as _pf
    names = _pf.fetch_names(relays or _load_relays_from_config(), [r["pubkey"] for r in rows])
    for r in rows:
        r["author"] = _pf.label(names, r["pubkey"])
        if r.get("d"):
            r["ref"] = r["d"][:6]
    return rows


def _whose(pubkey: str | None, request: Request | None = None) -> str:
    """The pubkey a request acts for: a browser-held key if given, else (for
    the host only) the machine's active identity."""
    if pubkey:
        if not _is_hex_pub(pubkey):
            raise HTTPException(400, "pubkey must be 64 lowercase hex characters")
        return pubkey
    ident = _active_for(request) if request is not None else get_active()
    if not ident:
        raise HTTPException(400, "no identity — create a key on this device or on /identity")
    return ident.hex_pub


# ─────────────────────────────── classifieds ───────────────────────────────
# Sync endpoints on purpose: FastAPI runs them in a worker thread, so the
# blocking relay helpers (which use asyncio.run) are safe to call.

from rag import classifieds as _cl  # noqa: E402


@app.get("/classifieds", response_class=HTMLResponse)
def classifieds_page(request: Request):
    active = _active_for(request)
    return templates.TemplateResponse(
        request, "classifieds.html",
        {"active": active.meta() if active else None, "soft_cap": _cl.SOFT_CAP},
    )


@app.get("/api/classifieds")
def api_classifieds(q: str = "", side: str | None = None, tag: str | None = None,
                    all: bool = False, limit: int = 100):
    relays = _load_relays_from_config()
    events = _cl.fetch_listings(relays, tag=tag or None, limit=limit, live_only=not all)
    return {"listings": _with_authors(_cl.search_infos(events, query=q, side=side or None), relays),
            "relays": relays}


@app.get("/api/classifieds/mine")
def api_classifieds_mine(request: Request, pubkey: str | None = None):
    me = _whose(pubkey, request)
    rows = [_cl.summarize(_cl._event_from_json(r)) for r in _cl._load_mine().values()
            if r["pubkey"] == me]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return {"listings": rows, "live": sum(r["is_live"] for r in rows),
            "soft_cap": _cl.SOFT_CAP}


@app.get("/api/classifieds/matches")
def api_classifieds_matches(request: Request, pubkey: str | None = None):
    mine = _cl.my_live_listings(_whose(pubkey, request))
    others = _cl.search_infos(_cl.fetch_listings(_load_relays_from_config(), limit=500)) if mine else []
    return {"matches": _cl.find_matches(mine, others)}


def _num(v):
    return float(v) if v not in (None, "") else None


@app.post("/api/classifieds")
def api_classifieds_post(request: Request, body: dict):
    _host_only(request)
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
def api_classifieds_renew(request: Request, d: str):
    _host_only(request)
    return _republish(d, None)


@app.post("/api/classifieds/{d}/close")
def api_classifieds_close(request: Request, d: str):
    _host_only(request)
    return _republish(d, "sold")


# ─────────────────────────── community board ───────────────────────────

from rag import board as _bd  # noqa: E402


@app.get("/board", response_class=HTMLResponse)
def board_page(request: Request):
    active = _active_for(request)
    return templates.TemplateResponse(
        request, "board.html",
        {"active": active.meta() if active else None, "categories": _bd.CATEGORIES},
    )


@app.get("/api/board")
def api_board(category: str | None = None, days: float = _bd.DEFAULT_DAYS):
    if category and category not in _bd.CATEGORIES:
        raise HTTPException(400, "unknown category")
    relays = _load_relays_from_config()
    return {"posts": _with_authors(_bd.fetch_posts(relays, category or None, days), relays)}


@app.post("/api/board")
def api_board_post(request: Request, body: dict):
    _host_only(request)
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
    from rag import skills as _sk
    skill_list = _sk.search(asyncio.run(_sk.afetch(relays)))
    _with_authors(listings + posts + skill_list, relays)
    urls = _cards.lan_urls(request.url.port or 8090)
    join_url = urls[0] if urls else str(request.base_url)
    return templates.TemplateResponse(request, "bulletin.html", {
        "offers": [l for l in listings if l["side"] == "offering"],
        "wanted": [l for l in listings if l["side"] == "looking-for"],
        "posts": posts,
        "skills": skill_list,
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


# ──────────────────────── receipts + reputation ────────────────────────

from rag import receipts as _rc  # noqa: E402
from rag import reputation as _rep  # noqa: E402


@app.get("/api/reputation/{who}")
def api_reputation(who: str):
    try:
        r = asyncio.run(_rep.acompute(_load_relays_from_config(), who))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**r, "summary": _rep.summary_line(r)}


@app.get("/receipts", response_class=HTMLResponse)
def receipts_page(request: Request):
    active = _active_for(request)
    return templates.TemplateResponse(request, "receipts.html",
                                      {"active": active.meta() if active else None})


@app.get("/api/receipts")
def api_receipts(request: Request, pubkey: str | None = None):
    me = _whose(pubkey, request)
    events = asyncio.run(_rc.afetch_for(_load_relays_from_config(), me))
    confirmed = {b["id"] for b in _rc.confirmed_receipts(events)}
    rows = []
    for b in sorted(_rc._load_mine().values(), key=lambda b: b["date"], reverse=True):
        if me not in b["parties"]:
            continue
        other = next(p for p in b["parties"] if p != me)
        rows.append({"id": b["id"], "date": b["date"], "with": other,
                     "text": _rc.describe(b, me),
                     "confirmed": b["id"] in confirmed,
                     "code": _rc.encode_code(b)})
    return {"receipts": rows}


@app.post("/api/receipts")
def api_receipts_draft(request: Request, body: dict):
    _host_only(request)
    ident = get_active()
    if not ident:
        raise HTTPException(400, "no active identity")
    try:
        rb, code, results = _rc.draft(ident, load_nsec(ident.name), body.get("with") or "",
                                      body.get("gave") or "", body.get("got") or "",
                                      body.get("listing") or None, body.get("note") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"code": code, "qr": _cards.qr_svg(code, scale=4),
            "text": _rc.describe(rb, ident.hex_pub),
            "published_to": {u: {"ok": ok, "message": m} for u, (ok, m) in results.items()}}


@app.post("/api/receipts/confirm")
def api_receipts_confirm(request: Request, body: dict):
    _host_only(request)
    ident = get_active()
    if not ident:
        raise HTTPException(400, "no active identity")
    try:
        rb, results = _rc.confirm(ident, load_nsec(ident.name), body.get("code") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"text": _rc.describe(rb, ident.hex_pub),
            "published_to": {u: {"ok": ok, "message": m} for u, (ok, m) in results.items()}}


# ──────────────────────────── captive portal ────────────────────────────
# When the hub's router answers every DNS query with this machine's IP,
# phones joining the WiFi hit these OS connectivity probes. Redirecting
# them makes iOS / Android / Windows / macOS pop up the Pergamon home page
# as a "sign in to network" sheet. Harmless when DNS isn't redirected —
# nothing requests these paths then.

from fastapi.responses import RedirectResponse  # noqa: E402

_PROBE_PATHS = (
    "/hotspot-detect.html",          # Apple
    "/library/test/success.html",    # Apple (older)
    "/generate_204",                 # Android / Chrome
    "/gen_204",                      # Android
    "/connecttest.txt",              # Windows 10+
    "/ncsi.txt",                     # Windows (older)
    "/success.txt",                  # Firefox
    "/canonical.html",               # Firefox
)


def _portal(request: Request):
    return RedirectResponse(url="/welcome", status_code=302)


for _p in _PROBE_PATHS:
    app.add_api_route(_p, _portal, methods=["GET"], include_in_schema=False)


@app.get("/welcome", response_class=HTMLResponse)
def welcome(request: Request):
    """Where phones land when they join the hub's Wi-Fi: the home screen plus a
    short introduction and a key for posting."""
    return _home(request, welcome=True)


# ─────────────────────────── secure connection setup ───────────────────────────
# Phones fetch the hub's root certificate over plain HTTP, trust it once, then
# use HTTPS. See rag/tls.py.

from fastapi.responses import Response  # noqa: E402


def _https_settings() -> tuple[bool, int]:
    from rag.serve import hub_https_settings
    return hub_https_settings()


def _require_https_on() -> None:
    if not _https_settings()[0]:
        raise HTTPException(404, "Secure connections are not turned on for this hub.")


def _device(request: Request) -> str:
    ua = (request.headers.get("user-agent") or "").lower()
    if "iphone" in ua or "ipad" in ua:
        return "ios"
    if "android" in ua:
        return "android"
    if "macintosh" in ua or "mac os x" in ua:
        return "mac"
    if "windows" in ua:
        return "windows"
    return "other"


@app.get("/trust", response_class=HTMLResponse)
def trust_page(request: Request):
    from rag import tls
    _require_https_on()
    _, https_port = _https_settings()
    host = request.url.hostname or "localhost"
    return templates.TemplateResponse(request, "trust.html", {
        "device": _device(request),
        "secure": request.url.scheme == "https",
        "secure_url": f"https://{host}:{https_port}/welcome",
        "check_url": f"https://{host}:{https_port}/trust/check",
        "fingerprint": tls.ca_fingerprint(),
        "ca_name": tls.ca_name(),
    })


@app.get("/trust/pergamon-ca.crt")
def trust_ca_cert():
    from rag import tls
    _require_https_on()
    return Response(tls.ca_der(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="pergamon-ca.crt"'})


@app.get("/trust/pergamon.mobileconfig")
def trust_mobileconfig():
    from rag import tls
    _require_https_on()
    return Response(tls.mobileconfig(), media_type="application/x-apple-aspen-config",
                    headers={"Content-Disposition": 'attachment; filename="pergamon.mobileconfig"'})


@app.get("/trust/check")
def trust_check():
    """Fetched cross-origin from the HTTP setup page: it only succeeds once the
    browser trusts the hub's certificate, so the page can say "done"."""
    return Response('{"secure": true}', media_type="application/json",
                    headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})



# ─────────────────────── browser-held keys: draft + publish ───────────────────────
# The server builds unsigned events for a given pubkey (validation stays here);
# the browser signs them with its own key; /api/publish verifies and relays.

import json as _json  # noqa: E402
import time as _time  # noqa: E402

from rag import skills as _sk  # noqa: E402
from rag.nostr import Event as _Event, verify_event as _verify  # noqa: E402

MAX_EVENT_BYTES = 64 * 1024
_PROFILE_KEYS = {"display_name", "name", "about", "picture", "nip05", "website", "lud16", "banner"}


def _draft(ev) -> dict:
    return {"event": {"pubkey": ev.pubkey, "created_at": ev.created_at, "kind": ev.kind,
                      "tags": ev.tags, "content": ev.content}}


def _need_pub(body: dict) -> str:
    pub = (body or {}).get("pubkey")
    if not _is_hex_pub(pub):
        raise HTTPException(400, "pubkey (64 hex chars) required")
    return pub


@app.post("/api/draft/board")
def draft_board(body: dict):
    pub = _need_pub(body)
    try:
        ev = _bd.build_post(pub, body.get("text") or "", body.get("category") or "notice",
                            (body.get("location") or "").strip() or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _draft(ev)


@app.post("/api/draft/classifieds")
def draft_classified(body: dict):
    pub = _need_pub(body)
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    if not body.get("force") and len(_cl.my_live_listings(pub)) >= _cl.SOFT_CAP:
        raise HTTPException(409, f"you already have {_cl.SOFT_CAP} live listings — close or "
                                 "let one expire, or post anyway")
    accepts = body.get("accepts") or []
    if isinstance(accepts, str):
        accepts = [a.strip() for a in accepts.split(",") if a.strip()]
    tags = body.get("hashtags") or []
    if isinstance(tags, str):
        tags = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()]
    try:
        ev = _cl.build_listing_event(
            pub, title, (body.get("description") or "").strip(),
            side=body.get("side") or "offering", price_oz_ag=_num(body.get("price_oz_ag")),
            price_usd=_num(body.get("price_usd")), accepts=accepts or None,
            location=body.get("location") or None, qty=body.get("qty") or None, hashtags=tags,
            ttl_days=_num(body.get("ttl_days")) or _cl.DEFAULT_TTL_DAYS)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _draft(ev)


@app.post("/api/draft/classifieds/{d}/{action}")
def draft_classified_republish(d: str, action: str, body: dict):
    pub = _need_pub(body)
    if action not in ("renew", "close"):
        raise HTTPException(404, "unknown action")
    try:
        d = _cl.resolve_ref(d, pub)
    except KeyError as e:
        raise HTTPException(404, str(e).strip('"'))
    return _draft(_cl.build_republish(d, status="sold" if action == "close" else None))


@app.post("/api/draft/receipt")
def draft_receipt(body: dict):
    pub = _need_pub(body)
    try:
        rb = _rc.make_body(pub, _rc._hex_pub(body.get("with") or ""), body.get("gave") or "",
                           body.get("got") or "", body.get("listing") or None,
                           body.get("note") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    code = _rc.encode_code(rb)
    return {**_draft(_rc.build_receipt_event(pub, rb)), "code": code,
            "qr": _cards.qr_svg(code, scale=4), "text": _rc.describe(rb, pub)}


@app.post("/api/draft/receipt/confirm")
def draft_receipt_confirm(body: dict):
    pub = _need_pub(body)
    try:
        rb = _rc.decode_code(body.get("code") or "")
        ev = _rc.build_receipt_event(pub, rb)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**_draft(ev), "text": _rc.describe(rb, pub)}


@app.post("/api/draft/profile")
def draft_profile(body: dict):
    pub = _need_pub(body)
    meta = {k: v for k, v in body.items() if k in _PROFILE_KEYS and isinstance(v, str) and v.strip()}
    if not meta:
        raise HTTPException(400, "no profile fields")
    return _draft(build_event(kind=0, content=_json.dumps(meta, separators=(",", ":")),
                              pubkey_hex=pub))


KIND_GIFT_WRAP = 1059


def _check_publishable(ev: "_Event") -> None:
    tags = {t[0]: t[1:] for t in ev.tags if t}
    if ev.kind == 1:
        if ["pergamon-board"] not in [t[1:2] for t in ev.tags if t and t[0] == "t"]:
            raise HTTPException(400, "only community-board notes are relayed")
    elif ev.kind == 0:
        try:
            meta = _json.loads(ev.content)
        except ValueError:
            raise HTTPException(400, "profile content must be JSON")
        if not isinstance(meta, dict) or not set(meta) <= _PROFILE_KEYS:
            raise HTTPException(400, "unexpected profile fields")
    elif ev.kind in (_cl.KIND_CLASSIFIED, _sk.KIND_SKILL, _rc.KIND_RECEIPT, 30406, 30408):
        if "d" not in tags:
            raise HTTPException(400, "addressable event needs a d tag")
    elif ev.kind == KIND_GIFT_WRAP:
        # A private message: encrypted, signed by a throwaway key, addressed by
        # exactly one "p" tag. The hub relays it without being able to read it.
        ps = [t for t in ev.tags if t and t[0] == "p"]
        if len(ps) != 1 or len(ev.tags) != 1 or not _is_hex_pub(ps[0][1] if len(ps[0]) > 1 else ""):
            raise HTTPException(400, "a gift wrap needs exactly one recipient tag")
    else:
        raise HTTPException(400, f"kind {ev.kind} is not relayed by this hub")
    if abs(ev.created_at - _time.time()) > 2 * 86400:
        raise HTTPException(400, "event timestamp is too far from now — check the device clock")


@app.post("/api/publish")
def api_publish(body: dict):
    raw = (body or {}).get("event") or {}
    if len(_json.dumps(raw)) > MAX_EVENT_BYTES:
        raise HTTPException(413, "event too large")
    try:
        ev = _Event(kind=int(raw["kind"]), content=str(raw["content"]), pubkey=str(raw["pubkey"]),
                    tags=list(raw["tags"]), created_at=int(raw["created_at"]),
                    id=str(raw["id"]), sig=str(raw["sig"]))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "malformed event")
    if not _verify(ev):
        raise HTTPException(400, "signature does not verify")
    from rag import moderation as _mod
    if _mod.is_blocked(ev.pubkey):
        raise HTTPException(403, "This hub's host has blocked this key from posting here.")
    _check_publishable(ev)
    results = _cl.publish(ev, _load_relays_from_config())
    if ev.kind == _cl.KIND_CLASSIFIED:
        _cl._remember(ev)
    elif ev.kind == _sk.KIND_SKILL:
        _sk._remember(ev)
    elif ev.kind == _rc.KIND_RECEIPT:
        _rc._remember(_json.loads(ev.content))
    return {"id": ev.id,
            "published_to": {u: {"ok": ok, "message": m} for u, (ok, m) in results.items()}}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(STATIC_DIR / "icons" / "icon-32.png", media_type="image/png")


# ─────────────────────────────── skills ───────────────────────────────


def _skill_fields(body: dict) -> dict:
    accepts = body.get("accepts") or []
    if isinstance(accepts, str):
        accepts = [a.strip() for a in accepts.split(",") if a.strip()]
    return dict(
        title=(body.get("title") or "").strip(),
        description=(body.get("description") or "").strip(),
        category=body.get("category") or "other",
        level=body.get("level") or None,
        availability=(body.get("availability") or "").strip() or None,
        radius_mi=_num(body.get("radius_mi")),
        location=(body.get("location") or "").strip() or None,
        rate=(body.get("rate") or "").strip() or None,
        cert=(body.get("cert") or "").strip() or None,
        accepts=accepts or None,
    )


@app.get("/skills", response_class=HTMLResponse)
def skills_page(request: Request):
    active = _active_for(request)
    return templates.TemplateResponse(request, "skills.html", {
        "active": active.meta() if active else None,
        "categories": _sk.CATEGORIES, "levels": _sk.LEVELS})


@app.get("/api/skills")
def api_skills(q: str = "", category: str | None = None):
    events = asyncio.run(_sk.afetch(_load_relays_from_config(), category or None))
    return {"skills": _with_authors(_sk.search(events, q, category or None))}


@app.get("/api/skills/mine")
def api_skills_mine(request: Request, pubkey: str | None = None):
    me = _whose(pubkey, request)
    rows = [_sk.summarize(_Event(**{k: r[k] for k in ("kind", "content", "pubkey", "tags",
                                                     "created_at", "id", "sig")}))
            for r in _sk._load_mine().values() if r["pubkey"] == me]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return {"skills": rows}


@app.post("/api/draft/skills")
def draft_skill(body: dict):
    pub = _need_pub(body)
    try:
        return _draft(_sk.build_skill_event(pub, **_skill_fields(body)))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/draft/skills/{d}/{action}")
def draft_skill_republish(d: str, action: str, body: dict):
    pub = _need_pub(body)
    if action not in ("renew", "close"):
        raise HTTPException(404, "unknown action")
    try:
        d = _sk.resolve_ref(d, pub)
    except KeyError as e:
        raise HTTPException(404, str(e).strip('"'))
    return _draft(_sk.build_republish(d, "closed" if action == "close" else None))


@app.post("/api/skills")
def api_skills_post(request: Request, body: dict):
    _host_only(request)
    ident = get_active()
    if not ident:
        raise HTTPException(400, "no identity — create a key on this device")
    try:
        ev, results = _sk.post(ident, load_nsec(ident.name), **_skill_fields(body))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"skill": _sk.summarize(ev),
            "published_to": {u: {"ok": ok, "message": m} for u, (ok, m) in results.items()}}


@app.post("/api/skills/{d}/{action}")
def api_skills_republish(request: Request, d: str, action: str):
    _host_only(request)
    ident = get_active()
    if not ident or action not in ("renew", "close"):
        raise HTTPException(400, "no identity or unknown action")
    try:
        ev = _sk.republish(d, load_nsec(ident.name), "closed" if action == "close" else None)
    except KeyError:
        raise HTTPException(404, f"no skill listing {d}")
    results = _cl.publish(ev, _load_relays_from_config())
    return {"skill": _sk.summarize(ev),
            "published_to": {u: {"ok": ok, "message": m} for u, (ok, m) in results.items()}}


# ─────────────────────────── paper drop-box forms ───────────────────────────

FORM_KINDS = ("board", "listing", "update", "receipt", "skill", "member")


@app.get("/forms", response_class=HTMLResponse)
def forms_page(request: Request):
    """Printable slips for the drop box. Each QR encodes pergamon-form:<kind>:v1
    so a future scanner can route slips automatically."""
    qr = {k: _cards.qr_svg(f"pergamon-form:{k}:v1", scale=2) for k in FORM_KINDS}
    return templates.TemplateResponse(request, "forms.html", {"qr": qr})


# ─────────────────────────────── food inventory ───────────────────────────────
# Household-private: stored in the food ledger on this machine, never published.

from datetime import date as _date  # noqa: E402

from rag import ledger as _lg  # noqa: E402

SOON_DAYS = 7
COOK_PROMPT = """You are Pergamon's kitchen helper. The user lists what's in their pantry,
with items that expire soonest first. Suggest 3 practical meals they can make mostly from
these ingredients, preferring the items marked USE SOON. For each: name, ingredients from
the list (and any common staples assumed), and short steps. Flag any food-safety concern
(e.g. items past their date, home-canned goods with bulging lids). Use recipes from LIBRARY
EXCERPTS if relevant and cite them. Be concise."""


def _food_allowed(request: Request) -> None:
    """Food data is household-private. Allow it from this machine only, unless
    install.local.yaml sets food: {lan_access: true} (fine on a home network,
    not on a community hub)."""
    if _is_host(request) or ((_local_cfg().get("food") or {}).get("lan_access")) is True:
        return
    raise HTTPException(403, "The food inventory is only available on this machine. "
                             "To use it from household phones, set food: {lan_access: true} "
                             "in config/install.local.yaml (not on a shared hub).")


def _food_rows() -> list[dict]:
    today = _date.today()
    rows = _lg.query_rows("food")
    for r in rows:
        r["days_left"] = None
        if r.get("expires"):
            r["days_left"] = (_date.fromisoformat(r["expires"]) - today).days
    rows.sort(key=lambda r: (r["days_left"] is None, r["days_left"] if r["days_left"] is not None else 0,
                             r["item"].lower()))
    return rows


def _food_values(body: dict, partial: bool = False) -> dict:
    allowed = ("item", "qty", "unit", "location", "barcode", "expires", "notes")
    vals = {k: str(body[k]).strip() for k in allowed if k in body and body[k] not in (None, "")}
    if not partial and not vals.get("item"):
        raise HTTPException(400, "item required")
    return vals


@app.get("/food", response_class=HTMLResponse)
def food_page(request: Request):
    _food_allowed(request)
    schema = _lg._load_schema("food")
    locations = next(f["options"] for f in schema["fields"] if f["name"] == "location")
    return templates.TemplateResponse(request, "food.html", {"locations": locations,
                                                             "soon_days": SOON_DAYS})


@app.get("/api/food")
def api_food(request: Request, location: str | None = None, q: str = ""):
    _food_allowed(request)
    rows = _food_rows()
    if location:
        rows = [r for r in rows if r.get("location") == location]
    if q:
        rows = [r for r in rows if q.lower() in (r["item"] + " " + (r.get("notes") or "")).lower()]
    soon = [r for r in rows if r["days_left"] is not None and r["days_left"] <= SOON_DAYS]
    return {"items": rows, "soon": soon}


@app.post("/api/food")
def api_food_add(request: Request, body: dict):
    _food_allowed(request)
    try:
        rid = _lg.add_row("food", _food_values(body))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": rid}


@app.post("/api/food/{row_id}")
def api_food_update(request: Request, row_id: int, body: dict):
    _food_allowed(request)
    try:
        ok = _lg.update_row("food", row_id, _food_values(body, partial=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "no such item")
    return {"ok": True}


@app.delete("/api/food/{row_id}")
def api_food_delete(request: Request, row_id: int):
    _food_allowed(request)
    if not _lg.remove_row("food", row_id):
        raise HTTPException(404, "no such item")
    return {"ok": True}


def pantry_text(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        if r.get("qty") is not None and float(r["qty"]) <= 0:
            continue
        amount = " ".join(x for x in (f"{r['qty']:g}" if r.get("qty") is not None else "",
                                      r.get("unit") or "") if x)
        flag = ""
        if r["days_left"] is not None:
            flag = " (EXPIRED)" if r["days_left"] < 0 else (
                " (USE SOON)" if r["days_left"] <= SOON_DAYS else "")
        lines.append(f"- {r['item']}{' — ' + amount if amount else ''}{flag}")
    return "\n".join(lines)


@app.get("/api/food/cook")
async def api_food_cook(request: Request):
    _food_allowed(request)
    text = pantry_text(_food_rows())
    if not text:
        raise HTTPException(400, "your pantry is empty — add some items first")
    # Pantry items are bought or stored food, not wild finds; COOK_PROMPT carries
    # its own food-safety rules, so skip the foraging guard here.
    return _stream_rag(request, text, COOK_PROMPT, "PANTRY", use_domain_guards=False)


# ─────────────────────────────── accounts ───────────────────────────────
# Username + password sign-in with the key underneath (rag/accounts.py). The
# password never reaches the hub; it stores only a password-locked key.

from rag import accounts as _accounts  # noqa: E402


@app.get("/api/account/salt")
def account_salt(username: str):
    acct = _accounts.get_store().get(username)
    if not acct:
        raise HTTPException(404, "No account by that name on this hub.")
    return {"salt": acct.salt, "log_n": acct.log_n}


@app.post("/api/account/signup")
def account_signup(body: dict):
    body = body or {}
    try:
        username = _accounts.normalize(body.get("username", ""))
        pubkey = _accounts.check_proof(body.get("proof") or {}, username)
        acct = _accounts.get_store().create(
            username, pubkey=pubkey, name=str(body.get("name") or username),
            salt=str(body.get("salt", "")), log_n=int(body.get("log_n") or 0),
            auth=str(body.get("auth", "")), ncryptsec=body.get("ncryptsec"))
    except (_accounts.AccountError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"username": acct.username, "pubkey": acct.pubkey, "key_on_hub": acct.ncryptsec is not None}


@app.post("/api/account/signin")
def account_signin(request: Request, body: dict):
    body = body or {}
    client = request.client.host if request.client else "?"
    try:
        acct = _accounts.get_store().sign_in(str(body.get("username", "")), str(body.get("auth", "")), client)
    except _accounts.AccountError as e:
        raise HTTPException(429 if "Too many" in str(e) else 401, str(e))
    return {"username": acct.username, "pubkey": acct.pubkey, "name": acct.name,
            "ncryptsec": acct.ncryptsec}


# ─────────────────────────────── moderation ───────────────────────────────
# The host hides posts and blocks keys on their own hub (rag/moderation.py).

from rag import moderation as _moderation  # noqa: E402


def _moderator_only(request: Request) -> None:
    if not _is_host(request):
        raise HTTPException(403, "Only the hub's host can hide posts or block keys.")


@app.get("/moderation", response_class=HTMLResponse)
def moderation_page(request: Request):
    _moderator_only(request)
    return templates.TemplateResponse(request, "moderation.html", {"mod": _moderation.summary()})


@app.get("/api/moderation")
def api_moderation(request: Request):
    _moderator_only(request)
    return _moderation.summary()


@app.post("/api/moderation/{action}")
def api_moderation_change(request: Request, action: str, body: dict):
    _moderator_only(request)
    body = body or {}
    try:
        if action == "hide":
            key = _moderation.hide(str(body.get("id", "")), kind=body.get("kind"),
                                   pubkey=body.get("pubkey"), tags=[["d", body["d"]]] if body.get("d") else [],
                                   preview=str(body.get("preview", "")))
            return {"hidden": key}
        if action == "unhide":
            return {"unhidden": _moderation.unhide(str(body.get("key", "")))}
        if action == "block":
            return {"blocked": _moderation.block(str(body.get("pubkey", "")), str(body.get("note", "")))}
        if action == "unblock":
            return {"unblocked": _moderation.unblock(str(body.get("pubkey", "")))}
    except ValueError as e:
        raise HTTPException(400, str(e))
    raise HTTPException(404, "unknown moderation action")


# ─────────────────────────────── messages ───────────────────────────────
# End-to-end encrypted private messages (NIP-17 gift wraps). Phones encrypt
# and decrypt; the hub only stores and relays wraps it can't read.

INBOX_LIMIT = 500


def _check_proof(proof, purpose: str) -> "_Event":
    """A fresh note signed by the caller's key, for `purpose`: proves who is
    asking without accounts or cookies."""
    proof = proof or {}
    try:
        ev = _Event(kind=int(proof["kind"]), content=str(proof["content"]), pubkey=str(proof["pubkey"]),
                    tags=list(proof["tags"]), created_at=int(proof["created_at"]),
                    id=str(proof["id"]), sig=str(proof["sig"]))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "missing proof")
    if not _verify(ev) or ev.kind != 27235 or ["purpose", purpose] not in [list(t) for t in ev.tags]:
        raise HTTPException(401, "proof doesn't verify")
    if abs(_time.time() - ev.created_at) > 600:
        raise HTTPException(401, "proof is stale; check this device's clock")
    return ev


@app.post("/api/messages/inbox")
def messages_inbox(body: dict):
    """Gift wraps addressed to the key that signed `proof` (a fresh note), so
    nobody can list someone else's mail (even though they couldn't read it)."""
    ev = _check_proof((body or {}).get("proof"), "inbox")
    wraps = asyncio.run(_nostr_afetch(_load_relays_from_config(),
                                      {"kinds": [KIND_GIFT_WRAP], "#p": [ev.pubkey], "limit": INBOX_LIMIT},
                                      INBOX_LIMIT))
    return {"wraps": [w.to_json() for w in wraps]}


@app.get("/messages", response_class=HTMLResponse)
def messages_page(request: Request):
    return templates.TemplateResponse(request, "messages.html", {})


@app.get("/api/people")
def people(username: str = "", pubkeys: str = ""):
    """Who's who for messaging: a username → its key, or keys → usernames and
    names (local accounts first, then shared profiles)."""
    store = _accounts.get_store()
    if username:
        acct = store.get(username)
        if not acct:
            raise HTTPException(404, "No one by that username on this hub.")
        return {"pubkey": acct.pubkey, "username": acct.username, "name": acct.name}
    keys = [k for k in pubkeys.split(",") if _is_hex_pub(k)][:100]
    out = {}
    from rag import hubs
    if hubs.pubkey() in keys:
        out[hubs.pubkey()] = {"username": None, "name": f"{hubs.name()} (this hub)"}
    for k in keys:
        acct = store.by_pubkey(k)
        if acct:
            out[k] = {"username": acct.username, "name": acct.name}
    missing = [k for k in keys if k not in out]
    if missing:
        from rag import profiles as _pf
        try:
            names = _pf.fetch_names(_load_relays_from_config(), missing)
        except Exception:
            names = {}
        for k in missing:
            if names.get(k):
                out[k] = {"username": None, "name": names[k]}
    return {"people": out}


# ─────────────────────────────── hub identity ───────────────────────────────

@app.get("/api/hub/hello")
def hub_hello(challenge: str):
    """Another hub checks who this is before syncing: we sign its challenge."""
    from rag import hubs
    try:
        return hubs.hello(challenge)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/hubs", response_class=HTMLResponse)
def hubs_page(request: Request):
    from rag import hubs
    from rag.cards import qr_svg
    _moderator_only(request)
    code = hubs.code()
    return templates.TemplateResponse(request, "hubs.html", {
        "name": hubs.name(), "code": code, "qr": qr_svg(code, scale=4), "hubs": hubs.trusted()})


@app.post("/api/hubs/{action}")
def hubs_change(request: Request, action: str, body: dict):
    from rag import hubs
    _moderator_only(request)
    body = body or {}
    try:
        if action == "trust":
            info = hubs.parse_code(str(body.get("code", "")))
            return {"trusted": hubs.trust(info)}
        if action == "untrust":
            return {"removed": hubs.untrust(str(body.get("who", "")))}
    except hubs.TrustError as e:
        raise HTTPException(400, str(e))
    raise HTTPException(404, "unknown action")


# ─────────────────────────────── feeds ───────────────────────────────

@app.get("/api/feeds")
def feeds_list():
    from rag import feeds
    return {"feeds": [{"id": f, "title": t} for f, (t, _) in feeds.FEEDS.items()],
            "times": [t.strftime("%H:%M") for t in feeds.times()]}


@app.post("/api/feeds/mine")
def feeds_mine(body: dict):
    """Read, or with `feeds` set, a key's subscriptions. Web subscribers get
    digests as sealed messages from the hub in Messages."""
    from rag import feeds
    ev = _check_proof((body or {}).get("proof"), "feeds")
    if "feeds" in (body or {}):
        try:
            return {"feeds": feeds.set_subscriptions(ev.pubkey, list(body["feeds"] or []))}
        except feeds.FeedError as e:
            raise HTTPException(400, str(e))
    return {"feeds": feeds.subscriptions(ev.pubkey)}


# ─────────────────────────────── local guide ───────────────────────────────

@app.get("/guide", response_class=HTMLResponse)
def guide_page(request: Request):
    from rag import guide
    return templates.TemplateResponse(request, "guide.html", {
        "categories": guide.PLACE_CATEGORIES, "is_host": _is_host(request)})


@app.get("/api/guide/planting")
def guide_planting():
    from rag import guide
    p = guide.plant_now()
    fr = p["frost"]
    row = lambda w: {k: (str(v) if k in ("start", "end") else v) for k, v in w.items()}  # noqa: E731
    out = {"frost": fr, "now": [row(w) for w in p["now"]], "soon": [row(w) for w in p["soon"]]}
    if fr:
        import datetime as _dt
        out["year"] = [row(w) for w in guide.windows(fr, _dt.date.today().year)]
    return out


@app.post("/api/guide/frost")
def guide_set_frost(request: Request, body: dict):
    from rag import guide
    _moderator_only(request)
    try:
        return guide.set_frost(str((body or {}).get("last", "")), str((body or {}).get("first", "")))
    except guide.GuideError as e:
        raise HTTPException(400, str(e))


@app.get("/api/guide/places")
def guide_places(q: str = "", category: str | None = None):
    from rag import guide
    places = guide.fetch_places(_load_relays_from_config(), q, category or None)
    authors = {n["pubkey"] for p in places for n in p["notes"]}
    from rag import profiles as _pf
    try:
        names = _pf.fetch_names(_load_relays_from_config(), list(authors))
    except Exception:
        names = {}
    store = _accounts.get_store()
    for p in places:
        for n in p["notes"]:
            acct = store.by_pubkey(n["pubkey"])
            n["author"] = names.get(n["pubkey"]) or (acct and (acct.name or acct.username)) or n["pubkey"][:8] + "…"
    return {"places": places}


@app.post("/api/draft/place")
def draft_place(body: dict):
    from rag import guide
    pub = _need_pub(body)
    try:
        return _draft(guide.build_place_note(pub, str(body.get("place", "")), str(body.get("note", "")),
                                             str(body.get("category") or "other"), str(body.get("location") or "")))
    except guide.GuideError as e:
        raise HTTPException(400, str(e))


# ─────────────────────────────── radios ───────────────────────────────

@app.get("/radios", response_class=HTMLResponse)
def radios_page(request: Request):
    from rag import radios
    _moderator_only(request)
    now = radios.connected()
    ids = {r["id"] for r in now}
    return templates.TemplateResponse(request, "radios.html", {
        "connected": now, "listening": bool(now) or radios._read(radios.CONNECTED_PATH).get("updated", 0) > _time.time() - 120,
        "others": {k: v for k, v in radios.known().items() if k not in ids}})


@app.post("/api/radios/role")
def radios_role(request: Request, body: dict):
    from rag import radios
    _moderator_only(request)
    body = body or {}
    try:
        if body.get("role") == "forget":
            return {"forgotten": radios.forget(str(body.get("id", "")))}
        name = next((r["name"] for r in radios.connected() if r["id"] == body.get("id")), "")
        return radios.set_role(str(body.get("id", "")), str(body.get("role", "")), name)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/radios/preset")
def radios_preset(request: Request, body: dict):
    from rag import radios
    _moderator_only(request)
    try:
        return {"requested": radios.request_preset(str((body or {}).get("preset", "")))}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─────────────────────────────── maps ───────────────────────────────

@app.get("/maps", response_class=HTMLResponse)
def maps_page(request: Request):
    from rag import maps
    return templates.TemplateResponse(request, "maps.html", {
        "installed": maps.installed(), "info": maps.region_info() or {}, "is_host": _is_host(request)})


@app.get("/maps/region.pmtiles", include_in_schema=False)
def maps_tiles():
    """The region's map file; the viewer reads it in byte ranges."""
    from fastapi.responses import FileResponse
    from rag import maps
    if not maps.tiles_path().exists():
        raise HTTPException(404, "no offline map installed")
    return FileResponse(maps.tiles_path(), media_type="application/octet-stream",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/maps/web/{path:path}", include_in_schema=False)
def maps_web(path: str):
    from fastapi.responses import FileResponse
    from rag import maps
    root = maps.web_dir().resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target, headers={"Cache-Control": "public, max-age=604800"})
