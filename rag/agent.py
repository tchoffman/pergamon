"""Tool-calling answer mode: the model decides whether to search, calculate,
or look things up, instead of always being handed library passages.

The always-retrieve path (rag.query) gives the model the nearest library text
for every question, so a weather question once got a survival manual's
"drop grass in the wind". Here retrieval is a tool the model may call, next
to a calculator and the community's own data, the way current chatbots work.

    pergamon ask --agent "what is 17.5% of 1284?"
    pergamon eval --mode agent

Tools that expose private data (the pantry) are only offered to the host.
"""
from __future__ import annotations

import ast
import json
import math
import operator
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

import ollama

from rag.config import load_config
from rag.domains import guard
from rag.query import _format_source, retrieve, system_prompt

MAX_ROUNDS = 4
TOOL_RESULT_CHARS = 4000

AGENT_SYSTEM = """You are Pergamon, a general-purpose assistant that runs entirely on this
machine. Help with anything, everyday, technical, creative or fun, the way a good
assistant would. Don't steer answers toward survival, emergencies or preparedness
unless the question is about that.

You have tools. Use one only when it makes the answer better:
- search_library for facts worth checking or detail you're unsure of, and for
  practical how-to questions where exact steps, amounts or names matter. Cite
  passages you use like [1]. Library books can be old; if one is outdated or
  unsafe by current practice, say so and give current practice.
- calculate for any arithmetic beyond the trivial. Never do sums in your head.
- community tools for what people nearby are offering, needing, or posting.
Don't call tools for chit-chat, writing, or things you know well.
Never offer to look something up or ask permission to use a tool: if it
would help, use it and answer. Don't ask which variant the person means;
answer for the most common case and mention how others differ. Ask a
question back only if you truly cannot answer without it.
If a search finds nothing useful, answer from your own knowledge. Never
comment on your sources: don't mention the library, what it lacks, or whether
you could verify something. Don't announce the answer ("I can provide...",
"Here is..."); just give it.
Read each message in the light of the conversation so far: a short reply
like "a bread oven" usually narrows the previous question.

You have no live data: no weather, news, prices, or road conditions. For those,
say so in one sentence, then give what still helps offline: what is typical for
this place and time of year, and how to judge it locally. Only bring this up
when the question asks for live information. Don't suggest apps,
websites, or the internet. Be concise. Verified safety facts, when given,
override everything else."""


# ───────────────────────────── tools ─────────────────────────────

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp,
          "sin": math.sin, "cos": math.cos, "tan": math.tan, "abs": abs, "round": round,
          "min": min, "max": max, "ceil": math.ceil, "floor": math.floor}
_CONSTS = {"pi": math.pi, "e": math.e}


def calculate(expression: str) -> str:
    """Safe arithmetic: numbers, + - * / // % **, parentheses, a few math functions."""
    expr = expression.replace(",", "").replace("^", "**").replace("×", "*").replace("÷", "/")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            left, right = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 1000:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _CONSTS:
            return _CONSTS[n.id]
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS
                and not n.keywords):
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError(f"unsupported expression: {ast.dump(n)[:60]}")

    try:
        value = ev(ast.parse(expr, mode="eval"))
    except Exception as e:
        return f"error: {e}"
    if isinstance(value, float):
        value = round(value, 10)
        if value.is_integer():
            value = int(value)
    return f"{expression} = {value}"


@dataclass
class Run:
    """What happened while answering: which tools ran, which sources were used."""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)  # e.g. the library drive is unplugged


def _search_library(cfg: dict, run: Run) -> Callable[[str], str]:
    def search_library(query: str) -> str:
        from rag.storage import StorageMissing
        limit = cfg.get("max_distance")
        try:
            hits = [h for h in retrieve(cfg, query) if limit is None or h["dist"] <= limit][:4]
        except StorageMissing:
            # The library's drive is unplugged: still answer, just without it.
            run.notices.append("The library drive isn't connected, so this answer doesn't use it.")
            return "The library is unavailable right now. Answer fully from your own knowledge."
        if not hits:
            return ("No library passages matched. Answer fully from your own knowledge; "
                    "don't mention the library.")
        out = []
        for h in hits:
            run.sources.append(_format_source(h["meta"]))
            n = len(run.sources)
            out.append(f"[{n}] {_format_source(h['meta'])}\n{h['text'].strip()[:900]}")
        return ("Library passages follow. Use any that help, citing them like [1]. If they "
                "don't cover the question, ignore them and answer fully from your own "
                "knowledge; don't say the library lacks it.\n\n" + "\n\n".join(out))
    return search_library


