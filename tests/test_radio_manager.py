"""Radios plugged into the hub: servers answer, clients are left alone, new
ones wait for the host."""
import time

import pytest

from modules.comms.meshtastic.manager import RadioManager
from rag import radios

RADIOS = {"/dev/a": ("!0000000a", "Pergamon-Blue"), "/dev/b": ("!0000000b", "Pergamon-Yellow"),
          "/dev/c": ("!0000000c", "Pergamon-Red")}


class FakeIface:
    opened = []

    def __init__(self, devPath):
        if devPath == "/dev/busy":
            raise OSError("Resource busy")
        self.port, self.closed = devPath, False
        FakeIface.opened.append(devPath)

    def getMyNodeInfo(self):
        nid, name = RADIOS[self.port]
        return {"user": {"id": nid, "longName": name}}

    def close(self):
        self.closed = True


class Serial:
    SerialInterface = FakeIface


@pytest.fixture
def setup():
    FakeIface.opened = []
    plugged = set()
    logs = []
    m = RadioManager(Serial, lambda: sorted(plugged), logs.append)
    return m, plugged, logs


def test_server_serves_client_is_left_alone_new_one_waits(setup):
    m, plugged, logs = setup
    radios.set_role("!0000000a", "server")
    radios.set_role("!0000000b", "client")
    plugged |= {"/dev/a", "/dev/b", "/dev/c", "/dev/busy"}
    m.scan()
    assert set(m.active) == {"/dev/a"}
    assert any("new radio Pergamon-Red !0000000c" in line for line in logs)
    opened = list(FakeIface.opened)
    m.scan()
    assert FakeIface.opened == opened  # clients and undecided aren't reopened every scan
    connected = {r["id"]: r for r in radios.connected()}
    assert connected["!0000000b"]["role"] == "client" and connected["!0000000c"]["role"] is None


def test_deciding_server_picks_it_up_and_client_lets_it_go(setup):
    m, plugged, logs = setup
    plugged |= {"/dev/c"}
    m.scan()
    assert m.active == {}
    radios.set_role("!0000000c", "server")
    m.scan()
    assert set(m.active) == {"/dev/c"} and m.primary().port == "/dev/c"
    radios.set_role("!0000000c", "client")
    m.scan()
    assert m.active == {}


def test_many_servers_and_unplugging(setup):
    m, plugged, logs = setup
    radios.set_role("!0000000a", "server")
    radios.set_role("!0000000c", "server")
    plugged |= {"/dev/a", "/dev/c"}
    m.scan()
    assert set(m.active) == {"/dev/a", "/dev/c"}
    iface_c = m.active["/dev/c"]
    plugged.discard("/dev/c")
    m.scan()
    assert set(m.active) == {"/dev/a"} and iface_c.closed
    plugged.add("/dev/c")  # plugged back in, maybe on the same port
    m.scan()
    assert set(m.active) == {"/dev/a", "/dev/c"}


def test_dead_or_silent_radios_are_reopened(setup):
    m, plugged, logs = setup
    m.silence_s = 60
    radios.set_role("!0000000a", "server")
    plugged.add("/dev/a")
    m.scan()
    first = m.active["/dev/a"]
    m.check_health(lambda iface: False, now=time.time() + 120)  # silent too long
    assert m.active == {} and first.closed
    m.scan()
    second = m.active["/dev/a"]
    assert second is not first
    m.check_health(lambda iface: iface is second)  # reader thread died
    assert m.active == {}


def test_fixed_port_is_always_served(setup):
    m, plugged, logs = setup
    m.fixed_port = "/dev/b"
    plugged.add("/dev/b")
    m.scan()
    assert set(m.active) == {"/dev/b"}


def test_only_server_radios_answer(setup, monkeypatch):
    from modules.comms.meshtastic import listen
    m, plugged, logs = setup
    monkeypatch.setattr(listen, "_manager", m)
    handled = []
    monkeypatch.setattr(listen, "_on_receive", lambda packet, iface: handled.append(iface))
    monkeypatch.setattr(listen, "_log_new_node", lambda packet, iface: None)
    radios.set_role("!0000000a", "server")
    plugged.add("/dev/a")
    m.scan()
    listen.on_receive({"fromId": "!x"}, m.active["/dev/a"])
    listen.on_receive({"fromId": "!x"}, FakeIface("/dev/b"))  # a handheld being probed
    assert handled == [m.active["/dev/a"]]


def test_roles_cli_and_migration(tmp_path, monkeypatch):
    from rag import install_config
    monkeypatch.setattr(install_config, "INSTALL_LOCAL_PATH", tmp_path / "i.yaml")
    (tmp_path / "i.yaml").write_text('comms:\n  meshtastic:\n    hub_node_id: "!e06e02f4"\n')
    assert radios.role("!e06e02f4") == "server"  # carried over from the old setting
    assert radios.main(["client", "e06fbbd0"]) == 0 and radios.role("!e06fbbd0") == "client"
    assert radios.main(["serve", "nonsense"]) == 1
    assert radios.main(["forget", "!e06fbbd0"]) == 0 and radios.role("!e06fbbd0") is None


def test_radios_page_is_host_only_and_sets_roles(monkeypatch):
    from fastapi.testclient import TestClient
    from rag import web
    radios.write_connected([{"port": "/dev/b", "id": "!0000000b", "name": "Pergamon-Yellow"}])
    c = TestClient(web.app)
    page = c.get("/radios").text
    assert "Pergamon-Yellow" in page and "new: choose below" in page
    assert c.post("/api/radios/role", json={"id": "!0000000b", "role": "client"}).json()["role"] == "client"
    assert radios.role("!0000000b") == "client"
    assert c.post("/api/radios/role", json={"id": "!0000000b", "role": "boss"}).status_code == 400
    monkeypatch.setattr(web, "_is_host", lambda request: False)
    assert c.get("/radios").status_code == 403
    assert c.post("/api/radios/role", json={"id": "!0000000b", "role": "server"}).status_code == 403
