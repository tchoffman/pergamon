# Getting started with Pergamon

A step-by-step walkthrough for your first install. Assumes you've never run
Pergamon before. Takes about 30 minutes end-to-end on a modern Mac, most of
which is downloads running in the background.

## What Pergamon is

An **offline reference machine** that combines:

- **A library** of wikis and books (Wikipedia, Wikivoyage, iFixit, survival
  manuals) you can browse without any internet.
- **A local LLM** (via [Ollama](https://ollama.com)) that answers questions
  by citing that library.
- **A set of services** you can optionally run alongside — calendar, ebook
  server, password vault, a Nostr relay for talking to other Pergamons.
- **A Nostr identity** so you can post classifieds, send DMs, and build
  reputation across other people's Pergamons (even over a mesh radio).

Everything runs on your own computer. Nothing leaves the LAN unless you
explicitly connect it to the wider Nostr network.

## Before you begin

You need:

- A **Mac** (Apple Silicon recommended). Linux works but the walkthrough
  hasn't been battle-tested there yet.
- **Homebrew** installed. If you don't have it: https://brew.sh.
- **Git** and **Python 3.11+** (both come free on modern macOS). Check with
  `python3 --version`.
- **Docker Desktop** — *only* if you want to run the services (calendar,
  ebook library, Nostr relay, password vault). You can skip Docker and still
  use the Pergamon library + ask/plan/search.
- **Disk space:**
  - Minimum install: ~2 GB
  - Default install: ~5 GB (fits a 16 GB Mac comfortably)
  - Comprehensive install: ~80 GB (for a dedicated appliance)
- **About 30 minutes** of attended time.

## Step 1 — Clone and set up

```bash
mkdir -p ~/github
cd ~/github
git clone https://github.com/tchoffman/pergamon.git
cd pergamon
```

## Step 2 — Pick what to install (interactive)

```bash
make setup
```

This walks you through:

1. **Pick a preset**: minimal, default, comprehensive, or custom.
2. **Model**: which LLM to use as the primary. Default is `qwen3.5-9b`
   (~5 GB, comfortable on a 16 GB Mac). On a bigger machine, pick `qwen2.5-14b-q4`
   for sharper answers.
3. **Embedding model**: how retrieval works. The default (`nomic-embed-text`)
   is fine for most people.
4. **Services** (optional): calendar, ebook library, password vault, Nostr
   relay. Pick none if you're only trying out the core RAG features.

This writes `config/install.local.yaml` with your picks. It's gitignored, so
it's yours alone and won't follow the repo around.

**Don't want to run setup?** Skip it. Everything below uses the `default`
preset, which is fine.

**Short on disk space?** Keep the library on an external drive. Setup asks
where the library should live and shows each disk's free space. Or do it
yourself:

```bash
bin/pergamon storage                                   # where it is now, and sizes
bin/pergamon storage drives                            # disks you could use
bin/pergamon storage use /Volumes/MyDrive/pergamon --move   # copy it over and switch
```

Downloads and the search index move; the program stays put. The old copy
is kept until you delete it. If the drive is unplugged, installs stop with a
message, and answers still work, just without the library. Format the drive
exFAT or APFS: FAT32 can't hold files over 4 GB.

**Want to test-drive the install first?** A test run does every install step
in a throwaway folder (`build/test-install/`) in a few minutes:

```bash
make test-install                     # what your config would install
make test-install ARGS=--choose       # pick in the setup menu first
make test-install ARGS=--everything   # every item, variant, model and service
```

Each download stops after its first 2 MB (`TEST_BYTES` changes that) and is
checked to be the right kind of file. Models and service images are looked
up in their registries instead of downloaded. A 1 MB library is downloaded
whole, indexed and searched to prove that part works. It ends with a
pass/fail line for every download link, model and image. Your real
`config/install.local.yaml`, downloads and index are not touched.

## Step 3 — Build the pergamon

```bash
make all
```

This runs in order:

1. **`make deps`** — installs Ollama + kiwix-serve via Homebrew (first run
   only; later runs are no-ops).
2. **`make venv`** — creates a Python environment under `build/venv`.
3. **`make models`** — pulls your chosen LLM + embedding model via Ollama.
   This is the big one — depending on model size, 3-10 GB download.
4. **`make corpus`** — downloads the ZIMs + public-domain PDFs you picked
   during setup. Default preset is ~4.7 GB; `kiwix.org` is sometimes slow
   so this may take a while.
5. **`make index`** — chunks + embeds any PDFs into the Chroma vector store
   so `pergamon ask` can cite them.

You can walk away during this. If any step fails (bad network, Ollama
hiccup), just re-run `make all` — every step is idempotent.

## Step 4 — Ask the pergamon something

```bash
bin/pergamon ask "how do I purify surface water?"
```

You should see:
- A streamed answer from the LLM
- A **Sources** section listing where the answer came from (will be slim at
  first — more on this below)

## Step 5 — Check what you have

```bash
bin/pergamon status
```

Shows a table with the Ollama binary, kiwix-serve binary, models, the
corpus files on disk, and the number of chunks in the RAG index.

## Step 6 — Index your ZIM content (optional, slow, big win)

Out of the box, your 4.7 GB of ZIM content is **browse-only**. To make
`ask` grounded in that content, run:

```bash
make zim-index
```

Expect several hours for a full iFixit ingest. To smoke-test first:

```bash
ZIM_LIMIT=50 make zim-index
```

That ingests 50 articles per ZIM so you can verify the pipeline works
before committing to the long run.

After indexing, re-ask: `bin/pergamon ask "fix my refrigerator"` — the
answer should cite actual iFixit articles with clickable kiwix URLs.

## Step 7 — Browse the library

The `kiwix-serve` binary may not have finished downloading (its CDN is
unreliable); if so, `make deps` falls back to installing **Kiwix.app** (the
macOS GUI).

```bash
bin/pergamon serve
```

- If the headless binary is there: opens a browser at `http://localhost:8080`
  with search across all your ZIMs.
- If only the GUI is there: launches Kiwix.app with your ZIMs preloaded.

Either way you can type a term and search across every indexed article.

## Step 8 — Use the web UI

```bash
make web
```

Opens Pergamon at `http://localhost:8090`: a home screen of apps (Ask,
Library, Board, Classifieds, Skills, Trades, Household, Me, Bulletin, Forms).
Every app has the same bar at the top; tap **‹ Pergamon** to go back home.

- **Ask** (`/chat`) has three modes:
  - *Ask* — a conversational answer; follow-up questions keep the context
  - *Plan* — structured plan output (Goal / Steps / Resources / Risks /
    Verification)
  - *Search* — top-K raw retrieval hits, no LLM involved
- **Library** (`/library`) searches the indexed library and opens each
  collection in the reader.
- **Household** (the food inventory) only appears on the hub itself, unless
  `food: {lan_access: true}` is set.
- Phone-friendly — `make web` prints the address phones on the same WiFi
  can use (e.g. `http://192.168.1.20:8090/`). Send phones to `/welcome`.
- Installable — on a phone, *Add to Home Screen* (iPhone) or *Install app*
  (Android) gives a Pergamon icon that opens full-screen. Android needs the
  secure connection first (`hub: {https: true}`, then `/trust` on the phone).
  If the phone later can't reach the hub, the installed app shows a short
  "can't reach the hub" page instead of a browser error. Icons are drawn by
  `scripts/make-icons.py`.
- Accounts — people sign up on Welcome or Me with a username and password,
  and sign in the same way from any phone. Underneath, each account is a
  Nostr key: the phone locks it with the password (NIP-49, `ncryptsec1…`)
  and the hub stores only that locked copy (`build/state/accounts.db`), never
  the password. *Download key file* saves the locked key to sign in at another
  hub (Me → Other ways in → Key file). Ticking *Keep my key only on this
  phone* stores no key on the hub. Wrong passwords are limited to 8 per 15
  minutes per account and per device. The crypto bundle is rebuilt with
  `scripts/build-vendor-bundle.sh` (pinned versions).
- Messages (✉️) — private messages to anyone by username or npub. Phones
  encrypt them end to end (NIP-17: NIP-44 inside a gift wrap signed by a
  throwaway key), so the hub stores sealed envelopes it can't open. Sync and
  USB bundles carry them to other hubs; the outside of each shows only who
  it's for, never who sent it, and times are blurred by up to two days. The
  inbox only opens for a fresh note signed by the recipient's key.
- Feeds — neighborhood newscasts. Feeds are the board categories (news,
  notices, events, help, lost & found, warnings) plus the morning weather.
  Radios send `/feeds`, `/sub news`, `/unsub news` (or `/unsub all`); web
  users tick boxes on the Me page and get digests as sealed messages from the
  hub in Messages. Digests go out at 07:00, 12:00 and 18:00 (set
  `feeds: {times: [...]}`) with only what's new, at most two radio messages
  per feed; warnings go out within a minute. The radio listener does the
  sending, so it must be running. `pergamon feeds` lists feeds and counts;
  `pergamon feeds preview` shows what a digest would say.
- Hubs (host only, 🔗) — this hub's code (text and QR) and the hubs it
  trusts. Each hub has its own key (`build/state/hub.key`). `pergamon sync
  pull|push|both <hub>` and `pergamon sync all` only run with trusted hubs,
  after the other hub signs a fresh challenge; anything else is refused.
  Exported bundles carry a manifest signed by this hub. Importing a bundle
  from a hub you don't trust (or an unsigned or altered one) shows what's on
  it — source, counts, top authors, newest posts — and loads nothing until
  you rerun with `--accept` (add `--trust` to trust that hub). Blocked keys
  and hidden posts are dropped from every import.
- Moderation (host only) — on the hub machine, Board, Classifieds and Skills
  cards show *Hide* and *Block author*; the 🛡️ Moderation app lists what's
  hidden and blocked, with undo. Hidden or blocked content disappears from
  every page, radio command and printed bulletin on this hub, and sync and
  USB bundles never pass it on; blocked keys can't post here. Nothing is
  deleted, and other hubs are unaffected. From the command line:
  `pergamon moderation [hide <id> | block <npub> | unhide | unblock]`. The list
  lives in `build/state/moderation.json`.

## Step 9 — Create your Nostr identity

Pergamon's cross-machine features (classifieds, distributed posts, DMs)
are built on the [Nostr protocol](https://nostr.com). Each user has a
keypair — the *private key* (`nsec1...`) is your account; the *public key*
(`npub1...`) is your handle.

```bash
bin/pergamon identity create trenton --nip05 trenton@pergamon.local --display "Trenton"
```

This:
- Generates a secp256k1 keypair
- Saves the private key at `build/identity/trenton.key` (mode 0600)
- Marks it as the active identity
- Prints your `npub` (safe to share)

**Back it up:**

```bash
bin/pergamon identity export trenton --path ~/safe/trenton.nsec
```

Put that file on an offline USB drive. If you lose it and the machine
dies, you lose the identity.

See [identity.md](identity.md) for the full command reference and recovery
procedures.

**On a phone or another computer**, you don't need the terminal: open
`/welcome`, enter a name, and tap **Create my key**. The key is created
and kept in that browser and signs that device's posts; the Pergamon
machine never sees it. Tap **Show backup key** and write it down — clearing
the browser's data deletes the key.

## Step 10 — (Optional) Start the services

If you installed Docker Desktop and picked services during `make setup`:

```bash
bin/pergamon services list        # see what's enabled
bin/pergamon services up          # start them
bin/pergamon services status      # check containers
```

What you get, per the default services pack:

| Service | URL | What |
|---|---|---|
| Calendar | http://localhost:5232 | CalDAV/CardDAV; set phones to sync here |
| Ebook library | http://localhost:8083 | Calibre-Web; Send-to-Kindle over WiFi |
| Password vault | http://localhost:8089 | Vaultwarden, Bitwarden-compatible |
| Nostr relay | ws://localhost:8091 | Point any Nostr client here |

Each has its own README under `services/<id>/README.md` with first-time
setup. **Each is LAN-only by default** — nothing is exposed to the
internet.

## Step 11 — Post your first classified

With an identity and the relay running (posts need a relay to be stored
and found):

```bash
bin/pergamon classified post \
    --title "20 lb apples" \
    --price-oz-ag 0.3 \
    --accepts silver,barter \
    -t apples -t harvest \
    "Honeycrisp, pick up in Asheville"
```

Add `--want` to post something you're looking for instead. Then:

```bash
bin/pergamon classified list         # live listings
bin/pergamon classified mine         # yours, with days left
bin/pergamon classified renew <id>   # listings expire after 14 days
bin/pergamon classified close <id>   # sold
bin/pergamon classified matches      # offers that fit your wants
```

The same is on the **Classifieds** web page, which also shows each poster's
reputation.

## Step 12 — Record a trade

After a trade, one person records it and the other confirms it. Only
confirmed trades count toward either person's reputation.

```bash
bin/pergamon receipt draft --with <their npub> --gave "0.3 oz Ag" --got "20 lb apples"
# prints a code; the other person runs:
bin/pergamon receipt confirm <code>
```

Or use the **Receipts** page: it shows a QR code the other person can scan.

## Step 13 — Talk to other Pergamons

Each Pergamon only knows what was posted to its own relay. To share:

```bash
bin/pergamon sync both ws://<other-pergamon-ip>:8091/          # when you can reach each other
bin/pergamon sync export --path /Volumes/USB/bundle.jsonl      # or carry a USB stick
bin/pergamon sync import /Volumes/USB/bundle.jsonl
```

Running a Pergamon for a whole neighborhood (WiFi, printed bulletin, drop
box, courier between towns) is covered in [grid-down.md](grid-down.md).

## Daily use

Day to day, you'll mostly use these four:

```bash
bin/pergamon ask "..."         # answers with sources
bin/pergamon plan "..."        # structured plan output
bin/pergamon search "..."      # raw retrieval, no LLM
bin/pergamon serve             # browse wikis in Kiwix
```

Or the web UI (`make web`) for the same thing in a browser you can point
phones at.

## Troubleshooting

**`make all` fails with "ollama: command not found"** → Homebrew install
failed. Run `brew install ollama` manually, then `make all` again.

**Ollama pull is incredibly slow** → not a Pergamon issue; Ollama's CDN
is occasionally regional. Try later or point `OLLAMA_HOST` at a local
mirror.

**Corpus download fails on kiwix.org** → their CDN is spotty. The fetch
script resumes partial downloads, so just re-run `make corpus`.

**`pergamon ask` cites wrong/random sources** → the RAG index is empty or
too small. Run `make zim-index` (slow) to actually ingest your ZIM
content. Until then, the LLM is answering mostly from general knowledge
and grabbing weak semantic matches for citations.

**Web UI returns 500 on /** → check `make test` passes; if you edited
templates, you may have broken Jinja. Nostr identity page is at
`/identity`; profile publish writes to the configured relays and reports
per-relay status in the JSON response.

**Docker services won't start** → make sure Docker Desktop is running
(the whale icon in the menu bar). `docker ps` should work.

## What's not working yet

Live-tracked at: https://github.com/tchoffman/pergamon/issues

Known limits as of this writing:

- The Meshtastic radio path has only run in mock mode; the real-radio path
  hasn't been verified on hardware yet (see
  [mesh-test-plan.md](mesh-test-plan.md)).
- Captive portal: Pergamon's side is tested, but no real router has been
  configured for it yet.
- The `kiwix-serve` headless binary sometimes fails to download from
  kiwix.org; fallback to Kiwix.app is automatic.
- ZIM ingestion is slow (local Ollama embedding is ~5 chunks/sec on an
  M4). Full iFixit is several hours; full Wikipedia Simple is longer.
- Services don't have a reverse proxy yet — each is at a raw port
  (issue #6). `pergamon.local` style hostnames require manual
  `/etc/hosts` entries for now.

## Where to go next

- **Daily reference**: [commands.md](commands.md) — every subcommand with
  flags and examples.
- **Running a community hub**: [grid-down.md](grid-down.md).
- **Identity details**: [identity.md](identity.md) — NIP-05, backup,
  recovery, multi-user households.
- **Hardware planning**: [hardware.md](hardware.md) — the "cage machine"
  for long-term off-grid use.
- **Printable survival cheatsheet**: [quick-reference.md](quick-reference.md) —
  water, fire, triage; print and keep with the machine.
- **Ideas backlog**: [ideas.md](ideas.md) — what's planned but not built.
- **GitHub issues**: open and close as you build and discover bugs.
