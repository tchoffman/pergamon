"""Where Pergamon keeps its library: downloads (corpus/) and the search index
(chroma/). By default that's build/ inside the repo; an install can move it to
a bigger disk, such as an external drive:

    # config/install.local.yaml
    storage:
      data_dir: /Volumes/Pergamon/pergamon

The program and its Python environment stay in the repo; only data moves.
If the drive isn't connected, Pergamon stops with a clear message rather than
writing to the laptop's own disk at the same path.

    pergamon storage                        # where the library is, and free space
    pergamon storage drives                 # disks it could use
    pergamon storage use /Volumes/X/pergamon [--move]   # switch (and copy what you have)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from rag.config import REPO_ROOT

DEFAULT = REPO_ROOT / "build"
SUBDIRS = ("corpus", "chroma")


class StorageMissing(RuntimeError):
    pass


def configured() -> Path | None:
    """The data_dir set in the install config, if any."""
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    v = ((local.get("storage") or {}).get("data_dir") or "").strip()
    return Path(os.path.expanduser(v)) if v else None


def data_dir(check: bool = True) -> Path:
    """The folder holding corpus/ and chroma/. An install test run's sandbox
    wins; then the configured folder; then build/."""
    sb = os.environ.get("PERGAMON_SANDBOX")
    if sb:
        return Path(sb).resolve()
    d = configured()
    if d is None:
        return DEFAULT
    if check and not d.is_dir():
        raise StorageMissing(
            f"Pergamon's library folder {d} isn't there. If it's on an external drive, "
            "plug the drive in. To use this computer's disk instead, remove storage.data_dir "
            "from config/install.local.yaml.")
    return d


def chroma_client(cfg: dict):
    """Open the search index, refusing if its data drive is unplugged (Chroma
    would otherwise create an empty index on the laptop's disk at that path)."""
    import chromadb
    if Path(cfg["chroma_dir"]).resolve().is_relative_to(data_dir(check=False).resolve()):
        data_dir()  # raises StorageMissing when the configured folder is gone
    return chromadb.PersistentClient(path=cfg["chroma_dir"])


def filesystem(path: Path) -> str:
    """The filesystem type of the disk holding `path` ('apfs', 'exfat', 'msdos', ...)."""
    path = path.resolve()
    best, fstype = "", ""
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        m = re.match(r".+? on (.+?) \(([^,)]+)", line) or re.match(r".+? on (.+?) type (\S+)", line)
        if m and (str(path) + "/").startswith(m.group(1).rstrip("/") + "/") and len(m.group(1)) > len(best):
            best, fstype = m.group(1), m.group(2)
    return fstype


def warnings_for(path: Path) -> list[str]:
    fs = filesystem(path)
    if fs in ("msdos", "vfat", "fat32"):
        return ["This drive is formatted FAT32, which can't hold files over 4 GB (full Wikipedia "
                "is ~118 GB). Reformat it as exFAT or APFS before using it for Pergamon."]
    return []


def drives() -> list[dict]:
    """Writable disks: this computer plus anything mounted under /Volumes or /media."""
    out = [{"path": DEFAULT, "label": "This computer", "free": shutil.disk_usage(REPO_ROOT).free}]
    roots = [Path("/Volumes"), Path("/media") / os.environ.get("USER", ""), Path("/mnt")]
    boot = os.stat("/").st_dev
    for root in roots:
        if not root.is_dir():
            continue
        for vol in sorted(root.iterdir()):
            try:
                if not vol.is_dir() or os.stat(vol).st_dev == boot or not os.access(vol, os.W_OK):
                    continue
                out.append({"path": vol / "pergamon", "label": vol.name,
                            "free": shutil.disk_usage(vol).free})
            except OSError:
                continue
    return out


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def _size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def use(target: Path, move: bool = False) -> int:
    """Point the install at `target`, copying the current library there first if asked."""
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    import yaml

    target = Path(os.path.expanduser(str(target))).resolve()
    if not target.parent.is_dir():
        print(f"{target.parent} doesn't exist. Is the drive plugged in?", file=sys.stderr)
        return 1
    for w in warnings_for(target.parent):
        print("Warning:", w, file=sys.stderr)
        return 1
    current = data_dir(check=False)
    target.mkdir(exist_ok=True)
    if move and current.resolve() != target:
        need = sum(_size(current / s) for s in SUBDIRS)
        free = shutil.disk_usage(target).free
        if need > free:
            print(f"Not enough room: the library is {_gb(need)}, {target} has {_gb(free)} free.",
                  file=sys.stderr)
            return 1
        for sub in SUBDIRS:
            if (current / sub).exists():
                print(f"Copying {current / sub} → {target / sub} ({_gb(_size(current / sub))})…")
                subprocess.run(["rsync", "-a", f"{current / sub}/", f"{target / sub}/"], check=True)
    local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    if target == DEFAULT.resolve():
        (local.get("storage") or {}).pop("data_dir", None)
        if not local.get("storage"):
            local.pop("storage", None)
    else:
        local.setdefault("storage", {})["data_dir"] = str(target)
    INSTALL_LOCAL_PATH.write_text(yaml.safe_dump(local, sort_keys=False))
    print(f"Pergamon's library now lives in {target}.")
    if move and current.resolve() != target:
        print(f"The old copy is still in {current} (corpus/, chroma/). Check that things work, "
              "then delete it to free the space.")
    print("Restart `pergamon web` and the radio listener so they use it.")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "show"
    if cmd == "path":  # for shell scripts
        try:
            print(data_dir())
            return 0
        except StorageMissing as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if cmd == "show":
        d = data_dir(check=False)
        print(f"Library: {d}{'' if d.is_dir() else '  (NOT CONNECTED)'}")
        if d.is_dir():
            for sub in SUBDIRS:
                print(f"  {sub + '/':<8} {_gb(_size(d / sub))}")
            print(f"  free     {_gb(shutil.disk_usage(d).free)}")
            for w in warnings_for(d):
                print("  Warning:", w)
        return 0
    if cmd == "drives":
        for dr in drives():
            print(f"  {dr['label']:<24} {_gb(dr['free']):>10} free   {dr['path']}")
        return 0
    if cmd == "use" and len(argv) >= 2:
        return use(Path(argv[1]), move="--move" in argv[2:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
