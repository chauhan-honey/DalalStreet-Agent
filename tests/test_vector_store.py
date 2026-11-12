"""Tests for the RAG store (backend/app/rag/vector_store.py).

Each test uses its own throwaway ChromaDB directory and small generated PDFs,
so the real ingested store under data/chroma_db/ is never opened or modified.
"""
import fitz  # PyMuPDF
import pytest

from backend.app.rag.vector_store import FinancialVectorStore

ACME_TEXT = ("Acme management states that cloud services margins expanded steadily "
             "this fiscal year, driven by automation and pricing discipline. ") * 6
GLOBEX_TEXT = ("Globex reports that its refining segment faced weaker crack spreads "
               "and that petrochemical volumes declined during the year. ") * 6


def _make_pdf(path, pages):
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(36, 36, 559, 806), text, fontsize=9)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def store(tmp_path):
    return FinancialVectorStore(persist_dir=str(tmp_path / "chroma"))


def test_ingest_tags_chunks_with_page_numbers(store, tmp_path):
    pdf = _make_pdf(tmp_path / "acme.pdf", ["short", ACME_TEXT, ACME_TEXT])
    count = store.ingest_pdf(pdf, "ACME.NS")

    assert count >= 2  # page 1 is below the minimum chunk length and is skipped
    hits = store.query("cloud margins", "ACME.NS", top_k=10)
    assert {h["page"] for h in hits} == {2, 3}
    assert all("cloud services margins" in h["content"] for h in hits)


def test_queries_never_cross_tickers(store, tmp_path):
    store.ingest_pdf(_make_pdf(tmp_path / "acme.pdf", [ACME_TEXT]), "ACME.NS")
    store.ingest_pdf(_make_pdf(tmp_path / "globex.pdf", [GLOBEX_TEXT]), "GLOBEX.NS")

    # Asked about refining, but scoped to ACME: must only ever return ACME text.
    hits = store.query("refining crack spreads", "ACME.NS", top_k=10)
    assert hits and all("Acme" in h["content"] for h in hits)


def test_reingesting_the_same_report_does_not_duplicate(store, tmp_path):
    pdf = _make_pdf(tmp_path / "acme.pdf", [ACME_TEXT, ACME_TEXT])
    first = store.ingest_pdf(pdf, "ACME.NS")
    store.ingest_pdf(pdf, "ACME.NS")
    assert store.collection.count() == first


def test_malformed_pdf_is_skipped_not_raised(store, tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is not a pdf")
    assert store.ingest_pdf(str(bad), "BAD.NS") == 0


def test_unknown_ticker_returns_no_results(store, tmp_path):
    store.ingest_pdf(_make_pdf(tmp_path / "acme.pdf", [ACME_TEXT]), "ACME.NS")
    assert store.query("cloud margins", "UNKNOWN.NS") == []
