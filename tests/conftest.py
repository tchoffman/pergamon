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


@pytest.fixture(autouse=True)
def _keep_feeds_out_of_build(tmp_path, monkeypatch):
    from rag import feeds
    monkeypatch.setattr(feeds, "SUBS_PATH", tmp_path / "feeds.json")
    monkeypatch.setattr(feeds, "SENT_PATH", tmp_path / "feeds_sent.json")


@pytest.fixture(autouse=True)
def _guide_offline(tmp_path, monkeypatch):
    """Tests get their own frost-date cache and never call NOAA."""
    from rag import guide
    monkeypatch.setattr(guide, "FROST_PATH", tmp_path / "frost.json")

    def offline(*a, **k):
        raise guide.GuideError("no network in tests")
    monkeypatch.setattr(guide, "_real_fetch_frost", guide.fetch_frost, raising=False)
    monkeypatch.setattr(guide, "fetch_frost", offline)
