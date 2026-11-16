"""Planning: RAG + a planner-tuned system prompt.

Takes a goal, retrieves relevant context (same as `ask`), but nudges the LLM
into a structured plan rather than a Q&A answer.
"""
from __future__ import annotations

import sys

import ollama
from rich.console import Console
from rich.rule import Rule

from rag.config import load_config
from rag.domains import guard
from rag.query import CONTEXT_HEADER, build_context, relevant_hits, system_prompt

console = Console()


PLAN_SYSTEM_PROMPT = """You are Pergamon's planner. The user describes a goal; you
produce a practical, grounded plan.

Structure the plan EXACTLY like this (markdown):

**Goal**: one-sentence restatement

**Steps**
1. ...
2. ...
(numbered, each one line, in execution order)

**Resources needed**
- materials, tools, references, time estimate

**Risks & mitigations**
- what could go wrong → how to handle it

**Verification**
- how you'll know it worked

Rules:
- Plan from your own knowledge. Use LIBRARY EXCERPTS, when given, to check
  facts and add specifics, citing them inline like [1], [2].
- Be concrete: numbers, specific gear, specific steps. No hedging.
- Off-grid / wilderness / survival planning assumes limited internet,
  limited tools, and the user handling things alone or in a small group.
- For medical, legal, or dangerous subjects, name the obvious caveats
  without burying the plan."""


def make_plan(goal: str, stream: bool = True) -> int:
    cfg = load_config()
    hits = relevant_hits(cfg, goal)

    if hits:
        context, sources = build_context(hits, cfg["max_context_chars"])
        user_prompt = f"{CONTEXT_HEADER}\n{context}\n\nGOAL: {goal}"
    else:
        context, sources = "", []
        user_prompt = f"GOAL: {goal}"

    system, domains = guard(goal, system_prompt(cfg, PLAN_SYSTEM_PROMPT))
    client = ollama.Client(host=cfg["ollama_host"])
    kwargs = dict(
        model=cfg["llm_model"],
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        options={"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]},
    )

    for d in domains:
        console.print(f"[bold yellow]⚠ {d.banner}[/bold yellow]")
    console.print(Rule("[bold green]Plan"))
    if stream:
        for chunk in client.chat(stream=True, **kwargs):
            piece = chunk["message"]["content"]
            if piece:
                console.out(piece, end="")
        console.print("")
    else:
        resp = client.chat(**kwargs)
        console.print(resp["message"]["content"])

    if sources:
        console.print(Rule("[bold]Sources"))
        for s in sources:
            console.print(f"  • {s}")

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: pergamon plan \"your goal\"", file=sys.stderr)
        sys.exit(2)
    sys.exit(make_plan(" ".join(sys.argv[1:])))
