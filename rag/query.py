"""Ask the library a question. Retrieves relevant chunks, synthesizes with the LLM."""
from __future__ import annotations

import json
import re
import sys
from datetime import date

import chromadb
import ollama
from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule

from rag.config import load_config
from rag.domains import frame_question, guard

console = Console()


SYSTEM_PROMPT = """You are Pergamon, an offline assistant for someone without internet.
Answer directly from your own knowledge; you do not need the library to answer.
When LIBRARY EXCERPTS are provided, use them to check your answer and to add
specific details (amounts, steps, names), citing them like [1]. With no excerpts,
cite nothing. Some library books are decades old: if an excerpt's advice is
outdated or unsafe by current practice, give current practice and say the book
is old. VERIFIED SAFETY FACTS, when given, override both. Ignore excerpts that
are off-topic and don't mention them. Be practical and concise. For medical, legal,
or dangerous subjects, note the limits of any text-based advice."""

def situation(cfg: dict, today: "date | None" = None) -> str:
    """One line of facts the model can't know: today's date and where the user is."""
    d = today or date.today()
    line = f"Today is {d.strftime('%A, %B')} {d.day}, {d.year}."
    if cfg.get("location"):
        line += f" The user is in {cfg['location']}."
    return line + " Mention the date or place only when it changes the answer."


def system_prompt(cfg: dict, base: str = SYSTEM_PROMPT) -> str:
    return f"{base}\n\n{situation(cfg)}"


CONTEXT_HEADER ="LIBRARY EXCERPTS (use to check and add detail; ignore any that are off-topic):"


def retrieve(cfg: dict, question: str) -> list[dict]:
    client_chroma = chromadb.PersistentClient(path=cfg["chroma_dir"])
    coll = client_chroma.get_or_create_collection(cfg["chroma_collection"])
    if coll.count() == 0:
        return []
    client_ollama = ollama.Client(host=cfg["ollama_host"])
    q_emb = client_ollama.embeddings(model=cfg["embedding_model"], prompt=question)["embedding"]
    res = coll.query(query_embeddings=[q_emb], n_results=cfg["retrieve_k"])
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    return [{"text": d, "meta": m, "dist": dist} for d, m, dist in zip(docs, metas, dists)]


def relevant_hits(cfg: dict, question: str) -> list[dict]:
    """Retrieved passages close enough to the question to be worth showing the LLM.

    Unrelated passages invite the model to bend its answer toward them, so the
    answer paths drop anything past `max_distance`. Search shows everything.
    """
    limit = cfg.get("max_distance")
    hits = retrieve(cfg, question)
    if limit is not None:
        hits = [h for h in hits if h["dist"] <= limit]
    if hits and cfg.get("vet_passages"):
        hits = vet_passages(cfg, question, hits[: cfg.get("vet_max", 4)])
    return hits


VET_PROMPT = """You decide which library passages help answer a question.
A passage helps only if it contains facts that directly answer the question.
Being about a similar topic is not enough (a wildflower guide does not answer
a vegetable-gardening question). Reply with JSON only: {"helpful": [numbers]}
or {"helpful": []} if none help."""


def vet_passages(cfg: dict, question: str, hits: list[dict], chat=None) -> list[dict]:
    """Keep only passages the model judges to actually answer the question.

    Distance alone can't tell "same topic" from "answers it": with no gardening
    book indexed, a gardening question's nearest passage is a wildflower guide,
    and a small model will build an answer around whatever it is handed. If the
    check fails or returns junk, no passages are used — the model then answers
    from its own knowledge, which beats steering it with off-topic text.
    """
    if not hits:
        return []
    listing = "\n\n".join(f"[{i}] {h['text'].strip()[:700]}" for i, h in enumerate(hits, 1))
    if chat is None:
        chat = ollama.Client(host=cfg["ollama_host"]).chat
    try:
        # No format="json": some models (Qwen 3.5 on Ollama 0.40) reject
        # structured output with a 501. Parse the object out of plain text.
        resp = chat(
            model=cfg["llm_model"],
            messages=[{"role": "system", "content": VET_PROMPT},
                      {"role": "user", "content": f"QUESTION: {question}\n\nPASSAGES:\n{listing}"}],
            think=cfg.get("think", False),
            options={"temperature": 0, "num_ctx": cfg.get("num_ctx", 8192)},
        )
        keep = _parse_helpful(resp["message"]["content"])
    except Exception as e:
        print(f"[pergamon] passage check failed, answering without library: {e!r}", file=sys.stderr)
        return []
    return [h for i, h in enumerate(hits, 1) if i in keep]


