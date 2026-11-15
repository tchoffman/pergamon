# Proposals (for review)

Ideas that came up while building, not yet agreed. Mark ✅ / ❌ / later.

## Finish soon

- **Browser keys on classifieds + receipts pages.** Board and profile already sign
  in the browser; classifieds and receipts pages still post as the hub identity.
  The server endpoints (`/api/draft/*`, `/api/publish`) are built and tested —
  only the page JavaScript is left.
- **Skills web form.** Skills are CLI + bulletin only.
- **Responsiveness signal** for reputation (needs DM read receipts).

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
