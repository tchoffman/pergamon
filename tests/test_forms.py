from fastapi.testclient import TestClient

from rag.web import FORM_KINDS, app


def test_forms_page_has_every_slip_with_qr():
    html = TestClient(app).get("/forms").text
    for title in ("BOARD NOTICE", "FOR TRADE / WANTED", "STILL AVAILABLE? / SOLD",
                  "TRADE RECORD", "SKILL OR SERVICE", "NEW MEMBER"):
        assert title in html, title
    assert html.count("<svg") == len(FORM_KINDS)
