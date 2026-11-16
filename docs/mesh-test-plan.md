# Mesh hardware test plan (two Heltec V3s)

A checklist for the first time the radios are in hand. Each test says what
to run, what "pass" looks like, and what to write down. Two setups:

- **Setup A — laptop + lone radio.** Radio 1 on the Pergamon laptop over USB;
  radio 2 standalone, paired by Bluetooth to a phone running the Meshtastic
  app. This is the realistic field case: someone with only a handheld asks
  the Pergamon a question.
- **Setup B — two Pergamons.** Each radio on its own laptop, each laptop
  running Pergamon.

Record results in a GitHub issue (one comment per test) so failures turn
into fixes.

## Results so far (2026-11-15, Setup A)

| Test | Result |
|---|---|
| 0. Bring-up | Pass. Both radios flashed with 2.7.26. Pergamon-A `!e06e2b0c`, Handheld-B `!e06fbbd0`, laptop port `/dev/cu.usbserial-0001` |
| 1. Send/receive | Pass after a key exchange (see gotcha below). Traceroute both ways ~6 dB SNR at a few feet |
| 2. Ask over mesh | Pass after a fix: only part 1 of multi-part answers arrived until parts were sent one at a time with acks. 4-part reply now arrives in ~16 s |
| Reconnect | Pass: radio reboot fires `connection.lost`; port reopens in ~5 s |

**Gotchas found on test day**

- **New radios ship with Heltec's factory test firmware** (screen says
  "LORA MODE 0"). Flash Meshtastic first.
- **Chrome's flasher tab keeps the serial port open** after flashing. Close
  it, or every CLI call fails with "Resource busy".
- **Direct messages need both radios to know each other's key.** A radio
  announces its key at boot; if the other one was off then, DMs fail with
  `PKI_UNKNOWN_PUBKEY` / `PKI_SEND_FAIL_PUBLIC_KEY` while broadcasts still
  work. Fix: send one DM from the phone app (or use "Exchange user info"), or
  reboot both radios while both are powered.

## 0. Bring-up (each radio)

1. Flash current Meshtastic firmware: https://flasher.meshtastic.org (Chrome
   or Edge, USB-C *data* cable). Pick "Heltec V3". Close the flasher tab
   afterwards — it holds the serial port.
2. Set the region — the radio will not transmit until you do:
   `meshtastic --set lora.region US` (or use the phone app).
3. Name them: `meshtastic --set-owner "Pergamon-A"` and `"Handheld-B"`.
4. Screw on the antennas **before** powering up. Transmitting without an
   antenna can damage the radio.
5. On the laptop: `pergamon mesh info`

   **Pass:** shows this radio's name and id (like `!a1b2c3d4`), region US,
   and — once both are on — the other radio in the node list.

Write down: each radio's node id, firmware version, which `/dev/cu.*` port
the laptop used.

## 1. Plain send/receive (Setup A)

1. Laptop: `pergamon mesh send !<handheld-id> "hello from pergamon"`
2. Phone app: the message shows up in the DM thread.
3. Phone app → reply "hello back" to Pergamon-A.
4. Laptop: watch `pergamon mesh listen` (next test) or the Meshtastic app on
   the laptop's radio.

**Pass:** both directions arrive. **Write down:** delay from send to arrival.

## 2. Ask the library over the mesh (Setup A) — the main event

1. Laptop: `pergamon mesh listen`
2. Phone: DM Pergamon-A: `how long should I boil water to make it safe?`
3. Laptop log shows the incoming question, then `→ N LoRa packet(s)`.
4. Phone receives `[1/N] …` through `[N/N] …`.

**Pass:** full answer arrives, in order, with the source line at the end.
**Write down:** number of packets, total time from question to last packet,
any missing or out-of-order parts.

Then:

- Ask a foraging question (`can I eat the mushrooms in my yard?`). **Pass:**
  the answer refuses to confirm edibility and mentions look-alikes.
- Send a broadcast (not a DM) on the primary channel. **Pass:** Pergamon
  ignores it — no reply floods the channel.

## 3. Allowlist (Setup A)

1. Add to `config/install.local.yaml`:

   ```yaml
   comms:
     meshtastic:
       allowed_node_ids: ["!some-other-id"]
   ```

2. Restart `pergamon mesh listen`, DM from the handheld.

**Pass:** log says `ignoring DM from unallowed node`; no reply. Then add the
handheld's id and confirm replies resume.

## 4. Range walk (Setup A)

1. Leave the laptop indoors, listening.
2. Walk away with the handheld, sending `ping` DMs every ~100 m (or every
   block). Note where replies stop.
3. Repeat with the laptop's radio near a window or raised up.

**Write down:** distance for reliable replies, distance for last reply, SNR
values from `pergamon mesh info`, terrain. Stock antenna vs. upgraded
antenna if you have one.

## 5. Two Pergamons over the mesh (Setup B)

1. Laptop A: `pergamon mesh listen`
2. Laptop B: `pergamon mesh send !<radio-A-id> "what are signs of hypothermia?"`
3. Laptop B: watch replies arrive (Meshtastic app or a second terminal with
   `meshtastic --listen`).

**Pass:** B receives A's full chunked answer.

## 6. Two Pergamons syncing over WiFi, radios as the "doorbell" (Setup B)

The mesh is too slow to move the board, but it can tell a neighbor "come
sync." This tests the full town-to-town loop with today's tools:

1. Laptop A: post to the board (`pergamon board post --category news "test from A"`).
2. Put both laptops on the same WiFi.
3. Laptop B: `pergamon sync pull ws://<laptop-A-ip>:8091/`
4. Laptop B: `pergamon board list` shows "test from A".
5. Repeat with a USB bundle instead of WiFi
   (`pergamon sync export` on A → `pergamon sync import` on B).

**Pass:** the post appears on B both ways. (Requires the nostr-relay
service running on both, so Docker must be running.)

## 7. Radio commands and mailbox (Setup A)

1. Phone → Pergamon-A: `ping` → `pong`; `help` → command list.
2. `/news`, `/find eggs`, `/skills welding` → short lists from the board,
   classifieds and skills (or "board is offline" if the relay isn't running).
3. `/msg <other radio name> hello` → "Saved for …". Power the other radio off
   and on: the message arrives after it checks in.

## Known limits

- Answers are capped near 200 characters per packet and radio answers are
  asked to stay within 4 short sentences.
- Answer parts that fail 3 delivery attempts are dropped (the log says
  where it gave up). Mail (`/msg`) is held and retried; answers are not.

## Ideas this test day could unlock

See [proposals.md](proposals.md) → mesh section: "come sync" beacons,
store-and-forward replies, and posting short board notices from a handheld.
