"""Printable member cards and LAN join links.

A member card carries the holder's npub as a QR code. A host scans the card
with a paper form to post on the member's behalf (via NIP-26 delegation), or
to look the member up on the board. Nothing secret is on the card — only the
public key.
"""
from __future__ import annotations

import socket
import sys
from html import escape
from pathlib import Path

import segno

from rag.identity import get_active, load_identity


def qr_svg(data: str, scale: int = 6) -> str:
    """Inline SVG (no XML declaration) for embedding in HTML."""
    return segno.make(data, error="m").svg_inline(scale=scale, dark="#000", light="#fff")


def lan_ip() -> str | None:
    """Best-guess LAN address. Uses a UDP connect, which sends no packets."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


def lan_urls(port: int) -> list[str]:
    """URLs a phone on the same network can try, most reliable first."""
    urls = []
    ip = lan_ip()
    if ip:
        urls.append(f"http://{ip}:{port}/")
    host = socket.gethostname()
    if host and not host.endswith(".local"):
        host = host.split(".")[0] + ".local"
    if host:
        urls.append(f"http://{host}:{port}/")
    return urls


def card_html(ident) -> str:
    name = escape(ident.display_name or ident.name)
    npub = escape(ident.npub)
    nip05 = escape(ident.nip05 or "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Member card — {name}</title>
<style>
  @page {{ size: letter; margin: 0.5in; }}
  body {{ font-family: Georgia, serif; color: #000; background: #fff; margin: 0; }}
  .card {{ width: 3.5in; height: 2in; border: 1px solid #000; border-radius: 0.12in;
           display: flex; align-items: center; gap: 0.12in; padding: 0.12in;
           box-sizing: border-box; break-inside: avoid; margin: 0 0.2in 0.2in 0;
           float: left; }}
  .card svg {{ width: 1.6in; height: 1.6in; flex: none; }}
  .who {{ font-size: 13pt; font-weight: bold; }}
  .label {{ font-size: 7pt; letter-spacing: 0.1em; text-transform: uppercase; }}
  .npub {{ font-family: monospace; font-size: 6.5pt; word-break: break-all; margin-top: 0.06in; }}
  .note {{ clear: both; font-family: system-ui, sans-serif; font-size: 9pt; padding-top: 0.2in; }}
  @media print {{ .note {{ display: none; }} }}
</style></head><body>
{''.join(f'''<div class="card">{qr_svg(ident.npub)}<div>
  <div class="label">Pergamon member</div><div class="who">{name}</div>
  {f'<div class="label">{nip05}</div>' if nip05 else ''}
  <div class="npub">{npub}</div></div></div>''' for _ in range(4))}
<div class="note">Four copies per sheet. Cut along the borders. The QR holds only your public
key — safe to show anyone. Keep your private key (nsec) off paper you carry.</div>
</body></html>"""


def main(argv: list[str]) -> int:
    name = None
    out = None
    i = 0
    while i < len(argv):
        if argv[i] == "--name" and i + 1 < len(argv):
            name = argv[i + 1]; i += 2
        elif argv[i] == "--path" and i + 1 < len(argv):
            out = Path(argv[i + 1]); i += 2
        else:
            print(f"unknown arg: {argv[i]}", file=sys.stderr)
            return 2
    ident = load_identity(name) if name else get_active()
    if not ident:
        print("no active identity — pergamon identity create <name>", file=sys.stderr)
        return 1
    out = out or Path(f"member-card-{ident.name}.html")
    out.write_text(card_html(ident))
    print(f"wrote {out} — open it and print (4 cards per sheet)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
