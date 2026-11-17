from fastapi.testclient import TestClient

from rag import situation


def test_empty_means_normal_and_adds_nothing(tmp_path):
    p = tmp_path / "s.json"
    assert situation.load(p) == {"text": "", "updated_at": None}
    assert situation.prompt_line(p) == ""


def test_set_shows_in_prompt_with_date(tmp_path):
    p = tmp_path / "s.json"
    situation.save("  Power out;   wood stove works ", p, now=1_794_700_000)
    assert situation.load(p)["text"] == "Power out; wood stove works"
    line = situation.prompt_line(p)
    assert "Power out; wood stove works" in line and "updated" in line


def test_reaches_every_system_prompt(tmp_path, monkeypatch):
    from rag.query import system_prompt
    monkeypatch.setattr(situation, "PATH", tmp_path / "s.json")
    situation.save(situation.PRESETS["grid-down"])
    assert "CURRENT SITUATION" in system_prompt({"location": ""})
    situation.save("")
    assert "CURRENT SITUATION" not in system_prompt({"location": ""})


def test_only_host_can_change_it(tmp_path, monkeypatch):
    from rag import web
    monkeypatch.setattr(situation, "PATH", tmp_path / "s.json")
    c = TestClient(web.app)
    assert c.post("/api/situation", json={"text": "Power out"}).json()["text"] == "Power out"
    assert c.get("/api/situation").json()["text"] == "Power out"
    assert "Power out" in c.get("/chat").text
    monkeypatch.setattr(web, "_is_host", lambda request: False)
    assert c.post("/api/situation", json={"text": "hacked"}).status_code == 403
    assert 'id="sitEdit"' not in c.get("/chat").text
