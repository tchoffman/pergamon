# Pergamon

An offline reference machine for extended off-grid scenarios. Combines large knowledge bases (Wikipedia, survival manuals, repair guides, OpenStreetMap, etc.) with a locally-running LLM so you can **research, plan, and look things up without any internet**.

Named after the [ancient library of Pergamon](https://en.wikipedia.org/wiki/Library_of_Pergamum) — Alexandria's rival, famous for parchment and for surviving when other collections burned.

This repo is the **recipe**, not the appliance. Running the scripts on a fresh machine produces a working Pergamon: models pulled, corpus downloaded, RAG index built, servers ready. The actual knowledge/models are gitignored and live in `build/` locally.

## Design

```
┌─ GitHub (this repo) ──────────────┐    ┌─ Local machine (your Pergamon) ─┐
│ scripts/     — build recipe        │ ─> │ build/models/    — Ollama blobs │
│ bin/         — CLI tools          │    │ build/corpus/    — ZIMs + PDFs  │
│ rag/         — Python package     │    │ build/chroma/    — vector DB    │
│ Makefile     — orchestration      │    │ (gitignored, portable)          │
└───────────────────────────────────┘    └─────────────────────────────────┘
```

## Quick start (on a fresh Mac)

```bash
git clone https://github.com/tchoffman/pergamon.git
cd pergamon
make setup        # (optional) pick what to install interactively
make all          # install deps → pull models → fetch corpus → build index
bin/pergamon ask "how do I purify surface water?"
bin/pergamon serve  # browse ZIMs at http://localhost:8080
```

Skip `make setup` and `make all` uses the **default** preset (~5 GB, fits a 16 GB Mac). Other presets live in `config/presets/`:

| Preset | Size | What |
|---|---|---|
| **minimal** | ~1 GB | Wikipedia Simple + survival PDFs. Barebones. |
| **default** | ~5 GB | + Wikivoyage, Wiktionary, iFixit, SE dumps, top-100 Gutenberg. |
| **comprehensive** | ~80 GB | Full English Wikipedia, Khan Academy core, wide SE dumps, mirrored docs sites. Cage-class disk. |

`make setup` writes `config/install.local.yaml` (gitignored) — you can also edit it by hand. `make plan` shows what would be fetched without downloading. `make all` is idempotent — re-running only does work that's missing. See `make help`.

## What it does

**The library** — offline wikis and books, plus a local LLM that answers with citations.

| | |
|---|---|
| Ask / plan / search | `pergamon ask`, `plan`, `search`, or the web UI at `/` |
| Offline wikis | Wikipedia, Wikivoyage, iFixit, … via Kiwix (`pergamon serve`) |
| Books | Survival manuals, a foraging + plant ID shelf, Gutenberg classics |
| Safety rules | Foraging, medical, and electrical questions get extra rules and a warning |

**The community network** — what a neighborhood uses when the internet is gone. Runs on the
Pergamon's WiFi; phones need only a browser.

| | |
|---|---|
| Community board | Local news, events, lost & found (`/board`) |
| Classifieds | Offers and wanted posts, 14-day expiry, matches (`/classifieds`) |
| Skills + services | Who can fix, build, teach, or treat (`/skills`) |
| Library + lending | Books, tools, seeds you'll lend (`pergamon library`) |
| Trade receipts + reputation | Both sides confirm; reputation is plain counts (`/receipts`) |
| Identity | Each phone makes its own key; nothing secret leaves the device |
| Paper on-ramp | Printed bulletin, drop-box forms, member cards (`/bulletin`, `/forms`) |
| Town to town | `pergamon sync` over any link, or a USB stick carried by courier |
| Mesh radio | Ask the library over Meshtastic (`pergamon mesh`) |

**At home** — food inventory with "what can I cook?" (`/food`), calendar, ebook library,
password vault (Docker services).

## Documentation

- **[docs/getting-started.md](docs/getting-started.md)** — first-time install, step by step. Start here.
- **[docs/commands.md](docs/commands.md)** — every command, flag, and web page.
- **[docs/grid-down.md](docs/grid-down.md)** — running a Pergamon as a neighborhood hub.
- **[docs/mesh-test-plan.md](docs/mesh-test-plan.md)** — two-radio hardware checklist.
- **[docs/identity.md](docs/identity.md)** — keys, backup, recovery.
- **[docs/hardware.md](docs/hardware.md)** — dev box vs. long-term "cage machine".
- **[docs/quick-reference.md](docs/quick-reference.md)** — printable survival cheatsheet.
- **[docs/proposals.md](docs/proposals.md)** — ideas awaiting a yes/no.
- **[docs/ideas.md](docs/ideas.md)** — the longer backlog.

## Under the hood

| Component | Tool |
|---|---|
| LLM + embeddings | [Ollama](https://ollama.com) — `qwen3.5:9b` + `nomic-embed-text` by default (`make setup` to change) |
| Vector store | [Chroma](https://www.trychroma.com) |
| Offline wikis | [Kiwix](https://kiwix.org) ZIM files |
| Community data | [Nostr](https://nostr.com) events on a local relay — signed, so they can travel anywhere |
| Browser signing | `@noble/curves` (vendored, works offline) |
| Web | FastAPI + plain HTML/JS, no build step |
| Tests | `make test` (unit + real-WebSocket integration), `make e2e` (headless Chrome, two users) |

## Hardware

Dev target: M4 MacBook Air, 16GB RAM, ~80GB free disk. See [docs/hardware.md](docs/hardware.md) for the long-term "cage machine" recommendations and faraday considerations.

## Status

Built and tested in software; not yet tried on real radios or a real router-based
captive portal. Open work is tracked in [GitHub issues](https://github.com/tchoffman/pergamon/issues).

## Why "Pergamon"?

A library that survives when the rest doesn't. Lots of inputs, synthesized answers, trusted when the regular channels are down.
