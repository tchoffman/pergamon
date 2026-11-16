import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rag import web
from rag.web import ModelGate


def test_one_answer_in_flight_per_device():
    g = ModelGate()
    g.acquire("10.0.0.5", is_host=False)
    with pytest.raises(HTTPException, match="Still answering"):
        g.acquire("10.0.0.5", is_host=False)
    g.acquire("10.0.0.6", is_host=False)  # another phone is fine
    g.release("10.0.0.5")
    g.acquire("10.0.0.5", is_host=False)


def test_hourly_cap_for_guests_not_host():
    g = ModelGate(per_hour=2)
    for t in (0, 1):
        g.acquire("guest", is_host=False, now=1000 + t)
        g.release("guest")
    with pytest.raises(HTTPException) as e:
        g.acquire("guest", is_host=False, now=1002)
    assert e.value.status_code == 429
    g.acquire("guest", is_host=False, now=1000 + 3601)  # window slid
    for t in range(5):
        g.acquire("host", is_host=True, now=1000 + t)
        g.release("host")


class FakeAsyncClient:
    def __init__(self, host=None):
        pass

    async def chat(self, **kw):
        async def gen():
            for piece in ["Boil ", "it."]:
                yield {"message": {"content": piece}}
        return gen()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(web, "relevant_hits", lambda cfg, q: [])
    monkeypatch.setattr(web.ollama, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(web, "_gate", ModelGate())
    return TestClient(web.app)


def test_ask_streams_and_releases_gate(client):
    body = client.get("/ask", params={"q": "how do I purify water"}).text
    assert "event: token" in body and "Boil" in body and "event: done" in body
    assert web._gate.in_flight == set()


def test_empty_and_huge_questions_explained_in_stream(client):
    assert "Type a question first" in client.get("/ask", params={"q": "  "}).text
    assert "too long" in client.get("/ask", params={"q": "x" * 5000}).text


def test_model_failure_becomes_failed_event(client, monkeypatch):
    class Broken(FakeAsyncClient):
        async def chat(self, **kw):
            raise ConnectionError("ollama down at http://localhost:11434")
    monkeypatch.setattr(web.ollama, "AsyncClient", Broken)
    body = client.get("/ask", params={"q": "hi"}).text
    assert "event: failed" in body and "localhost" not in body
    assert web._gate.in_flight == set()


class FakeAgentStream:
    """AsyncClient stand-in: round 1 calls the calculator, round 2 answers."""

    def __init__(self, host=None):
        self.round = 0

    async def chat(self, **kw):
        self.round += 1
        r = self.round

        async def gen():
            if r == 1:
                yield {"message": {"content": "", "tool_calls": [
                    {"function": {"name": "calculate", "arguments": {"expression": "15/100*240"}}}]}}
            else:
                for piece in ["It is ", "36."]:
                    yield {"message": {"content": piece}}
        return gen()


def test_agent_mode_streams_tool_use_then_answer(client, monkeypatch):
    from rag import agent
    monkeypatch.setattr(web, "load_config", lambda: {**_cfg(), "answer_mode": "agent"})
    monkeypatch.setattr(agent.ollama, "AsyncClient", FakeAgentStream)
    body = client.get("/ask", params={"q": "what is 15% of 240"}).text
    assert "event: tool" in body and "calculate(15/100*240)" in body
    assert "36." in body and "event: done" in body
    assert web._gate.in_flight == set()


def _cfg():
    from rag.config import load_config
    return load_config()
