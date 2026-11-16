"""Ask the library a question. Retrieves relevant chunks, synthesizes with the LLM."""
from __future__ import annotations

import sys

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
cite nothing. If an excerpt
contradicts what you believe, go with the excerpt and say so. Ignore excerpts that
are off-topic and don't mention them. Be practical and concise. For medical, legal,
or dangerous subjects, note the limits of any text-based advice."""

CONTEXT_HEADER = "LIBRARY EXCERPTS (use to check and add detail; ignore any that are off-topic):"


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
    return hits if limit is None else [h for h in hits if h["dist"] <= limit]


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


def ask(question: str) -> int:
    cfg = load_config()
    hits = relevant_hits(cfg, question)

    system, domains = guard(question, SYSTEM_PROMPT)
    framed = frame_question(question, domains)
    if hits:
        context, sources = build_context(hits, cfg["max_context_chars"])
        user_prompt = f"{CONTEXT_HEADER}\n{context}\n\nQUESTION: {framed}"
    else:
        context, sources = "", []
        user_prompt = f"QUESTION: {framed}"

    client_ollama = ollama.Client(host=cfg["ollama_host"])
    stream = client_ollama.chat(
        model=cfg["llm_model"],
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        stream=True,
        options={"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]},
    )

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
