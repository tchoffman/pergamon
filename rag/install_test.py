"""Helpers for scripts/test-install.sh: plan the test configs, prove the
library pipeline end to end, and report.

    python -m rag.install_test rounds <out-dir>   # configs covering the whole catalog
    python -m rag.install_test pipeline           # index a sample, then search it
    python -m rag.install_test report             # summary of results.tsv; exit 1 on failures

Runs only inside a sandbox (PERGAMON_SANDBOX), so nothing real is touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml

from rag.config import REPO_ROOT
from rag.install_config import load_catalog, load_model_catalog, load_services_catalog
from rag.sandbox import record, sandbox

# A preset name with no file: the effective config is exactly the overrides.
EMPTY_PRESET = "_none"
# Free-text options need an example value to test with.
SAMPLE_OPTIONS = {"osm-regional": {"regions": ["us-georgia"]}}


def _variants(item: dict) -> list[dict]:
    """Each distinct option set worth testing for one catalog item."""
    iid = item["id"]
    if iid in SAMPLE_OPTIONS:
        return [SAMPLE_OPTIONS[iid]]
    prefixes = (item.get("kiwix") or {}).get("prefix_by_variant") or {}
    if len(prefixes) > 1:
        return [{"variant": v} for v in prefixes]
    for opt in item.get("options") or []:
        choices = [c.get("value") if isinstance(c, dict) else c for c in opt.get("choices") or []]
        if isinstance((item.get("defaults") or {}).get(opt["name"]), list) and choices:
            return [{opt["name"]: choices}]  # multi-select: every choice at once
    return [{}]


def rounds(catalog: dict | None = None, models: dict | None = None,
           services: dict | None = None) -> list[dict]:
    """install.local.yaml contents that, run one after another, touch every
    item, every variant, every model and every service once."""
    catalog = catalog if catalog is not None else load_catalog()
    models = models if models is not None else load_model_catalog()
    services = services if services is not None else load_services_catalog()
    per_item = {iid: _variants(it) for iid, it in catalog.items()}
    primaries, embeddings = list(models["primary"]), list(models["embedding"])
    n = max([len(v) for v in per_item.values()] + [len(primaries), len(embeddings), 1])
    out = []
    for i in range(n):
        cfg: dict = {"preset": EMPTY_PRESET,
                     "overrides": {iid: v[i] for iid, v in per_item.items() if i < len(v)}}
        cfg["models"] = {"primary": primaries[min(i, len(primaries) - 1)],
                         "embedding": embeddings[min(i, len(embeddings) - 1)]}
        cfg["services"] = {sid: {} for sid in services} if i == 0 else {}
        out.append(cfg)
    return out


# ───────────────────────────── pipeline probe ─────────────────────────────

def _installed_models(host: str) -> set[str]:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            names = {m["name"] for m in json.load(r).get("models", [])}
    except OSError:
        return set()
    return names | {n.removesuffix(":latest") for n in names}


def pipeline() -> int:
    """Index a small sample (downloaded books plus a tiny real ZIM) into the
    sandbox's own index, then search it. Uses an embedding model already on
    this machine: test runs never download models."""
    from rag.config import load_config
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml

    sb = sandbox()
    cfg = load_config()
    installed = _installed_models(cfg["ollama_host"])
    if not installed:
        record("SKIP", "index", "pipeline", "Ollama isn't running, so nothing could be indexed")
        return 0
    chosen = cfg["embedding_model"]
    if chosen not in installed:
        fallback = next((m["id"] for m in load_model_catalog()["embedding"].values()
                         if m["ollama_tag"] in installed), None)
        if not fallback:
            record("SKIP", "index", "pipeline", f"no embedding model installed (wanted {chosen})")
            return 0
        local = load_yaml(INSTALL_LOCAL_PATH)
        local.setdefault("models", {})["embedding"] = fallback
        INSTALL_LOCAL_PATH.write_text(yaml.safe_dump(local, sort_keys=False))
        record("SKIP", "index", f"embedding {chosen}",
               f"not installed here; indexed with {fallback} instead (test runs don't pull models)")

    env = {**os.environ, "INGEST_MAX_CHUNKS": "5", "ZIM_LIMIT": "10"}
    py = str(REPO_ROOT / "build" / "venv" / "bin" / "python")
    steps = [("books and PDFs", [py, "-m", "rag.ingest"])]
    probe = sb / "corpus" / "zims" / "_probe.zim"
    if probe.exists():
        steps.append(("ZIM", [py, "-m", "rag.zim_ingest", str(probe)]))
    else:
        record("FAIL", "index", "ZIM", "the sample ZIM didn't download")
    for label, cmd in steps:
        r = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
        lines = (r.stdout + r.stderr).strip().splitlines()
        summary = [ln for ln in lines if "Added" in ln or "No PDFs or books" in ln]
        tail = ((summary or lines)[-1:] or [""])[0].strip()[:200]
        if r.returncode == 0 and "No PDFs or books" in tail:
            record("SKIP", "index", label, "none downloaded in this run, so nothing to index")
        else:
            record("PASS" if r.returncode == 0 else "FAIL", "index", label, tail)

    from rag.query import retrieve
    cfg = load_config()
    for q, label in (("AngularJS directive", "ZIM"), ("chapter", "books")):
        try:
            hits = retrieve(cfg, q)
        except Exception as e:  # noqa: BLE001 — reported, not raised
            record("FAIL", "search", label, repr(e)[:200])
            continue
        record("PASS" if hits else "FAIL", "search", f"'{q}'",
               f"{len(hits)} passages from the test index" if hits else "nothing found")
    return 0


# ───────────────────────────────── report ─────────────────────────────────

def report() -> int:
    path = sandbox() / "results.tsv"
    rows: list[list[str]] = []
    seen = set()
    for line in path.read_text().splitlines() if path.exists() else []:
        parts = (line.split("\t") + ["", "", "", ""])[:4]
        if tuple(parts) not in seen:
            seen.add(tuple(parts))
            rows.append(parts)
    if not rows:
        print("No results recorded.")
        return 1
    order = {"FAIL": 0, "SKIP": 1, "PASS": 2}
    rows.sort(key=lambda r: (order.get(r[0], 3), r[1], r[2]))
    mark = {"PASS": "\033[32m PASS \033[0m", "FAIL": "\033[31m FAIL \033[0m", "SKIP": "\033[33m SKIP \033[0m"}
    width = max(len(r[2]) for r in rows)
    print("\n==> Install test results")
    for status, step, item, detail in rows:
        print(f"  [{mark.get(status, status)}] {step:<9} {item:<{width}}  {detail}")
    counts = {s: sum(r[0] == s for r in rows) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n  {counts['PASS']} passed · {counts['FAIL']} failed · {counts['SKIP']} skipped")
    return 1 if counts["FAIL"] else 0


def main(argv: list[str]) -> int:
    if not sandbox():
        print("Run this through scripts/test-install.sh (it needs PERGAMON_SANDBOX).", file=sys.stderr)
        return 2
    cmd = argv[0] if argv else "report"
    if cmd == "rounds":
        out = Path(argv[1])
        out.mkdir(parents=True, exist_ok=True)
        for i, cfg in enumerate(rounds()):
            p = out / f"round-{i + 1}.yaml"
            p.write_text(yaml.safe_dump(cfg, sort_keys=False))
            print(p)
        return 0
    if cmd == "pipeline":
        return pipeline()
    if cmd == "report":
        return report()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