def _community_tools() -> dict[str, Callable[..., str]]:
    from modules.comms.meshtastic import commands

    def search_classifieds(query: str) -> str:
        return commands.run(f"/find {query}") or ""

    def search_skills(query: str) -> str:
        return commands.run(f"/skills {query}") or ""

    def board_posts(category: str = "") -> str:
        return commands.run(f"/news {category}".strip()) or ""

    return {"search_classifieds": search_classifieds, "search_skills": search_skills,
            "board_posts": board_posts}


def pantry() -> str:
    from datetime import date
    from rag import ledger
    rows = ledger.query_rows("food")
    if not rows:
        return "The pantry list is empty."
    today = date.today()
    lines = []
    for r in rows:
        if r.get("qty") is not None and float(r["qty"]) <= 0:
            continue
        left = ""
        if r.get("expires"):
            days = (date.fromisoformat(r["expires"]) - today).days
            left = f" (expires in {days} days)" if days >= 0 else f" (EXPIRED {-days} days ago)"
        qty = f"{r['qty']:g} {r.get('unit') or ''}".strip() if r.get("qty") is not None else ""
        lines.append(f"- {r['item']}{' — ' + qty if qty else ''}{left}")
    return "\n".join(lines[:60])


def _schema(name: str, description: str, params: dict[str, str], required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object",
                       "properties": {k: {"type": "string", "description": v} for k, v in params.items()},
                       "required": required}}}


def planting_calendar() -> str:
    from rag import guide
    return guide.describe_now()


def tools_for(cfg: dict, run: Run, is_host: bool) -> tuple[list[dict], dict[str, Callable[..., str]]]:
    """(schemas for the model, name → function). Private tools only for the host."""
    fns: dict[str, Callable[..., str]] = {
        "search_library": _search_library(cfg, run),
        "calculate": calculate,
        **_community_tools(),
        "planting_calendar": planting_calendar,
    }
    schemas = [
        _schema("search_library", "Search the offline library (encyclopedia, manuals, field guides, "
                "books) for passages about a topic.", {"query": "what to look up"}, ["query"]),
        _schema("calculate", "Evaluate an arithmetic expression exactly, e.g. '17.5/100*1284' or "
                "'sqrt(2)*3'.", {"expression": "the arithmetic expression"}, ["expression"]),
        _schema("search_classifieds", "Find items people nearby are offering or seeking.",
                {"query": "item words, e.g. 'eggs' or 'bike'"}, ["query"]),
        _schema("search_skills", "Find people nearby who offer a skill or service.",
                {"query": "skill words, e.g. 'welding'"}, ["query"]),
        _schema("board_posts", "Latest posts on the local community bulletin board.",
                {"category": "optional: news, notice, event, help, lost-found, warning"}, []),
        _schema("planting_calendar", "This area's frost dates and which vegetables to plant now "
                "and soon (from local climate records).", {}, []),
    ]
    if is_host:
        fns["pantry"] = pantry
        schemas.append(_schema("pantry", "This household's food inventory with expiry dates.", {}, []))
    return schemas, fns


# ───────────────────────────── loop ─────────────────────────────

def _call(fns: dict[str, Callable[..., str]], name: str, args: dict) -> str:
    fn = fns.get(name)
    if fn is None:
        return f"error: no tool named {name}"
    try:
        result = fn(**{k: str(v) for k, v in (args or {}).items()})
    except TypeError as e:
        return f"error: bad arguments for {name}: {e}"
    except Exception as e:
        return f"error: {name} failed: {e}"
    return result[:TOOL_RESULT_CHARS]


