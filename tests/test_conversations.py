from rag import agent
from rag.conversations import Conversations


def test_history_is_chat_messages_oldest_first():
    c = Conversations()
    c.add("r", "How do I build a wood-fired oven?", "What kind of oven?")
    c.add("r", "A bread oven", "Use a dome of firebrick...")
    h = c.history("r")
    assert [m["role"] for m in h] == ["user", "assistant", "user", "assistant"]
    assert h[0]["content"].startswith("How do I build") and h[2]["content"] == "A bread oven"


def test_keeps_only_recent_turns_and_forgets_when_idle():
    c = Conversations(max_turns=2, idle_s=60)
    for i in range(4):
        c.add("r", f"q{i}", f"a{i}", now=1000 + i)
    assert [m["content"] for m in c.history("r", now=1005)][::2] == ["q2", "q3"]
    assert c.history("r", now=1003 + 61) == []


def test_conversations_are_separate_and_resettable():
    c = Conversations()
    c.add("a", "q", "x")
    assert c.history("b") == []
    c.reset("a")
    assert c.history("a") == []


class Model:
    def __init__(self):
        self.sent = None

    def __call__(self, messages, tools=None, **kw):
        self.sent = messages
        return {"message": {"content": "ok"}}


CFG = {"llm_model": "m", "temperature": 0, "num_ctx": 2048, "ollama_host": "x", "location": ""}


def test_agent_sees_earlier_turns_before_the_new_question():
    m = Model()
    hist = [{"role": "user", "content": "How do I build a wood-fired oven?"},
            {"role": "assistant", "content": "Pizza or bread?"}]
    agent.answer("A bread oven", CFG, chat=m, history=hist)
    assert [x["content"] for x in m.sent[1:]] == [hist[0]["content"], hist[1]["content"], "A bread oven"]


def test_safety_facts_follow_the_conversation():
    m = Model()
    hist = [{"role": "user", "content": "A snake bit my friend on the hand"},
            {"role": "assistant", "content": "Get medical help."}]
    agent.answer("what should I do while we wait?", CFG, chat=m, history=hist)
    assert "Do NOT cut the" in m.sent[0]["content"]
