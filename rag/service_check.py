"""Check that each service's container images exist in their registries,
without Docker and without pulling. Used by install test runs.

    python -m rag.service_check radicale vaultwarden
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

from rag.config import REPO_ROOT
from rag.install_config import load_services_catalog
from rag.sandbox import record

ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])


def parse_image(ref: str) -> tuple[str, str, str]:
    """'vaultwarden/server:latest' → ('registry-1.docker.io', 'vaultwarden/server', 'latest')."""
    name, _, tag = ref.rpartition(":") if ":" in ref.split("/")[-1] else (ref, "", "latest")
    first = name.split("/")[0]
    if "." in first or ":" in first or first == "localhost":
        registry, repo = first, name.split("/", 1)[1]
    else:
        registry, repo = "registry-1.docker.io", name if "/" in name else f"library/{name}"
    return registry, repo, tag or "latest"


def images_for(service_id: str) -> list[str]:
    svc = load_services_catalog().get(service_id)
    if not svc:
        return []
    compose = REPO_ROOT / "services" / svc.get("service_dir", service_id) / "docker-compose.yml"
    data = yaml.safe_load(compose.read_text()) or {}
    return [s["image"] for s in (data.get("services") or {}).values() if s.get("image")]


def _get(url: str, headers: dict, opener):
    req = urllib.request.Request(url, headers=headers, method="GET")
    return opener(req, timeout=30)


def check_image(ref: str, opener=urllib.request.urlopen) -> tuple[bool, str]:
    registry, repo, tag = parse_image(ref)
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    headers = {"Accept": ACCEPT}
    try:
        try:
            with _get(url, headers, opener):
                return True, "image found"
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            # Anonymous pull token, as `docker pull` gets one.
            m = dict(re.findall(r'(\w+)="([^"]*)"', e.headers.get("WWW-Authenticate", "")))
            q = urllib.parse.urlencode({k: m[k] for k in ("service", "scope") if k in m})
            with _get(f"{m['realm']}?{q}", {}, opener) as r:
                tok = json.load(r)
            headers["Authorization"] = "Bearer " + (tok.get("token") or tok.get("access_token"))
            with _get(url, headers, opener):
                return True, "image found"
    except urllib.error.HTTPError as e:
        return False, "image not found" if e.code == 404 else f"registry said HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
        return False, f"registry unreachable: {e}"


def main(ids: list[str]) -> int:
    failed = 0
    for sid in ids:
        images = images_for(sid)
        if not images:
            print(f"  [err ] {sid}: no images found in its compose file")
            record("FAIL", "service", sid, "no images in compose file")
            failed += 1
            continue
        for ref in images:
            ok, detail = check_image(ref)
            print(f"  [{' ok ' if ok else 'err '}] {sid}: {ref} — {detail}")
            record("PASS" if ok else "FAIL", "service", sid, f"{ref} · {detail}")
            failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
