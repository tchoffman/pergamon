"""The hub's current situation: what's true around here right now.

Pergamon is general-purpose and assumes ordinary conditions unless told
otherwise. When things change (the power is out, stores are closed, water
comes from the creek), the host writes it here once and every answer, on the
web, the radio and the command line, takes it into account. Empty means
normal.

    pergamon situation                 # show
    pergamon situation set "Power out since Tuesday; wood stove works"
    pergamon situation clear
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from rag.config import REPO_ROOT

PATH = REPO_ROOT / "build" / "state" / "situation.json"
MAX_CHARS = 1000

PRESETS = {
    "power-out": "The power is out. Assume no electricity; gas and water may still work.",
    "grid-down": ("The power grid is down and stores are closed. Assume no electricity, no "
                  "oven or electric stove, no internet, and no deliveries; prefer fire, hand "
                  "tools and common household items."),
}


def load(path: Path | None = None) -> dict:
    path = path or PATH
    try:
        data = json.loads(path.read_text())
        return {"text": str(data.get("text") or "").strip(), "updated_at": data.get("updated_at")}
    except (OSError, ValueError):
        return {"text": "", "updated_at": None}


def save(text: str, path: Path | None = None, now: float | None = None) -> dict:
    path = path or PATH
    text = " ".join((text or "").split())[:MAX_CHARS]
    data = {"text": text, "updated_at": int(now or time.time()) if text else None}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def prompt_line(path: Path | None = None) -> str:
    """The line added to the model's context; empty when conditions are normal."""
    s = load(path)
    if not s["text"]:
        return ""
    when = time.strftime("%B %-d", time.localtime(s["updated_at"])) if s["updated_at"] else "recently"
    return (f"CURRENT SITUATION here (set by the host, updated {when}): {s['text']} "
            "Fit practical answers to this situation unless the person says otherwise.")


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "show"
    if cmd == "show":
        s = load()
        print(s["text"] or "(normal conditions — nothing set)")
        return 0
    if cmd == "set":
        text = " ".join(argv[1:]).strip()
        if text in PRESETS:
            text = PRESETS[text]
        if not text:
            print('Usage: pergamon situation set "what is true right now" '
                  f"(or a preset: {', '.join(PRESETS)})", file=sys.stderr)
            return 2
        save(text)
        print("Situation set:", load()["text"])
        return 0
    if cmd == "clear":
        save("")
        print("Situation cleared (normal conditions).")
        return 0
    print("Usage: pergamon situation [show | set \"text\" | clear]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
