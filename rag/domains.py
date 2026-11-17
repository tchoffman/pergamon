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
            # Plurals too: "morels" must trigger the "morel" safety facts.
            pattern=re.compile(rf"\b(?:{alts})(?:s|es)?\b", re.IGNORECASE),
        ))
    return tuple(out)


def detect(question: str) -> list[Domain]:
    """Domains whose keywords appear, or whose curated facts' triggers do: a
    question about a puffball needs the foraging rules even if it never says
    "mushroom"."""
    return [d for d in load_domains()
            if d.pattern.search(question) or matching_facts(question, d.name)]


@lru_cache(maxsize=None)
def load_facts(domain: str = "foraging") -> tuple[dict, ...]:
    """Curated facts for one domain, from config/domains/<domain>_facts.yaml."""
    path = DOMAINS_DIR / f"{domain}_facts.yaml"
    if not path.exists():
        return ()
    return tuple((yaml.safe_load(path.read_text()) or {}).get("facts") or [])


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z']+", text.lower()):
        out.add(w)
        if len(w) > 3 and w.endswith("s"):
            out.add(w[:-1])
    return out


def matching_facts(question: str, domain: str = "foraging") -> list[dict]:
    """Curated facts for `domain` whose triggers all appear in the question."""
    words = _words(question)
    return [f for f in load_facts(domain)
            if any(all(t.lower() in words for t in trig) for trig in f.get("triggers") or [])]


def missing_warnings(question: str, answer: str) -> list[str]:
    """Short warnings for safety facts that apply to `question` but that
    `answer` never mentions (no `must_mention` term appears in it).

    Radio answers are a few sentences, and a small model under that limit
    sometimes drops the one warning that matters (in testing it described
    true morels without naming false morels, 3 runs in 4). Prompting made it
    likelier, not certain; this makes it certain.
    """
    low = answer.lower()
    out = []
    for d in detect(question):
        for f in matching_facts(question, d.name):
            must, radio = f.get("must_mention"), f.get("radio")
            if must and radio and not any(m.lower() in low for m in must):
                out.append(radio)
    return out


def guard(question: str, system_prompt: str) -> tuple[str, list[Domain]]:
    """Return (system_prompt with any matching domain tails appended, matches).

    Curated facts for each matched domain are injected first: the local
    model's own knowledge is unreliable on these, and the library's older
    manuals carry outdated advice (e.g. cut-and-suck for snakebite).
    """
    matches = detect(question)
    facts = [f for d in matches for f in matching_facts(question, d.name)]
    if not matches:
        return system_prompt, []
    parts = [system_prompt]
    if facts:
        parts.append("Verified safety facts follow. Say the relevant ones first, in your own "
                     "plain words, before anything else. Never print this heading or call "
                     "them 'verified safety facts':\n" + "\n".join(f"- {f['fact']}" for f in facts))
    parts += [d.system_prompt_tail for d in matches if d.system_prompt_tail]
    return "\n\n".join(parts), matches


def frame_question(question: str, domains: list[Domain]) -> str:
    """Reword high-stakes questions so the model gives guidance instead of
    refusing outright (small models refuse "can I eat this?" reflexively)."""
    if any(d.name == "foraging" for d in domains):
        return (f'A forager asks for safety guidance about a find: "{question}"\n'
                "Explain what the described features suggest, which dangerous species share "
                "them, what to check next, and why not to eat it without in-person expert "
                "confirmation.")
    return question
