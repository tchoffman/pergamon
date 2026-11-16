import rag.query as q


def _fake_hits(cfg, question):
    return [{"text": "a", "meta": {}, "dist": 200.0},
            {"text": "b", "meta": {}, "dist": 349.9},
            {"text": "c", "meta": {}, "dist": 451.0}]


def test_drops_passages_past_max_distance(monkeypatch):
    monkeypatch.setattr(q, "retrieve", _fake_hits)
    hits = q.relevant_hits({"max_distance": 350}, "anything")
    assert [h["text"] for h in hits] == ["a", "b"]


def test_no_limit_keeps_everything(monkeypatch):
    monkeypatch.setattr(q, "retrieve", _fake_hits)
    assert len(q.relevant_hits({}, "anything")) == 3


def test_prompt_lets_model_answer_on_its_own():
    assert "ONLY" not in q.SYSTEM_PROMPT
    assert "own knowledge" in q.SYSTEM_PROMPT


def test_situation_gives_date_and_location():
    from datetime import date
    line = q.situation({"location": "Marietta, Georgia, USA"}, today=date(2026, 11, 15))
    assert line.startswith("Today is Sunday, November 15, 2026. The user is in Marietta, Georgia, USA.")
    assert "user is in" not in q.situation({}, today=date(2026, 11, 15))


def _hits(*texts):
    return [{"text": t, "meta": {}, "dist": 200.0} for t in texts]


def _chat_returning(content):
    def chat(**kw):
        chat.prompt = kw["messages"][1]["content"]
        return {"message": {"content": content}}
    return chat


def test_vet_keeps_only_helpful_passages():
    hits = _hits("wildflowers", "planting calendar", "boiling water")
    kept = q.vet_passages({"llm_model": "m"}, "what to plant", hits, chat=_chat_returning('{"helpful": [2]}'))
    assert [h["text"] for h in kept] == ["planting calendar"]


def test_vet_drops_everything_on_junk_or_error():
    hits = _hits("a", "b")
    assert q.vet_passages({"llm_model": "m"}, "q", hits, chat=_chat_returning("not json")) == []
    def broken(**kw):
        raise ConnectionError("ollama down")
    assert q.vet_passages({"llm_model": "m"}, "q", hits, chat=broken) == []


def test_vet_ignores_out_of_range_numbers():
    hits = _hits("a", "b")
    kept = q.vet_passages({"llm_model": "m"}, "q", hits, chat=_chat_returning('{"helpful": [1, 9, "x"]}'))
    assert [h["text"] for h in kept] == ["a"]
