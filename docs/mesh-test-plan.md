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

## 0. Bring-up (each radio)

1. Flash current Meshtastic firmware: https://flasher.meshtastic.org (Chrome
   or Edge, USB-C *data* cable). Pick "Heltec V3".
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

## Known limits going in

- Answers are capped near 200 characters per packet and the prompt asks for
  two sentences, so detailed answers will be terse.
- No store-and-forward yet: if the handheld is out of range when the reply
  goes out, those packets are lost.
- The listener has only been tested in mock mode before this.

## Ideas this test day could unlock

See [proposals.md](proposals.md) → mesh section: "come sync" beacons,
store-and-forward replies, and posting short board notices from a handheld.
