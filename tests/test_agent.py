import pytest

from rag import agent
from rag.agent import calculate


def test_calculate_basics():
    assert calculate("17.5/100*1,284") == "17.5/100*1,284 = 224.7"
    assert calculate("2^10") == "2^10 = 1024"
    assert calculate("sqrt(16) + pi*0") == "sqrt(16) + pi*0 = 4"


@pytest.mark.parametrize("bad", ["__import__('os').system('ls')", "open('x')", "9**99999",
                                 "a.b", "[1,2]", "lambda: 1"])
def test_calculate_refuses_anything_but_arithmetic(bad):
    assert calculate(bad).startswith("error:")


class ScriptedModel:
    """Plays back model turns; records what it was sent."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def __call__(self, messages, tools=None, **kw):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return {"message": self.turns.pop(0)}


def _tool_turn(name, **args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


CFG = {"llm_model": "m", "temperature": 0, "num_ctx": 2048, "ollama_host": "x", "location": ""}


def test_no_tool_needed_answers_directly():
    model = ScriptedModel([{"content": "Soft paws, quiet room."}])
    text, run = agent.answer("haiku about cats", CFG, chat=model)
    assert text == "Soft paws, quiet room." and run.tool_calls == []


def test_tool_result_is_fed_back_then_answered():
    model = ScriptedModel([_tool_turn("calculate", expression="15/100*240"),
                           {"content": "15% of 240 is 36."}])
    text, run = agent.answer("What is 15% of 240?", CFG, chat=model)
    assert text == "15% of 240 is 36."
    assert run.tool_calls == [("calculate", {"expression": "15/100*240"})]
    tool_msg = model.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"].endswith("= 36")


def test_library_search_records_sources(monkeypatch):
    monkeypatch.setattr(agent, "retrieve", lambda cfg, q: [
        {"text": "Boil for one minute.", "meta": {"source": "fm.pdf", "chunk_index": 3}, "dist": 200},
        {"text": "off topic", "meta": {"source": "far.txt", "chunk_index": 1}, "dist": 900}])
    model = ScriptedModel([_tool_turn("search_library", query="purify water"),
                           {"content": "Boil it for a minute [1]."}])
    text, run = agent.answer("purify water?", dict(CFG, max_distance=350), chat=model)
    assert run.sources == ["fm.pdf#chunk3"]
    assert "far.txt" not in model.calls[1]["messages"][-1]["content"]


def test_pantry_only_offered_to_host():
    model = ScriptedModel([{"content": "ok"}, {"content": "ok"}])
    agent.answer("q", CFG, is_host=True, chat=model)
    agent.answer("q", CFG, is_host=False, chat=model)
    names = [[t["function"]["name"] for t in c["tools"]] for c in model.calls]
    assert "pantry" in names[0] and "pantry" not in names[1]


def test_unknown_tool_and_bad_args_are_reported_not_raised():
    model = ScriptedModel([_tool_turn("format_disk"), _tool_turn("calculate", nope="1"),
                           {"content": "done"}])
    text, _ = agent.answer("q", CFG, chat=model)
    tool_msgs = [m for m in model.calls[-1]["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == "error: no tool named format_disk"
    assert tool_msgs[1]["content"].startswith("error: bad arguments")
    assert text == "done"


def test_round_limit_forces_an_answer():
    turns = [_tool_turn("calculate", expression="1+1")] * agent.MAX_ROUNDS + [{"content": "2"}]
    model = ScriptedModel(turns)
    text, run = agent.answer("q", CFG, chat=model)
    assert text == "2" and len(run.tool_calls) == agent.MAX_ROUNDS
    assert model.calls[-1]["tools"] is None


def test_safety_facts_still_injected():
    model = ScriptedModel([{"content": "ok"}])
    agent.answer("how do I treat a snake bite?", CFG, chat=model)
    assert "Do NOT cut the" in model.calls[0]["messages"][0]["content"]
