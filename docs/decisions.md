# Decisions

Product decisions made 2026-11-16 in a design review. Each says what was
decided, why, and what it rules out. Change one by adding a dated note
under it rather than rewriting it.

## 1. One install serves households and community hubs

A household runs Pergamon for itself; a hub is a household that opens it
to neighbors. Same install, hub features switched on.

## 2. One product: a self-contained internet in a box

Pergamon is a general-purpose assistant and a suite of everyday apps that
keep working with no internet. It is not a survival product: people sell
toys and makeup on the board and ask about anything. The library carries
extra material that is unusually useful when things go wrong, the way a
gardening calendar might stand in for restaurant reviews.

Rules out: splitting the community features into a separate project;
survival framing in prompts, examples or tests.

## 3. Apps, in build order

Built: Ask, Library, Classifieds, Board, Skills, Household (pantry,
lending, records). Next, in order:

1. Polish into one consistent app shell (home screen, shared look)
2. Messages
3. Maps
4. Entertainment
5. Local guide

## 4. Access: installable web app first

People reach Pergamon by joining the hub's WiFi. The apps are an
installable web app (home-screen icon, opens like a real app), with the
plain browser as the fallback that always works, the radio for people out
of WiFi range, and a USB installer that turns a laptop into a full node.
Native phone apps are ruled out: they must be installed before the
internet goes down. Consequence: the hub needs HTTPS.

## 5. Hardware target: a 16 GB laptop or mini-PC

That is "the" Pergamon and what testing assumes. Smaller machines (a
Raspberry Pi, old laptops) run everything except a good assistant. The
installer picks the model size from the RAM it finds.

## 6. Moderation: each host moderates their own hub

The host can hide posts and block keys; hidden content isn't shown or
passed on by sync. Control is local: hiding on one hub deletes nothing
elsewhere. Community flagging comes later.

## 7. Library: configurable at install; indexing optional

Every content item is a choice at install, with presets as defaults.
Indexing for the assistant's search is optional per item; un-indexed
items should still be searchable by keyword (Kiwix files usually carry
their own keyword search; to be verified).

## 8. Sign-in: username and password, with a key underneath

People sign in the normal way. Each account holds a key encrypted by the
password; it can be downloaded to take to another hub, or kept only on
the person's own phone. The sign-in page says plainly that a host could
see an unlocked key.

## 9. Messages: end-to-end encrypted, and they travel

Only sender and recipient can read a message. Sync and couriers carry
messages to other towns' hubs; short ones go over the radio. Uses the
current Nostr messaging standard, which hides most of who-messaged-whom.

## 10. Hub-to-hub sync: trusted hubs, plus reviewed courier sticks

Only hubs the host has added sync automatically. A USB stick from an
unknown hub can be reviewed (what's on it, from whom) and accepted once.

## 11. Marketplace prices: dollars by default

Prices are in dollars, trade is always allowed, and a host can define one
local unit (silver, town scrip, labor hours) for when dollars stop
meaning much. Prices stay sortable.

## 12. Distribution: open source (AGPL), plus optional kits

Anyone can download, run, change and sell it; modified versions run for
others must share their changes. Ready-made kits (preloaded USB
installers, hub-in-a-box) are for people who won't do the setup.

## Standing decisions from the same period

- **Answers use tool calling.** The model decides whether to search the
  library, calculate, or check community data (eval: 30/31 at ~19 s vs
  29/31 at ~43 s for always-retrieve).
- **Default model: Qwen 3.5 9B**, with thinking off.
- **The radio node stays closed.** Only allowlisted radios get answers;
  strangers get no reply and no advertising.
- **Local mesh preset: MediumFast** (matches the Cobb County mesh).
