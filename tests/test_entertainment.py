"""Watch & Listen: the pack, installing it, and streaming to phones."""
import pytest
from fastapi.testclient import TestClient

from rag import entertainment as ent
from rag import web

FILM = {"id": "chaplin", "kind": "film", "title": "Chaplin Festival", "creator": "Charlie Chaplin",
        "year": "1938", "license": "Public domain", "license_url": "http://creativecommons.org/licenses/publicdomain/",
        "source": "https://archive.org/details/chaplin", "sampler": True,
        "files": [{"name": "chaplin_512kb.mp4", "bytes": 1000, "seconds": 3600}]}
BOOK = {**FILM, "id": "peter_pan", "kind": "audiobook", "title": "Peter Pan", "creator": "J. M. Barrie",
        "files": [{"name": "peterpan_01_64kb.mp3", "bytes": 300, "seconds": 600},
                  {"name": "peterpan_02_64kb.mp3", "bytes": 300, "seconds": 700}]}


def fake_download(url, dest):
    size = 1000 if dest.name.endswith(".mp4") else 300
    dest.write_bytes(bytes(i % 256 for i in range(size)))


def test_the_real_pack_lists_films_audiobooks_and_music():
    full, sampler = ent.pack("full"), ent.pack("sampler")
    assert {i["kind"] for i in full} == {"film", "audiobook", "music"}
    assert set(i["id"] for i in sampler) < set(i["id"] for i in full)
    assert 0.5e9 < ent.pack_bytes("sampler") < ent.pack_bytes("full") < 5e9
    with pytest.raises(ent.MediaError):
        ent.pack("everything")


def test_install_downloads_once_and_notes_details():
    meta = lambda ident: {"licenseurl": FILM["license_url"]}  # noqa: E731
    calls = []

    def dl(url, dest):
        calls.append(url)
        fake_download(url, dest)
    assert ent.install_item(FILM, metadata=meta, download=dl, log=lambda m: None) == 1000
    assert ent.install_item(FILM, metadata=meta, download=dl, log=lambda m: None) == 0  # already there
    assert calls == ["https://archive.org/download/chaplin/chaplin_512kb.mp4"]
    lib = ent.library()
    assert [i["title"] for i in lib] == ["Chaplin Festival"] and lib[0]["source"].startswith("https://archive.org")


def test_half_downloaded_items_are_not_listed():
    ent.install_item(BOOK, metadata=lambda i: {}, download=fake_download, log=lambda m: None)
    (ent.media_dir() / "peter_pan" / "peterpan_02_64kb.mp3").unlink()
    assert ent.library() == []


def test_pages_and_streaming_with_seek():
    for item in (FILM, BOOK):
        ent.install_item(item, metadata=lambda i: {}, download=fake_download, log=lambda m: None)
    c = TestClient(web.app)
    home = c.get("/media").text
    assert "Films" in home and "Audiobooks" in home and 'href="/media/peter_pan"' in home
    film = c.get("/media/chaplin").text
    assert "<video" in film and "/media/files/chaplin/chaplin_512kb.mp4" in film
    book = c.get("/media/peter_pan").text
    assert "<audio" in book and book.count('class="linklike track"') == 2
    r = c.get("/media/files/chaplin/chaplin_512kb.mp4", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4" and len(r.content) == 100
    assert c.get("/media/files/peter_pan/peterpan_01_64kb.mp3").headers["content-type"] == "audio/mpeg"


def test_only_library_files_are_served():
    ent.install_item(FILM, metadata=lambda i: {}, download=fake_download, log=lambda m: None)
    c = TestClient(web.app)
    assert c.get("/media/files/chaplin/info.json").status_code == 404
    assert c.get("/media/files/chaplin/..%2F..%2Fsecret").status_code == 404
    assert c.get("/media/nope").status_code == 404


def test_empty_library_explains_how_to_add_it():
    assert "pergamon media install sampler" in TestClient(web.app).get("/media").text


def test_reachability_check_compares_sizes(monkeypatch):
    monkeypatch.setattr(ent, "pack", lambda variant: [
        FILM, {**BOOK, "files": [{"name": "x.mp3", "bytes": 5, "seconds": 1}]}])
    res = ent.check_reachable("sampler", head=lambda url: {"chaplin_512kb.mp4": 1000}.get(url.rsplit("/", 1)[1], 9))
    assert res[0][1] is True and res[1][1] is False and "!=" in res[1][2]
