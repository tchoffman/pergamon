import pytest


@pytest.fixture(autouse=True)
def _keep_listener_log_out_of_build(tmp_path, monkeypatch):
    """Tests must never write into the real build/mesh/listener.log."""
    from modules.comms.meshtastic import listen
    monkeypatch.setattr(listen.console, "path", tmp_path / "listener.log")


@pytest.fixture(autouse=True)
def _keep_hub_identity_out_of_build(tmp_path, monkeypatch):
    """Tests get their own hub key and trusted-hub list, never the real ones."""
    from rag import hubs
    monkeypatch.setattr(hubs, "STATE", tmp_path / "hubstate")
    monkeypatch.setattr(hubs, "KEY_PATH", tmp_path / "hubstate" / "hub.key")
    monkeypatch.setattr(hubs, "TRUST_PATH", tmp_path / "hubstate" / "trusted_hubs.json")
