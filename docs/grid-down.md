# Running a community hub when the grid is down

How to turn one Pergamon machine into a neighborhood's "internet": a local
WiFi network where anyone with a phone can read and post local news, trade,
and look things up — plus paper for people without phones, and a courier
route to carry everything to the next town.

Everything here uses features that are built and tested. Where a step
depends on hardware or a router setting that hasn't been tried end to end,
it says so.

## What people get

| Need | How it works at the hub | Without a phone |
|---|---|---|
| Local news, notices, lost & found | `/board` | Printed bulletin on the corkboard |
| Trading | `/classifieds` (offers + wanted, 14-day expiry) | Bulletin lists both; drop-box slip quoting the *ref* code |
| Who can fix / teach / deliver | `pergamon skills` listings | Bulletin "Skills & services" section |
| Borrow a tool or book | `pergamon library find` | Ask the host |
| "Is this person reliable?" | Reputation line on every listing | Host reads it out |
| Reference questions | Ask the library (offline LLM + wikis) | Host asks for you |
| Hear from the next town | `pergamon sync` by courier or radio link | Next printed bulletin |

## 1. The hardware

Minimum:

- The Pergamon machine (a Mac mini or laptop; see [hardware.md](hardware.md))
- Any WiFi router. It does **not** need an internet connection — it only
  hands out addresses and carries traffic between phones and the Pergamon.
- Power for both. A Mac mini (~10–30 W) plus a small router (~5–10 W) runs
  a full day on well under 1 kWh.
- A printer (optional but strongly recommended) and a corkboard + drop box.

## 2. Network setup

1. Power the router. Give it a clear network name — e.g. `Community-Pergamon`
   — and either no password or one printed on the bulletin.
2. Connect the Pergamon to the router (Ethernet is most reliable).
3. In the router settings, give the Pergamon a **fixed address** (a "DHCP
   reservation"), so the URL on the bulletin never changes.
4. Start the services and web UI:

   ```bash
   pergamon services up nostr-relay   # needs Docker; stores posts + listings
   pergamon web                       # prints the URLs phones can use
   ```

   `pergamon web` prints something like `on your network: http://192.168.1.20:8090/`.
   That URL is also printed as a QR code on the bulletin.

### Optional: make phones open Pergamon automatically (captive portal)

Pergamon answers the "am I online?" checks that phones make when they join
a WiFi network, and redirects them to a welcome page. For this to trigger,
two router settings are needed:

1. **Point all DNS at the Pergamon.** On OpenWrt, add to dnsmasq:
   `address=/#/192.168.1.20` (use your Pergamon's fixed address). Many
   stock routers can't do this; OpenWrt or a Raspberry Pi running dnsmasq
   can.
2. **Port 80 must reach the web UI.** Either run `pergamon web --port 80`
   (on Linux this needs `sudo` or `setcap`; recent macOS allows it
   unprivileged) or have the router forward port 80 to 8090.

*Not yet tested end to end on real routers* — the redirect logic is tested,
the router side isn't. Without this step everything still works; people
just scan the QR code or type the URL.

## 3. Identities for the community

- **Phone users** create a key on their own phone from the welcome page
  ("This phone's key"). The key lives in that phone's browser storage and
  signs their posts on the phone; the hub only relays them. They should
  save the backup text the page shows — clearing browser data deletes the
  key.
- **Paper-only members**: the host creates an identity for them at the
  terminal (`pergamon identity create <name>`), switches to it
  (`pergamon identity use <name>`) when posting their slips, and prints them
  member cards (`pergamon identity card`). These private keys live on the
  hub, so the hub operator must be trusted.
- Anyone who wants full control runs their own Pergamon and syncs with the
  hub.

The terminal's "active" identity is what the web UI uses when a browser has
no key of its own — so on a shared hub, set the active identity to the
host's own, not a member's.

## 4. The daily routine (host)

1. **Morning:** empty the drop box. For each slip, post it on the person's
   behalf — board posts (`/board`), listings (`/classifieds`), or
   confirmations ("still available" → **Renew** on their listing).
2. **Print the bulletin:** open `/bulletin` and print. It shows live offers,
   wanted posts, skills, and the last 14 days of board posts, each with a
   *ref* code, plus the WiFi QR code. Replace yesterday's copy.
3. **Evening:** check `pergamon library loans --overdue` if you're also
   lending.

Listings expire after 14 days unless renewed. That's deliberate — it keeps
the bulletin honest. Remind people to renew what's still available.

## 5. Connecting towns

Each hub's relay only knows what was posted at that hub. To share, events
have to travel. Every post is signed, so nothing can be forged or edited in
transit; duplicates are ignored, so syncing twice is harmless.

**When the two hubs can reach each other** (a long-range WiFi link, both on
the same network for an afternoon, a laptop someone brings over):

```bash
pergamon sync both ws://<other-hub-address>:8091/
```

**Courier (sneakernet)** — the most reliable long-distance link there is:

```bash
# At hub A, before the courier leaves:
pergamon sync export --path /Volumes/USB/hubA-2027-03-01.jsonl

# At hub B, when the courier arrives:
pergamon sync import /Volumes/USB/hubA-2027-03-01.jsonl
pergamon sync export --path /Volumes/USB/hubB-2027-03-02.jsonl   # send B's news back
```

A weekly courier between neighboring towns is enough to keep everyone's
board, listings, skills, library catalogs, and trade reputation in step.
A bundle covers the last 60 days by default (`--days N` to change).

What travels: board posts, classifieds, trade receipts, library catalogs,
skills, profiles. What doesn't: direct messages, the ask/LLM history, and
the corpus itself (ZIMs are too big — copy those separately if needed).

## 6. Radio

- **Meshtastic** (LoRa, a few km per hop): `pergamon mesh listen` answers
  text questions from the library over the mesh. Good for "what's the dose
  of X" from someone out in the field, not for moving the board around.
  See [modules/comms/meshtastic/README.md](../modules/comms/meshtastic/README.md).
  The real-radio path is not yet verified on hardware.
- Bulk data over LoRa is not practical (a 1 MB file takes most of an hour).
  Use the courier for anything bigger than a few messages.

## 7. Safety notes for hosts

- Board posts are signed and attributable but **not moderated**. The
  "warning" category exists for people to tell neighbors about real
  problems; it is also the easiest category to abuse. Use judgment about
  what you print.
- Foraging, medical, and electrical questions get extra safety rules and a
  warning banner. Never let the library be the only check before someone
  eats a wild mushroom.
- The hub machine holds the community's private keys. Keep it physically
  secure, and keep a backup of `build/identity/` somewhere safe.