def _setup(question: str, cfg: dict, is_host: bool, extra_system: str,
           history: list[dict] | None = None):
    run = Run()
    schemas, fns = tools_for(cfg, run, is_host)
    # Safety domains are detected on the whole recent conversation, so "how do
    # I treat it?" after "a snake bit my dog" still gets the medical facts.
    recent = " ".join([m["content"] for m in (history or []) if m["role"] == "user"][-2:] + [question])
    system, domains = guard(recent, system_prompt(cfg, AGENT_SYSTEM) + extra_system)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system},
                                      *(history or []),
                                      {"role": "user", "content": question}]
    opts = dict(model=cfg["llm_model"], think=cfg.get("think", False),
                options={"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]})
    return run, schemas, fns, messages, opts, domains


def _args(c) -> dict:
    args = c["function"].get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    return dict(args)


def answer(question: str, cfg: dict | None = None, *, is_host: bool = True,
           extra_system: str = "", chat=None, history: list[dict] | None = None) -> tuple[str, Run]:
    """Answer with tools. `history` is earlier user/assistant messages from
    this conversation. Returns (text, run record)."""
    cfg = cfg or load_config()
    run, schemas, fns, messages, opts, _ = _setup(question, cfg, is_host, extra_system, history)
    if chat is None:
        chat = ollama.Client(host=cfg["ollama_host"]).chat

    for _ in range(MAX_ROUNDS):
        resp = chat(messages=messages, tools=schemas, **opts)
        msg = resp["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip(), run
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for c in calls:
            name, args = c["function"]["name"], _args(c)
            run.tool_calls.append((name, args))
            messages.append({"role": "tool", "tool_name": name, "content": _call(fns, name, args)})

    # Out of rounds: answer with what we have, no more tools.
    messages.append({"role": "user", "content": "Answer now using what you have; no more tools."})
    resp = chat(messages=messages, **opts)
    return (resp["message"].get("content") or "").strip(), run


async def astream(question: str, cfg: dict, *, is_host: bool = True, extra_system: str = "",
                  client=None, history: list[dict] | None = None):
    """Async version for the web page. Yields (kind, data) events:

      ("notice", banner)      safety domain matched
      ("tool", "name(args)")  a tool is being used
      ("source", "[n] label") a library passage the answer may cite
      ("token", text)         answer text as it is generated

    Each round streams; tool calls arrive at the end of a round, run off the
    event loop, and the next round streams the answer.
    """
    import asyncio
    run, schemas, fns, messages, opts, domains = _setup(question, cfg, is_host, extra_system, history)
    for d in domains:
        yield ("notice", d.banner)
    client = client or ollama.AsyncClient(host=cfg["ollama_host"])

    for round_no in range(MAX_ROUNDS + 1):
        last = round_no == MAX_ROUNDS
        if last:
            messages.append({"role": "user", "content": "Answer now using what you have; no more tools."})
        stream = await client.chat(messages=messages, stream=True,
                                   **({} if last else {"tools": schemas}), **opts)
        content, calls = [], []
        async for chunk in stream:
            msg = chunk["message"]
            if msg.get("tool_calls"):
                calls.extend(msg["tool_calls"])
            piece = msg.get("content") or ""
            if piece:
                content.append(piece)
                yield ("token", piece)
        if not calls:
            return
        messages.append({"role": "assistant", "content": "".join(content), "tool_calls": calls})
        for c in calls:
            name, args = c["function"]["name"], _args(c)
            run.tool_calls.append((name, args))
            yield ("tool", f"{name}({', '.join(str(v) for v in args.values())})")
            before, notes = len(run.sources), len(run.notices)
            result = await asyncio.to_thread(_call, fns, name, args)
            for n in run.notices[notes:]:
                yield ("notice", n)
            for i, s in enumerate(run.sources[before:], before + 1):
                yield ("source", f"[{i}] {s}")
            messages.append({"role": "tool", "tool_name": name, "content": result})


def main(argv: list[str]) -> int:
    q = " ".join(argv).strip()
    if not q:
        print('Usage: python -m rag.agent "question"', file=sys.stderr)
        return 2
    text, run = answer(q)
    for name, args in run.tool_calls:
        print(f"· {name}({json.dumps(args)})", file=sys.stderr)
    print(text)
    if run.sources:
        print("\nSources:")
        for i, s in enumerate(run.sources, 1):
            print(f"  [{i}] {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
