"""Run the web app: plain HTTP always, plus HTTPS when hub.https is on.

HTTP stays up because phones probe it to detect a captive portal (that's
what pops the welcome page on join) and because a phone has to fetch the
hub's root certificate before it can trust HTTPS.

    python -m rag.serve [--host 0.0.0.0] [--port 8090]

Config (config/install.local.yaml):

    hub:
      https: true
      https_port: 8443
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn

DEFAULT_HTTPS_PORT = 8443


def hub_https_settings() -> tuple[bool, int]:
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    hub = local.get("hub") or {}
    return bool(hub.get("https")), int(hub.get("https_port") or DEFAULT_HTTPS_PORT)


async def _serve(host: str, port: int, https: bool, https_port: int, relay_port: int | None = None) -> None:
    servers = [uvicorn.Server(uvicorn.Config("rag.web:app", host=host, port=port))]
    extra = []
    if relay_port:
        from rag.relay import Relay
        extra.append(Relay().serve(host=host, port=relay_port))
    if https:
        from rag.tls import ensure_server_cert
        cert = ensure_server_cert()
        print(f"  HTTPS certificate covers: {', '.join(cert.names)}")
        servers.append(uvicorn.Server(uvicorn.Config(
            "rag.web:app", host=host, port=https_port,
            ssl_certfile=str(cert.cert_path), ssl_keyfile=str(cert.key_path))))
    await asyncio.gather(*(s.serve() for s in servers), *extra)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pergamon web")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args(argv)

    from rag.cards import lan_urls
    https, https_port = hub_https_settings()
    print(f"Pergamon web UI → http://localhost:{args.port}")
    for u in lan_urls(args.port):
        print(f"  on your network: {u}")
    if https:
        for u in lan_urls(https_port):
            print(f"  secure:          {u.replace('http://', 'https://')}")
        print(f"  phones set up the secure connection at http://<this hub>:{args.port}/trust")
        from rag.tls import ca_fingerprint
        print(f"  certificate fingerprint (phones compare this): {ca_fingerprint()}")
    # The community relay (board, listings, messages): built in, unless the
    # configured relay is elsewhere or something (e.g. a Docker relay) already serves it.
    from rag.relay import local_port, port_in_use
    relay_port = local_port()
    if relay_port and port_in_use(relay_port):
        print(f"  relay: using the one already running on port {relay_port}")
        relay_port = None
    elif relay_port:
        from rag.relay import Relay
        print(f"  relay: built in, ws://<this hub>:{relay_port}/ ({Relay().store.count()} events stored)")
    try:
        asyncio.run(_serve(args.host, args.port, https, https_port, relay_port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
