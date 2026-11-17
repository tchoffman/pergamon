import pytest
import yaml

from rag import install_config, storage


@pytest.fixture
def local(tmp_path, monkeypatch):
    """A throwaway install.local.yaml, with no install-test sandbox in play."""
    monkeypatch.delenv("PERGAMON_SANDBOX", raising=False)
    path = tmp_path / "install.local.yaml"
    monkeypatch.setattr(install_config, "INSTALL_LOCAL_PATH", path)
    return path


def test_default_is_build(local):
    assert storage.data_dir() == storage.DEFAULT


def test_configured_drive_is_used(local, tmp_path):
    drive = tmp_path / "Drive" / "pergamon"
    drive.mkdir(parents=True)
    local.write_text(yaml.safe_dump({"storage": {"data_dir": str(drive)}}))
    assert storage.data_dir() == drive
    from rag.config import load_config
    assert load_config()["chroma_dir"] == str(drive / "chroma")


def test_unplugged_drive_stops_instead_of_filling_the_laptop(local, tmp_path):
    gone = tmp_path / "Unplugged" / "pergamon"
    local.write_text(yaml.safe_dump({"storage": {"data_dir": str(gone)}}))
    with pytest.raises(storage.StorageMissing, match="plug the drive in"):
        storage.data_dir()
    from rag.config import load_config
    with pytest.raises(storage.StorageMissing):
        storage.chroma_client(load_config())
    assert not gone.exists()  # nothing was created at the drive's path


def test_use_moves_library_and_keeps_other_settings(local, tmp_path, monkeypatch):
    old = tmp_path / "build"
    (old / "corpus" / "zims").mkdir(parents=True)
    (old / "corpus" / "zims" / "w.zim").write_bytes(b"ZIM\x04data")
    (old / "chroma").mkdir()
    (old / "chroma" / "db").write_text("x")
    monkeypatch.setattr(storage, "DEFAULT", old)
    local.write_text(yaml.safe_dump({"location": "Somewhere", "comms": {"x": 1}}))
    drive = tmp_path / "Drive"
    drive.mkdir()
    assert storage.use(drive / "pergamon", move=True) == 0
    assert (drive / "pergamon" / "corpus" / "zims" / "w.zim").read_bytes() == b"ZIM\x04data"
    assert (old / "corpus" / "zims" / "w.zim").exists()  # originals are kept
    cfg = yaml.safe_load(local.read_text())
    assert cfg["storage"]["data_dir"] == str((drive / "pergamon").resolve())
    assert cfg["location"] == "Somewhere" and cfg["comms"] == {"x": 1}
    # and back to this computer
    assert storage.use(old) == 0
    assert "storage" not in yaml.safe_load(local.read_text())


def test_use_refuses_a_missing_drive_and_fat32(local, tmp_path, monkeypatch):
    assert storage.use(tmp_path / "NoSuchDrive" / "pergamon") == 1
    monkeypatch.setattr(storage, "filesystem", lambda p: "msdos")
    (tmp_path / "Fat").mkdir()
    assert storage.use(tmp_path / "Fat" / "pergamon") == 1
    assert not local.exists()


def test_filesystem_reads_mount_table():
    assert storage.filesystem(storage.REPO_ROOT)  # e.g. apfs / ext4


def test_setup_save_keeps_settings_it_does_not_own(local):
    local.write_text(yaml.safe_dump({"location": "X", "hub": {"https": True},
                                     "storage": {"data_dir": "/d"}, "models": {"primary": "old"}}))
    install_config.save_local_config("minimal", models={"primary": "new"})
    cfg = yaml.safe_load(local.read_text())
    assert cfg["preset"] == "minimal" and cfg["models"] == {"primary": "new"}
    assert cfg["location"] == "X" and cfg["hub"] == {"https": True} and cfg["storage"] == {"data_dir": "/d"}


def test_library_search_with_drive_unplugged_still_answers(local, tmp_path):
    from rag import agent
    from rag.config import load_config
    local.write_text(yaml.safe_dump({"storage": {"data_dir": str(tmp_path / "gone")}}))
    run = agent.Run()
    out = agent._search_library(load_config(), run)("sourdough starter")
    assert "own knowledge" in out
    assert run.notices == ["The library drive isn't connected, so this answer doesn't use it."]
