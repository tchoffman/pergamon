# Command reference

Every `pergamon` subcommand with its flags. For the first-time
walkthrough, see [getting-started.md](getting-started.md).

## Build + lifecycle (Makefile)

```bash
make help            # one-line help per target
make setup           # interactive install picker → writes install.local.yaml
make plan            # dry-run: show what `make corpus` would download
make all             # full build: deps → venv → models → corpus → index
make deps            # install Ollama + kiwix-serve (idempotent)
make venv            # Python venv under build/venv
make models          # pull LLM + embedding model via Ollama
make corpus          # download ZIMs + public-domain PDFs per install config
make index           # embed PDFs into Chroma (uses active embedding model)
make zim-index       # embed ZIM articles into Chroma (slow; ZIM_LIMIT=N caps)
make status          # pergamon status (deps, models, corpus, index)
make serve           # launch kiwix-serve or Kiwix.app with your ZIMs
make web             # launch the web UI at :8090
make test            # pytest unit suite (no network)
make check-urls      # HEAD-check every URL in the plan (slow; needs network)
make smoke-test      # end-to-end: deps/models/corpus/index/ask
make e2e             # drive the web pages in headless Chrome as two users
make clean           # wipe build/ (redownload everything)
```

## Core: ask / plan / search

```bash
pergamon ask "question"
#   Streams a RAG answer. Retrieves top-K chunks, feeds to the LLM with
#   citations. Prints Sources at the end — each cites the ZIM article URL
#   (if applicable) and chunk index.

pergamon plan "goal"
#   Same retrieval but with a planner-tuned system prompt. Output is
#   structured markdown: Goal / Steps / Resources / Risks & mitigations
#   / Verification.

pergamon search "query" [--k N]
#   Raw semantic search — top-K chunks with sources + previews. No LLM.
#   Default K is 6; override with --k.
```

## Browse the library

```bash
pergamon serve [--port N] [--app]
#   Default port 8080. Prefers headless kiwix-serve binary; if missing
#   AND Kiwix.app is installed, falls back to opening the GUI with your
#   ZIMs pre-loaded. --app forces GUI mode.

pergamon web [--port N] [--host 0.0.0.0]
#   Web UI at :8090 by default. Ask / Plan / Search in a browser, plus
#   /identity, /.well-known/nostr.json, etc.
```

## Status + admin

```bash
pergamon status      # Rich table: binaries, models, ZIMs, PDFs, Chroma count
pergamon shell       # Drop into a Python REPL with the venv activated
pergamon setup       # Interactive install picker
```

## Identity (Nostr keys)

See [identity.md](identity.md) for background.

```bash
pergamon identity list
#   Table of all identities on this machine + which is active.

pergamon identity create <name> [--nip05 user@domain] [--display "Name"]
#   Generate a new keypair; mark it active. --nip05 is optional but useful
#   for human-readable handles.

pergamon identity use <name>
#   Switch the active identity. All subsequent signing (ask doesn't sign,
#   but nostr/classified/delegation do) uses this one.

pergamon identity show [<name>] [--reveal]
#   Show public info. --reveal ALSO prints the nsec (be sure no one's
#   looking over your shoulder).

pergamon identity export <name> [--path FILE]
#   Export the nsec. Default prints to stdout; --path writes a 0600 file.
#   Put it on a USB drive; if the machine dies, this is your recovery.

pergamon identity import <name> <file>
#   Create an identity from an exported nsec. Useful for restoring or
#   moving between machines.

pergamon identity delete <name>
#   Removes the key file + metadata. Prompts for confirmation.

pergamon identity card [--name N] [--path FILE]
#   Printable sheet of 4 member cards (npub as QR). Public key only.
```

## Nostr publishing

```bash
pergamon nostr post "content"  [--kind N]  [-t tagname,value ...]
#   Sign with the active identity, publish to all configured relays.
#   Default kind is 1 (short note).

pergamon nostr relays            # list configured relays
pergamon nostr relays add ws://peer.example/
pergamon nostr relays rm  ws://peer.example/
```

## Classifieds

