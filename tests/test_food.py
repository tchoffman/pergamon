from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from rag import install_config, ledger, nostr
from rag.web import app, pantry_text


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "DB_DIR", tmp_path / "db")
    monkeypatch.setattr(install_config, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    return TestClient(app)


def _iso(days):
    return (date.today() + timedelta(days=days)).isoformat()


def test_add_list_soon_and_order(client):
    assert client.get("/food").status_code == 200
    client.post("/api/food", json={"item": "Rice", "qty": "10", "unit": "lb", "location": "pantry"})
    client.post("/api/food", json={"item": "Milk", "qty": "1", "expires": _iso(2), "location": "fridge"})
    client.post("/api/food", json={"item": "Old bread", "expires": _iso(-1)})
    data = client.get("/api/food").json()
    assert [i["item"] for i in data["items"]] == ["Old bread", "Milk", "Rice"]  # soonest first
    assert [i["item"] for i in data["soon"]] == ["Old bread", "Milk"]
    assert [i["item"] for i in client.get("/api/food?location=fridge").json()["items"]] == ["Milk"]


def test_update_and_delete(client):
    rid = client.post("/api/food", json={"item": "Eggs", "qty": "12"}).json()["id"]
    assert client.post(f"/api/food/{rid}", json={"qty": 11}).status_code == 200
    assert client.get("/api/food").json()["items"][0]["qty"] == 11
    assert client.delete(f"/api/food/{rid}").status_code == 200
    assert client.delete(f"/api/food/{rid}").status_code == 404
    assert client.post("/api/food/999", json={"qty": 1}).status_code == 404


def test_validation(client):
    assert client.post("/api/food", json={"qty": "3"}).status_code == 400
    assert client.post("/api/food", json={"item": "x", "location": "garage"}).status_code == 400


def test_pantry_text_flags_and_skips_empty():
    rows = [
        {"item": "Milk", "qty": 1.0, "unit": "gal", "days_left": 2},
        {"item": "Bread", "qty": None, "unit": None, "days_left": -1},
        {"item": "Rice", "qty": 10.0, "unit": "lb", "days_left": None},
        {"item": "Flour", "qty": 0.0, "unit": "lb", "days_left": None},
    ]
    text = pantry_text(rows)
    assert "Milk — 1 gal (USE SOON)" in text
    assert "Bread (EXPIRED)" in text
    assert "Rice — 10 lb" in text
    assert "Flour" not in text


def test_cook_requires_items(client):
    assert client.get("/api/food/cook").status_code == 400


def test_lan_clients_blocked_unless_enabled(client, tmp_path):
    lan = TestClient(app, client=("192.168.1.50", 50000))
    assert lan.get("/food").status_code == 403
    assert lan.get("/api/food").status_code == 403
    assert lan.post("/api/food", json={"item": "x"}).status_code == 403
    (tmp_path / "install.local.yaml").write_text("food:\n  lan_access: true\n")
    assert lan.get("/api/food").status_code == 200
