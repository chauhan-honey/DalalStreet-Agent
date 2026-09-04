"""
INGESTION SCRIPT  ──  scripts/ingest_pdf.py
===========================================

WHAT THIS FILE IS
    A small command-line tool you run ONCE per annual report to load it into the
    local vector database. After ingestion, the RAG worker in graph.py can search
    that report during live runs. This is a one-off setup step, separate from the
    web server.

HOW TO RUN IT
    python scripts/ingest_pdf.py --file data/annual_reports/TCS_FY24.pdf --ticker TCS.NS

WHAT HAPPENS (the flow)
    THIS SCRIPT ── FinancialVectorStore.ingest_pdf(file, ticker) ──► rag/vector_store.py
      -> PyMuPDF extracts text -> text is chunked -> chunks embedded locally
      -> stored in ChromaDB on disk (at CHROMA_PERSIST_DIR from config.py).
    Every chunk is tagged with `ticker`, so later searches stay scoped to the
    right company.

WHY argparse
    `argparse` is Python's standard library for reading command-line options. It
    turns `--file ...` and `--ticker ...` into variables and auto-generates a
    `--help` message.
"""
import argparse
import os
import sys

# Running "python scripts/ingest_pdf.py" puts the scripts/ folder on sys.path, not
# the repo root, so the `backend` package would not be importable. Add the repo
# root (the parent of this file's directory) so the import below always works.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.rag.vector_store import FinancialVectorStore


def main() -> None:
    """Parse CLI arguments and ingest the given PDF under the given ticker."""
    # Declare the two required command-line options and read the values passed.
    parser = argparse.ArgumentParser(description="Ingest an annual-report PDF into ChromaDB.")
    parser.add_argument("--file", required=True, help="Path to the source PDF.")
    parser.add_argument("--ticker", required=True, help="Ticker to tag chunks with, e.g. TCS.NS")
    args = parser.parse_args()

    # Open the vector store (this loads the embedding model + opens ChromaDB) and
    # ingest the file. ingest_pdf returns how many chunks it stored.
    store = FinancialVectorStore()
    count = store.ingest_pdf(args.file, args.ticker)
    # Report the outcome. A count of 0 means the PDF was unreadable or empty.
    if count:
        print(f"Ingestion complete: {count} chunks indexed for {args.ticker}.")
    else:
        print(f"No chunks indexed for {args.ticker} (unreadable or empty PDF).")


if __name__ == "__main__":
    # Only run main() when executed directly (not when imported).
    main()
