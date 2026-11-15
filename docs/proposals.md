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
- **Store-and-forward mesh replies.** Queue answers when the asker is out of range;
  resend when the node is heard again (Briar-style).
- **Post board notices from a bare handheld.** A DM like `BOARD news: bridge out`
  to the hub becomes a signed board post (host's key, with the sender's node id).
- **Raspberry Pi hub image.** Pi + hostapd + dnsmasq (DNS → pergamon) gives the
  captive portal with no router configuration.
- **Courier manifest.** `sync export` also prints a one-page summary (counts,
  newest posts) so the courier and receiving host can see what's on the stick.
- **Embedding-based matching** so "cordwood" matches "firewood".

## Paper on-ramp

- **Printable drop-box forms** (board post, listing, renew, receipt) with a QR
  slot for the member card, and a scan → post flow at the hub.
