"""Where installed data lives, and whether this is an install test run.

Normally everything goes under build/. `scripts/test-install.sh` sets
PERGAMON_SANDBOX to a throwaway folder so a test install has its own config,
downloads and index, and never touches the real ones. It also sets
PERGAMON_TEST_INSTALL=1, which tells the installer to download only the start
of each file and to check models and service images without pulling them.
"""
from __future__ import annotations

import os
from pathlib import Path

from rag.config import REPO_ROOT


def sandbox() -> Path | None:
    v = os.environ.get("PERGAMON_SANDBOX")
    return Path(v).resolve() if v else None


def test_mode() -> bool:
    return os.environ.get("PERGAMON_TEST_INSTALL") == "1"


def data_dir() -> Path:
    """The folder holding corpus/ and chroma/: build/, or the sandbox."""
    return sandbox() or REPO_ROOT / "build"


def record(status: str, step: str, item: str, detail: str = "") -> None:
    """Add a line to the test run's results (no-op outside a sandbox)."""
    sb = sandbox()
    if not sb:
        return
    clean = lambda s: str(s).replace("\t", " ").replace("\n", " ")
    with open(sb / "results.tsv", "a") as f:
        f.write("\t".join(clean(x) for x in (status, step, item, detail)) + "\n")
