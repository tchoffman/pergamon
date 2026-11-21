"""The Entertainment pack: films, audiobooks and music people may share.

Items come from config/entertainment/pack.yaml (built by
scripts/curate-entertainment.py from the Internet Archive). Each item's details
(title, year, source, the license the archive lists) are saved next to its
files in info.json.

    pergamon media install sampler|full     # ~1.1 GB or ~3.3 GB
    pergamon media list
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from rag.config import REPO_ROOT

PACK_PATH = REPO_ROOT / "config" / "entertainment" / "pack.yaml"
UA = {"User-Agent": "Pergamon-media"}
KINDS = {"film": "Films", "audiobook": "Audiobooks", "music": "Music"}
REDISTRIBUTABLE = re.compile(r"creativecommons\.org/(licenses/(publicdomain|by(-sa|-nc|-nd|-nc-sa|-nc-nd)?)/|"
                             r"publicdomain/(mark|zero)/)")


class MediaError(RuntimeError):
    pass


def media_dir() -> Path:
    from rag.storage import data_dir
    return data_dir() / "media"


def pack(variant: str = "full") -> list[dict]:
    items = yaml.safe_load(PACK_PATH.read_text())["items"]
    if variant == "sampler":
        items = [i for i in items if i.get("sampler")]
    elif variant != "full":
        raise MediaError("the pack is 'sampler' or 'full'")
    return items


def pack_bytes(variant: str) -> int:
    return sum(f["bytes"] for i in pack(variant) for f in i["files"])


def _metadata(ident: str) -> dict:
    url = f"https://archive.org/metadata/{urllib.parse.quote(ident)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r).get("metadata") or {}


def check_license(item: dict, metadata=_metadata) -> str:
    """Whatever license the archive lists for the item now (noted, not enforced)."""
    try:
        return metadata(item["id"]).get("licenseurl") or ""
    except OSError:
        return item.get("license_url") or ""


def file_url(item: dict, name: str) -> str:
    return f"https://archive.org/download/{urllib.parse.quote(item['id'])}/{urllib.parse.quote(name)}"


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)


def install_item(item: dict, metadata=_metadata, download=_download, log=print) -> int:
    """Download one item (skipping files already complete). Returns bytes added."""
    lic = check_license(item, metadata)
    folder = media_dir() / item["id"]
    folder.mkdir(parents=True, exist_ok=True)
    added = 0
    for f in item["files"]:
        dest = folder / f["name"]
        if dest.exists() and (not f["bytes"] or dest.stat().st_size == f["bytes"]):
            continue
        log(f"  {item['title']}: {f['name']} ({f['bytes'] / 1e6:.0f} MB)")
        download(file_url(item, f["name"]), dest)
        added += dest.stat().st_size
    (folder / "info.json").write_text(json.dumps({
        k: item[k] for k in ("id", "kind", "title", "creator", "year", "license", "source")
    } | {"license_url": lic or item.get("license_url", ""), "files": item["files"]}, indent=1, ensure_ascii=False))
    return added


def install(variant: str, log=print, **kw) -> int:
    total = 0
    for item in pack(variant):
        total += install_item(item, log=log, **kw)
    return total


# ───────────────────────────── the library ─────────────────────────────

def check_reachable(variant: str, head=None) -> list[tuple[str, bool, str]]:
    """For install test runs: each item's first file answers with the expected
    size, without downloading the pack."""
    def default_head(url):
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get("Content-Length") or 0)
    head = head or default_head
    out = []
    for i in pack(variant):
        f = i["files"][0]
        try:
            size = head(file_url(i, f["name"]))
            ok = not f["bytes"] or size == f["bytes"]
            out.append((i["title"], ok, f"{size / 1e6:.0f} MB" if ok else f"size {size} != {f['bytes']}"))
        except OSError as e:
            out.append((i["title"], False, str(e)[:120]))
    return out


def library() -> list[dict]:
    """Installed items (those with every file present), grouped later by kind."""
    out = []
    root = media_dir()
    for lic in sorted(root.glob("*/info.json")) if root.exists() else []:
        try:
            info = json.loads(lic.read_text())
        except (OSError, ValueError):
            continue
        files = [f for f in info["files"] if (lic.parent / f["name"]).exists()]
        if len(files) == len(info["files"]) and files:
            out.append({**info, "files": files})
    return out


def item(ident: str) -> dict | None:
    return next((i for i in library() if i["id"] == ident), None)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    try:
        if cmd == "check":
            variant = argv[1] if len(argv) > 1 else "sampler"
            bad = 0
            for title, ok, detail in check_reachable(variant):
                print(f"{'PASS' if ok else 'FAIL'}\t{title}\t{detail}")
                bad += not ok
            return 1 if bad else 0
        if cmd == "install":
            variant = argv[1] if len(argv) > 1 else "sampler"
            print(f"Installing the {variant} pack (~{pack_bytes(variant) / 1e9:.1f} GB)…")
            added = install(variant)
            print(f"Done: {added / 1e9:.2f} GB added in {media_dir()}")
            return 0
        if cmd == "list":
            lib = library()
            if not lib:
                print("No entertainment installed. Run: pergamon media install sampler   (or full)")
                return 1
            for i in lib:
                print(f"  {KINDS[i['kind']]:<11} {i['title']:<40} {i['license']}")
            return 0
    except MediaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
