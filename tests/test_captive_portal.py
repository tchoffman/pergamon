from fastapi.testclient import TestClient

from rag.web import _PROBE_PATHS, app


def test_probes_redirect_to_welcome():
    client = TestClient(app, raise_server_exceptions=False)
    for path in _PROBE_PATHS:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302, path
        assert r.headers["location"] == "/welcome"


def test_welcome_page():
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/welcome")
    assert r.status_code == 200
    assert "/board" in r.text and "/classifieds" in r.text
