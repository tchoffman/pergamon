"""Ask the same questions of several local models and write the answers side by side.

Runs the real mesh answer path (retrieval, safety guards, short-reply rules),
so what you read is what a radio user would get.

    build/venv/bin/python scripts/compare-models.py llama3.1:8b-instruct-q4_K_M qwen3.5:9b
    # → build/eval/compare-<timestamp>.md
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.comms.meshtastic.common import ask  # noqa: E402
from rag.config import REPO_ROOT  # noqa: E402

QUESTIONS = [
    "What vegetables can I plant now?",
    "How do I purify water?",
    "How do I treat a snake bite?",
    "Is this puffball edible?",
    "Found a white mushroom with a ring on the stem under an oak. Can I eat it?",
    "How do I start a fire without matches?",
    "How long can I keep frozen meat after the power goes out?",
    "What is the capital of Nebraska?",
    "How do I safely hook a generator to my house?",
    "My neighbor has a fever and a stiff neck. What should I do?",
]


def main(models: list[str]) -> int:
    out_dir = REPO_ROOT / "build" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"compare-{datetime.now():%Y%m%d-%H%M}.md"
    lines = [f"# Model comparison ({datetime.now():%Y-%m-%d %H:%M})", ""]
    for q in QUESTIONS:
        lines += [f"## {q}", ""]
        for m in models:
            t = time.time()
            try:
                r = ask(q, model=m)
                body = r.answer + (f"\n\n_source: {r.sources[0]}_" if r.sources else "")
            except Exception as e:  # keep going; one bad model shouldn't sink the run
                body = f"ERROR: {e}"
            lines += [f"**{m}** ({time.time() - t:.0f}s)", "", body, ""]
            print(f"{m:32} {time.time() - t:5.0f}s  {q[:50]}", flush=True)
        out.write_text("\n".join(lines))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