Listings expire after 14 days unless renewed; renewing or closing re-signs
the same listing so other pergamons replace the old version. Ten live
listings per person is the soft cap (`--force` to exceed).

```bash
pergamon classified post --title "20 lb apples" \
    [--want]                         # a wanted post instead of an offer
    [--qty "20 lb"] [--price-oz-ag 0.3] [--price-usd 12.50] \
    [--accepts silver,barter,labor] [--location "Asheville NC"] \
    [--ttl 14d] [-t apples -t harvest] [--force] \
    "description text..."

pergamon classified list [--offers|--wanted] [--tag X] [--limit 50] [--all]
#   Live listings only unless --all.
pergamon classified search "chicken"
pergamon classified mine             # your listings with days left; N/10 live
pergamon classified renew <id>       # another 14 days
pergamon classified close <id>       # sold / no longer wanted
pergamon classified matches          # others' offers that fit your wants, and vice versa
```

## Community board

Local news, notices, events, help, lost & found, warnings. Plain Nostr
notes, signed, not moderated.

```bash
pergamon board post --category news [--location X] "Bridge on Rt 9 is out"
#   categories: news, notice, event, help, lost-found, warning
pergamon board list [--category event] [--days 30]
```

## Skills + services

```bash
pergamon skills post --title "Small engine repair" --category repair \
    [--level journeyman] [--availability weekends] [--radius 15] \
    [--rate "1 day labor"] [--accepts barter,silver] [--cert "..."] [--location X] \
    "Mowers, chainsaws, generators."
#   categories: medical repair building food farming animals teaching childcare
#   transport comms sewing metalwork woodwork electrical labor other
#   Expires after 60 days unless renewed.
pergamon skills list [--category medical]
pergamon skills find "midwife"
pergamon skills mine | renew <id> | close <id>
```

## Library + lending

Books, tools, digital files, seeds, media. Only title, type, and creator of
lendable/seedable items are ever published.

```bash
pergamon library add title="Where There Is No Doctor" type=book lendable=yes
pergamon library add title="Stihl MS 250" type=tool lendable=yes serial=X1 \
    required_skill="experienced" usage_limits="return with full tank"
pergamon library list [--type tool] [--lendable]
pergamon library lend <item_id> --to <npub-or-name> [--due 14d] [--borrowed]
pergamon library return <loan_id> [--condition "chain dull"]
pergamon library loans [--open|--overdue]
pergamon library publish             # share your catalog (expires in 45 days)
pergamon library find "pressure canner" [--type tool]
```

## Trade receipts + reputation

A trade counts only when both people have confirmed the same receipt.

```bash
pergamon receipt draft --with <npub> --gave "0.3 oz Ag" --got "20 lb apples" [--listing <id>]
#   prints a code; the other person runs:
pergamon receipt confirm <code>
pergamon receipt list                # confirmed / waiting on the other side

pergamon reputation <npub>
#   e.g. "3 confirmed trades · with 2 people · 4/5 listings kept up · active 40d"
#   Counted from what this pergamon can see.
```

## Sync between pergamons

Board posts, classifieds, receipts, library catalogs, skills, and profiles.
Not DMs. Everything is signature-checked; duplicates are ignored.

```bash
pergamon sync pull ws://other-hub:8091/      # theirs → yours
pergamon sync push ws://other-hub:8091/      # yours → theirs
pergamon sync both ws://other-hub:8091/
pergamon sync export --path /Volumes/USB/hub-2027-03-01.jsonl [--days 60]
pergamon sync import /Volumes/USB/hub-2027-03-01.jsonl
```

## Delegation (NIP-26)

```bash
pergamon delegation create --to <npub> [--kind N]+ [--until 30d]
#   Sign a delegation authorizing <npub> to post specific kinds on your
#   behalf for the window. --until accepts: 7d / 24h / ISO date / unix.
#   Outputs the delegation tag; share with the delegatee who attaches it
#   to events.

pergamon delegation verify <delegator_hex> <delegatee_hex> <conditions> <sig>
#   Verify a delegation signature.
```

## Mesh (Meshtastic)

