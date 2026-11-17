"""Shared helpers: running a RAG query, chunking the answer for LoRa.

Keeps the mesh-facing code identical in mock and real modes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import ollama

from rag.config import load_config
from rag.domains import frame_question, guard, missing_warnings
from rag.query import CONTEXT_HEADER, _format_source, build_context, relevant_hits, system_prompt

# Meshtastic messages cap around 240 bytes of payload. Leave headroom for
# UTF-8 multi-byte chars and our "[p/N] " prefix.
MAX_MESH_CHARS = 200


@dataclass
class MeshReply:
    answer: str
    sources: list[str]

    def chunks(self, max_chars: int = MAX_MESH_CHARS) -> list[str]:
        """Split the full reply (answer + top source) into LoRa-sized chunks.

        Each chunk is prefixed with [p/N] so receivers can reassemble out of
        order. Prefers splitting on whitespace boundaries.
        """
        body = self.answer.strip()
        if self.sources:
            body += "\n\n" + self.sources[0]

        pieces: list[str] = []
        remaining = body
        while remaining:
            if len(remaining) <= max_chars:
                pieces.append(remaining)
                break
            # Try to split on a word boundary near max_chars.
            split_at = remaining.rfind(" ", 0, max_chars)
            if split_at < max_chars // 2:
                split_at = max_chars  # fall back to hard split
            pieces.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip()

        n = len(pieces)
        return [f"[{i + 1}/{n}] {p}" for i, p in enumerate(pieces)]


SHORT_HINT = (
    "\n\nRespond in at most 4 short sentences, plain text, no lists or markdown. "
    "If they ask for a recipe or measurements, include the quantities. "
    "This reply goes over low-bandwidth mesh radio, so lead with the most "
    "important point and never drop a critical safety step or warning to save space."
)
_CITES = re.compile(r"\s*\[\d+(?:,\s*\d+)*\]")

# Sentences about where the answer came from waste airtime and read oddly
# ("Check the library for the steps", "I can't verify this"). The model is
# told not to write them; this catches the ones it writes anyway.
_META = re.compile(r"\b(library|passages?|I can provide|I found (a|an|the|some)|"
                   r"(can't|cannot|couldn't|unable to) (be )?verif\w*|"
                   r"based on general \w*\s?knowledge)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_IN_LIBRARY = re.compile(r"\s+(in|from) (the|my|our) library\b", re.I)
_FOUND = re.compile(r"^I found (a|an|the|some) ", re.I)


def drop_meta(text: str, question: str = "") -> str:
    """Remove sentences that talk about sources instead of answering, unless
    the question was about them. Never empties an answer."""
    if _META.search(question):
        return text
    # A sentence with numbers in it likely carries amounts or times: keep it.
    kept = [x for x in _SENTENCE.split(text.strip()) if not _META.search(x) or re.search(r"\d", x)]
    # In the sentences kept for their amounts, trim the sourcing words themselves.
    kept = [_FOUND.sub(lambda m: m.group(1).capitalize() + " ", _IN_LIBRARY.sub("", x)) for x in kept]
    return " ".join(kept) if kept else text


def _ask_agent(question: str, cfg: dict, history: list[dict] | None = None) -> MeshReply:
    """Tool-calling answer for a radio user. Radio users are never the host,
    so private tools (pantry) aren't offered."""
    from rag import agent
    text, run = agent.answer(question, cfg, is_host=False, extra_system=SHORT_HINT,
                             history=history)
    top = run.sources[0].split("  (")[0] if run.sources else None
    text = drop_meta(_CITES.sub("", text).strip(), question)
    # Same window the safety check in agent._setup uses: this and the last two questions.
    recent = " ".join([m["content"] for m in (history or []) if m["role"] == "user"][-2:] + [question])
    warnings = missing_warnings(recent, text)
    if warnings:
        text = " ".join(warnings + [text])
    return MeshReply(answer=text, sources=[top] if top else [])


def ask(question: str, max_tokens_hint: int = 150, model: str | None = None,
        history: list[dict] | None = None) -> MeshReply:
    """Run the RAG pipeline synchronously and return a MeshReply.

    Deliberately short answers: mesh users don't want essays. The system
    prompt gets an extra hint to stay terse. `model` overrides the configured
    LLM (used by scripts/compare-models.py).
    """
    cfg = load_config()
    if model:
        cfg["llm_model"] = model
    if cfg.get("answer_mode") == "agent":
        return _ask_agent(question, cfg, history)
    hits = relevant_hits(cfg, question)

    if hits:
        context, sources = build_context(hits, cfg["max_context_chars"])
        user_prompt = f"{CONTEXT_HEADER}\n{context}\n\nQUESTION: {frame_question(question, guard(question, '')[1])}"
    else:
        sources = []
        user_prompt = f"QUESTION: {frame_question(question, guard(question, '')[1])}"

    client = ollama.Client(host=cfg["ollama_host"])
    resp = client.chat(
        model=cfg["llm_model"],
        messages=[
            {"role": "system", "content": guard(question, system_prompt(cfg))[0] + SHORT_HINT},
            {"role": "user", "content": user_prompt},
        ],
        think=cfg.get("think", False),
        options={"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]},
    )
    # Inline [n] markers mean nothing on a radio screen; the top source is appended below.
    answer = _CITES.sub("", resp["message"]["content"]).strip()

    # Trim to just the top source for brevity; include only the article title.
    short_sources: list[str] = []
    if hits:
        top = _format_source(hits[0]["meta"])
        # Keep the human-readable part only, drop the long kiwix URL.
        short_sources = [top.split("  (")[0] if "  (" in top else top]

    return MeshReply(answer=answer, sources=short_sources)
