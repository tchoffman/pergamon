"""Score answer quality against config/eval/questions.yaml.

    pergamon eval                         # configured model, full-length answers
    pergamon eval --mesh                  # radio-length answers
    pergamon eval --model qwen3.5:9b --model gemma4:12b
    pergamon eval --only snakebite --only amanita

Writes build/eval/eval-<timestamp>.md with every answer and why it failed.
Exit code is non-zero if any case fails, so it can gate a model or prompt change.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from rag.config import REPO_ROOT, load_config

CASES_PATH = REPO_ROOT / "config" / "eval" / "questions.yaml"


@dataclass
class Result:
    case_id: str
    model: str
    answer: str
    seconds: float
    failures: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return (yaml.safe_load(path.read_text()) or {}).get("cases") or []


NEGATIONS = re.compile(r"\b(not|never|avoid|don't|do not|no|nor|instead of|outdated|old advice)\b|n't\b")


def _affirmed(text: str, phrase: str) -> bool:
    """True if `phrase` appears without a negation shortly before it.

    "Do not suck out the venom" is the right advice; "suck out the venom" on
    its own is the dangerous one. Looks back to the start of the sentence (up to
    150 chars), since lists like "Do not cut, suck, ice, or apply a tourniquet"
    put the negation far from the last item.
    """
    start = 0
    while (i := text.find(phrase, start)) != -1:
        before = re.split(r"[.!?\n]", text[max(0, i - 150):i])[-1]
        if not NEGATIONS.search(before):
            return True
        start = i + 1
    return False


def check(case: dict, answer: str) -> list[str]:
    """Reasons the answer fails the case (empty list = pass)."""
    text = answer.lower()
    failures = []
    for group in case.get("must") or []:
        alts = [str(a).lower() for a in (group if isinstance(group, list) else [group])]
        if not any(a in text for a in alts):
            failures.append(f"missing one of: {', '.join(alts)}")
    for bad in case.get("must_not") or []:
        if _affirmed(text, str(bad).lower()):
            failures.append(f"contains forbidden: {bad}")
    if case.get("max_chars") and len(answer) > case["max_chars"]:
        failures.append(f"too long: {len(answer)} > {case['max_chars']} chars")
    return failures


def run_case(case: dict, model: str, mesh: bool, mode: str = "rag") -> Result:
    t = time.time()
    try:
        if mesh:
            from modules.comms.meshtastic.common import ask as mesh_ask
            text = mesh_ask(case["question"], model=model).answer
        elif mode == "agent":
            from rag import agent
            cfg = load_config()
            cfg["llm_model"] = model
            text, run = agent.answer(case["question"], cfg)
            r = Result(case["id"], model, text, time.time() - t, check(case, text))
            r.tools = [f"{n}({', '.join(f'{v}' for v in a.values())})" for n, a in run.tool_calls]
            return r
        else:
            from rag.query import answer
            cfg = load_config()
            cfg["llm_model"] = model
            text, _ = answer(case["question"], cfg)
    except Exception as e:
        return Result(case["id"], model, "", time.time() - t, [f"error: {e!r}"])
    return Result(case["id"], model, text, time.time() - t, check(case, text))


def report(results: list[Result], cases: list[dict], mesh: bool, mode: str = "rag") -> str:
    by_id = {c["id"]: c for c in cases}
    models = list(dict.fromkeys(r.model for r in results))
    lines = [f"# Pergamon eval ({datetime.now():%Y-%m-%d %H:%M}, {'mesh' if mesh else 'full'} answers, {mode} mode)", ""]
    for m in models:
        rs = [r for r in results if r.model == m]
        avg = sum(r.seconds for r in rs) / max(1, len(rs))
        lines.append(f"- **{m}**: {sum(r.passed for r in rs)}/{len(rs)} passed, avg {avg:.0f}s")
    lines.append("")
    for cid in dict.fromkeys(r.case_id for r in results):
        lines += [f"## {cid}: {by_id[cid]['question']}", ""]
        for r in (r for r in results if r.case_id == cid):
            mark = "PASS" if r.passed else "FAIL — " + "; ".join(r.failures)
            used = f" · tools: {'; '.join(r.tools)}" if r.tools else ""
            lines += [f"**{r.model}** ({r.seconds:.0f}s) {mark}{used}", "", r.answer or "_(no answer)_", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pergamon eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", help="model to test (repeatable)")
    ap.add_argument("--mesh", action="store_true", help="score radio-length answers")
    ap.add_argument("--only", action="append", help="case id to run (repeatable)")
    ap.add_argument("--mode", choices=["rag", "agent"], default="rag",
                    help="rag: always retrieve (default); agent: model calls tools")
    args = ap.parse_args(argv)

    cases = load_cases()
    if args.only:
        cases = [c for c in cases if c["id"] in set(args.only)]
    models = args.model or [load_config()["llm_model"]]

    out_dir = REPO_ROOT / "build" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"eval-{datetime.now():%Y%m%d-%H%M%S}.md"

    results: list[Result] = []
    # Model-outer: interleaving models on a 16GB machine forces Ollama to swap
    # them in and out of memory, and a 15s answer becomes a 15-minute one.
    for m in models:
        for case in cases:
            r = run_case(case, m, args.mesh, args.mode)
            results.append(r)
            print(f"{'PASS' if r.passed else 'FAIL'}  {m:30} {r.seconds:5.0f}s  {case['id']}"
                  + (f"  [{'; '.join(r.tools)}]" if r.tools else "")
                  + ("" if r.passed else f"  ({'; '.join(r.failures)})"), flush=True)
            out.write_text(report(results, cases, args.mesh, args.mode))

    for m in models:
        rs = [r for r in results if r.model == m]
        print(f"{m}: {sum(r.passed for r in rs)}/{len(rs)} passed")
    print(f"report: {out}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