```bash
pergamon mesh mock
#   Dev mode: stdin → chunked LoRa-formatted RAG answer. No radio needed.

pergamon mesh listen
#   Production: opens serial interface to a USB-attached Meshtastic node.
#   Subscribes to DMs; replies with chunked RAG answers.
#   Honors config/install.local.yaml → comms.meshtastic.allowed_node_ids.

pergamon mesh info                   # attached radio, region, nodes it can hear
pergamon mesh send <!nodeid> "text"  # one-off DM; ^all to broadcast
```

See [mesh-test-plan.md](mesh-test-plan.md) for the two-radio checklist.

## Ledger (generic records)

```bash
pergamon ledger list
#   Show available ledgers (defined in config/ledgers/*.yaml).

pergamon ledger show <name> [filters...]
#   Print all rows (or filtered) as a table. Filter forms: k=v, k~sub, k<N,
#   k>N, k<=N, k>=N.

pergamon ledger add <name> field=value [...]
#   Insert a row. Date fields accept ISO (2027-01-15), bool fields accept
#   yes/no/true/false/1/0, enums must match declared options.

pergamon ledger rm <name> <id>
#   Delete a row by its integer id.

pergamon ledger query <name> field=value [...]
#   Alias of show with filters.
```

## Services (Docker)

Requires Docker Desktop. Each service lives under `services/<id>/` with
its own `docker-compose.yml` + README.

```bash
pergamon services list                   # catalog + enabled state (on/off)
pergamon services up [id ...]            # start enabled (or named) services
pergamon services down [id ...]          # stop
pergamon services restart [id ...]       # restart
pergamon services status                 # docker ps filtered to pergamon-*
pergamon services logs <id> [-f]         # tail one service's logs
```

Services shipped today:

| id | title | port | what |
|---|---|---|---|
| radicale | Calendar + Contacts | 5232 | CalDAV/CardDAV |
| calibre-web | E-reader library | 8083 | Ebooks + Send-to-Kindle |
| vaultwarden | Password vault | 8089 | Bitwarden-compatible |
| nostr-relay | Nostr relay | 8091 | WebSocket; backbone for classifieds + DMs |

Each service's `README.md` has its own first-time-setup walkthrough.

## Web pages

`pergamon web` (default port 8090) serves:

| Page | What |
|---|---|
| `/welcome` | Landing page for phones on the hub WiFi; create or restore this device's key |
| `/` | Ask / Plan / Search the library |
| `/board` | Community board |
| `/classifieds` | Browse, post, renew, close, matches |
| `/receipts` | Record and confirm trades |
| `/identity` | This device's key + the machine's identity; profile; member cards |
| `/bulletin` | Printable sheet for the physical corkboard |
| `/.well-known/nostr.json` | NIP-05 verification |

Phones that have created a key on `/welcome` sign their own posts; the
private key stays in that browser. Posting as the machine's own identity
(and `/food`) works only on the machine itself, unless `hub: {trust_lan: true}`
is set in `config/install.local.yaml` for a household network.

## Config files cheat sheet

| Path | What | Edit by hand? |
|---|---|---|
| `config/catalog.yaml` | Catalog of available corpus items + models | No, the committed source |
| `config/presets/*.yaml` | Named install profiles | No, committed |
| `config/install.yaml` | Which preset is the default | Rarely |
| `config/install.local.yaml` | **Your** install picks | Yes (or via `pergamon setup`) |
| `services/catalog.yaml` | Available services | No, committed |
| `config/ledgers/*.yaml` | Ledger schemas | Yes — add new ledgers here |
| `rag/config.yaml` | RAG defaults (chunk size, retrieval K, etc.) | Rarely |

## Environment variables worth knowing

| Var | Default | What |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Point at a different Ollama instance |
| `ZIM_LIMIT` | unlimited | Cap articles per ZIM in `make zim-index` |
| `VW_ADMIN_TOKEN` | unset | Vaultwarden admin panel token at service startup |
| `MESHTASTIC_PORT` | auto-detect | Override USB device for `pergamon mesh listen` |
| `DRY_RUN=1` | — | `scripts/fetch-corpus.sh` prints the plan but doesn't fetch |
