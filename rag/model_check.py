"""Check that Ollama model tags exist in the registry, without pulling them.

Used by install test runs (scripts/test-install.sh): proves the names in the
catalog still resolve and reports each model's download size.

    python -m rag.model_check qwen3.5:9b nomic-embed-text
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from rag.sandbox import record

REGISTRY = "https://registry.ollama.ai/v2"
ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"


def manifest_url(tag: str) -> str:
    name, _, version = tag.partition(":")
    if "/" not in name:
        name = f"library/{name}"
    return f"{REGISTRY}/{name}/manifests/{version or 'latest'}"


def download_bytes(manifest: dict) -> int:
    return sum(int(layer.get("size", 0)) for layer in manifest.get("layers", []))


def check(tag: str, opener=urllib.request.urlopen) -> tuple[bool, str]:
    req = urllib.request.Request(manifest_url(tag), headers={"Accept": ACCEPT})
    try:
        with opener(req, timeout=30) as r:
            size = download_bytes(json.load(r))
    except urllib.error.HTTPError as e:
        return False, f"not in the registry (HTTP {e.code})" if e.code == 404 else f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return False, f"registry unreachable: {e}"
    return True, f"{size / 1e9:.1f} GB download"


def main(tags: list[str]) -> int:
    failed = 0
    for tag in tags:
        ok, detail = check(tag)
        print(f"  [{' ok ' if ok else 'err '}] {tag}: {detail}")
        record("PASS" if ok else "FAIL", "model", tag, detail)
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
