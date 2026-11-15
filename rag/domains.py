"""Domain-aware safety guards for high-stakes questions.

Each domain lives in config/domains/<name>.yaml with keywords, a banner shown
to the user, and a system-prompt tail appended for the LLM. Detection is
keyword-based (word boundaries, case-insensitive) so it is cheap and
predictable, and works on the mesh path too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from rag.config import REPO_ROOT

DOMAINS_DIR = REPO_ROOT / "config" / "domains"


@dataclass(frozen=True)
class Domain:
    name: str
    title: str
    banner: str
    system_prompt_tail: str
    pattern: re.Pattern


@lru_cache(maxsize=1)
def load_domains() -> tuple[Domain, ...]:
    out = []
    if not DOMAINS_DIR.exists():
        return ()
    for p in sorted(DOMAINS_DIR.glob("*.yaml")):
        d = yaml.safe_load(p.read_text()) or {}
        kws = [k.strip().lower() for k in d.get("keywords") or [] if k.strip()]
        if not kws:
            continue
        # Longest first so multi-word phrases win; \b on both sides.
        alts = "|".join(re.escape(k) for k in sorted(kws, key=len, reverse=True))
        out.append(Domain(
            name=d["name"],
            title=d.get("title", d["name"]),
            banner=(d.get("banner") or "").strip(),
            system_prompt_tail=(d.get("system_prompt_tail") or "").strip(),
            pattern=re.compile(rf"\b(?:{alts})\b", re.IGNORECASE),
        ))
    return tuple(out)


def detect(question: str) -> list[Domain]:
    return [d for d in load_domains() if d.pattern.search(question)]


def guard(question: str, system_prompt: str) -> tuple[str, list[Domain]]:
    """Return (system_prompt with any matching domain tails appended, matches)."""
    matches = detect(question)
    if not matches:
        return system_prompt, []
    tails = "\n\n".join(d.system_prompt_tail for d in matches if d.system_prompt_tail)
    return f"{system_prompt}\n\n{tails}", matches
