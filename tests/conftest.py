import pytest


@pytest.fixture(autouse=True)
def _keep_listener_log_out_of_build(tmp_path, monkeypatch):
    """Tests must never write into the real build/mesh/listener.log."""
    from modules.comms.meshtastic import listen
    monkeypatch.setattr(listen.console, "path", tmp_path / "listener.log")
