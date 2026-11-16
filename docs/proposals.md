# Proposals (for review)

Ideas that came up while building, not yet agreed. Mark ✅ / ❌ / later.

## Finish soon

- **Responsiveness signal** for reputation (needs DM read receipts).

- **HTTPS on the hub (local certificate).** Phone browsers only allow the camera
  over HTTPS, so barcode scanning on `/food` and photo plant/mushroom ID (#12)
  are blocked at `http://192.168.x.x`. A one-time local certificate the host
  installs on household phones would unlock both.
- **Grow the curated foraging facts.** Six danger facts now get injected
  (config/domains/foraging_facts.yaml) because live testing showed the 8B
  model's own mycology is unreliable. Worth extending with a forager's review:
  regional deadly look-alikes, and species the old guides call edible that are
  now known toxic (*Paxillus involutus*, *Tricholoma equestre*, raw elderberry).
- **Bigger model for foraging/medical questions.** On a machine with more RAM,
  route high-stakes domains to a larger model even if everyday questions stay
  on the fast one.
- **Gardening and local-growing shelf.** The library has no gardening book, so
  "what can I plant now?" retrieves a wildflower guide. Public-domain options:
  USDA Farmers' Bulletins and state extension planting calendars (Georgia's
  UGA "Vegetable Garden Calendar" is the obvious first one for this install).
  Needs a license check per source.
- **Grow the eval set from real use.** Every bad answer someone reports becomes
  a case in config/eval/questions.yaml; run `pergamon eval` before any model
  or prompt change. A "this answer was wrong" button on the web page could
  append the question to a review list for the host.
- **Start the radio listener at boot.** Docker on a Mac can't reach USB radios,
  so `pergamon mesh listen` needs a launchd agent (macOS) or systemd unit
  (Pi/Linux) to survive reboots.
- **"Verified" badge for names.** Show a check next to a poster's name when
  their NIP-05 handle resolves on a pergamon you trust, so look-alike names
  are harder to pass off.
- **Host PIN.** Host-only actions are now limited to the hub machine itself.
  A PIN would let the host do them from their own phone without opening them
  to every guest.

## Grid-down network

- **"Come sync" mesh beacon.** A pergamon broadcasts a tiny LoRa message when it
  has new events; a nearby pergamon that hears it pulls over WiFi or asks for a
  USB bundle. Mesh as the doorbell, not the pipe.
- ~~Store-and-forward~~ — built as the radio mailbox (`/msg`). Still open:
  hold *answers* the same way when the asker walks out of range mid-reply.
- **Mesh chat in the browser, served by the hub.** Meshtastic's web client
  (the app at client.meshtastic.org) is open source and can be hosted
  locally. Serving it from Pergamon would let anyone on the hub's WiFi chat
  on the mesh from a laptop or phone browser with no app install and no
  internet: connect to a radio over Bluetooth or USB (Chrome/Edge only;
  Safari lacks Web Bluetooth). Needs a check of its license and whether it
  runs fully offline once hosted.
- **Delivery receipts for radio mail.** Tell the sender "delivered to Bob"
  when a held message goes out (costs one more packet per message).
- **Mailbox at the web bulletin.** Let someone at the hub leave a `/msg` for a
  radio from the web page, and show radio mail waiting for collection.
- **Post board notices from a bare handheld.** `/post news bridge out on 5th`
  to the hub becomes a board post signed by the hub's key, tagged with the
  sender's node id. Reading the board by radio (`/news`, `/find`, `/skills`)
  is built; posting is not, because it lets anyone in radio range speak with
  the hub's identity. Needs your call: allowlisted radios only, or a host
  approval queue?
- **Raspberry Pi hub image.** Pi + hostapd + dnsmasq (DNS → pergamon) gives the
  captive portal with no router configuration.
- **Courier manifest.** `sync export` also prints a one-page summary (counts,
  newest posts) so the courier and receiving host can see what's on the stick.
- **Embedding-based matching** so "cordwood" matches "firewood".

## Paper on-ramp

- **Printable drop-box forms** (board post, listing, renew, receipt) with a QR
  slot for the member card, and a scan → post flow at the hub.