def _parse_helpful(text: str) -> set[int]:
    """Numbers from {"helpful": [...]} anywhere in the reply (code fences, prose)."""
    m = re.search(r"\{[^{}]*\"helpful\"[^{}]*\}", text, re.S)
    if not m:
        raise ValueError(f"no helpful list in reply: {text[:120]!r}")
    picked = json.loads(m.group(0)).get("helpful") or []
    return {int(n) for n in picked if str(n).isdigit()}


def _format_source(meta: dict, kiwix_port: int = 8080) -> str:
    """Turn a chunk's metadata into a human-readable citation.

    ZIM-sourced chunks get a browse URL into kiwix-serve so the user can jump
    to the full article. PDF chunks fall back to source#chunk.
    """
    source = meta.get("source", "?")
    if "zim_path" in meta:
        title = meta.get("title", meta["zim_path"])
        zim_stem = source.rsplit(".", 1)[0]  # strip .zim
        url = f"http://localhost:{kiwix_port}/viewer#{zim_stem}/{meta['zim_path']}"
        return f"{source} — {title}  ({url})"
    return f"{source}#chunk{meta.get('chunk_index', '?')}"


def build_context(hits: list[dict], cap: int) -> tuple[str, list[str]]:
    sources: list[str] = []
    parts: list[str] = []
    used = 0
    for i, h in enumerate(hits, 1):
        label = f"[{i}] {_format_source(h['meta'])}"
        block = f"=== {label} ===\n{h['text'].strip()}\n"
        if used + len(block) > cap and parts:
            break
        parts.append(block)
        sources.append(label)
        used += len(block)
    return "\n".join(parts), sources


def build_messages(cfg: dict, question: str) -> tuple[list[dict], list[str], list]:
    """Everything the model sees for one question: (messages, source labels, domains)."""
    hits = relevant_hits(cfg, question)
    system, domains = guard(question, system_prompt(cfg))
    framed = frame_question(question, domains)
    if hits:
        context, sources = build_context(hits, cfg["max_context_chars"])
        user_prompt = f"{CONTEXT_HEADER}\n{context}\n\nQUESTION: {framed}"
    else:
        sources = []
        user_prompt = f"QUESTION: {framed}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}]
    return messages, sources, domains


def _chat_kwargs(cfg: dict) -> dict:
    return dict(model=cfg["llm_model"], think=cfg.get("think", False),
                options={"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]})


def answer(question: str, cfg: dict | None = None) -> tuple[str, list[str]]:
    """Non-streaming full-length answer: (text, source labels)."""
    cfg = cfg or load_config()
    messages, sources, _ = build_messages(cfg, question)
    resp = ollama.Client(host=cfg["ollama_host"]).chat(messages=messages, **_chat_kwargs(cfg))
    return resp["message"]["content"].strip(), sources


def ask(question: str) -> int:
    cfg = load_config()
    messages, sources, domains = build_messages(cfg, question)
    stream = ollama.Client(host=cfg["ollama_host"]).chat(
        messages=messages, stream=True, **_chat_kwargs(cfg))

    for d in domains:
        console.print(f"[bold yellow]⚠ {d.banner}[/bold yellow]")
    console.print(Rule("[bold green]Pergamon"))
    buf = []
    for chunk in stream:
        piece = chunk["message"]["content"]
        buf.append(piece)
        console.out(piece, end="")
    console.print("")
    if sources:
        console.print(Rule("[bold]Sources"))
        for s in sources:
            console.print(f"  • {s}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m rag.query 'your question here'", file=sys.stderr)
        sys.exit(2)
    sys.exit(ask(" ".join(sys.argv[1:])))
