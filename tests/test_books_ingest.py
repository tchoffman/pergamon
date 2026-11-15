from __future__ import annotations

import subprocess
import sys

from rag import ingest
from rag.install_config import load_catalog


def test_extract_txt_strips_gutenberg_boilerplate(tmp_path):
    p = tmp_path / "book.txt"
    p.write_text(
        "The Project Gutenberg eBook of Mushrooms\nLicense blah\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK MUSHROOMS ***\n"
        "Chapter 1. The chanterelle.\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK MUSHROOMS ***\nMore license text\n"
    )
    assert ingest.extract_txt(p) == "Chapter 1. The chanterelle."


def test_extract_txt_plain_file_untouched(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("just notes\n")
    assert ingest.extract_txt(p) == "just notes"


def test_corpus_documents_includes_books_and_pdfs(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "REPO_ROOT", tmp_path)
    (tmp_path / "build/corpus/pdfs").mkdir(parents=True)
    (tmp_path / "build/corpus/books").mkdir(parents=True)
    (tmp_path / "build/corpus/pdfs/a.pdf").write_bytes(b"%PDF")
    (tmp_path / "build/corpus/books/b.txt").write_text("x")
    names = [(p.name, fn.__name__) for p, fn in ingest.corpus_documents()]
    assert names == [("a.pdf", "extract_pdf"), ("b.txt", "extract_txt")]


def test_foraging_shelf_catalog_entry():
    item = load_catalog()["foraging-shelf"]
    assert item["fetcher"] == "gutenberg"
    ids = [b["id"] for b in item["gutenberg_ids"]]
    assert len(ids) == len(set(ids)) == 12


def test_dispatcher_passes_curated_ids():
    out = subprocess.run(["bash", "scripts/fetch-corpus.sh"], env={"DRY_RUN": "1", "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert "foraging-shelf" in out.stdout
