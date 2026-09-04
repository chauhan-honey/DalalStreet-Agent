"""
RETRIEVAL / RAG STORAGE  ──  backend/app/rag/vector_store.py
============================================================

WHAT THIS FILE IS
    The project's "document memory". It takes a company's Annual Report (a PDF),
    splits it into small pieces, converts each piece into a numeric vector
    ("embedding"), and stores them in a local database. Later it can find the
    pieces most relevant to a question. This technique is called RAG:
    Retrieval-Augmented Generation — we RETRIEVE real text from the report and
    feed it to the LLM so its answers are grounded in the actual document.

THE THREE LIBRARIES (plain-English)
    - PyMuPDF (imported as `fitz`): reads a PDF and extracts its raw text, page
      by page.
    - sentence-transformers ("all-MiniLM-L6-v2"): a small AI model that turns a
      piece of text into a vector of numbers. Texts with similar meaning get
      similar vectors. It runs locally on the CPU and is free (no API cost).
    - ChromaDB: a vector database. It stores the vectors + the original text +
      metadata, and can quickly find the vectors closest to a query vector.

WHERE THIS SITS IN THE PROJECT (the flow)
    INGESTION (offline, run once per report):
        scripts/ingest_pdf.py  ->  FinancialVectorStore.ingest_pdf(pdf, ticker)
        -> text chunks stored in ChromaDB on disk (CHROMA_PERSIST_DIR).

    QUERYING (during a live run):
        graph.py (rag_worker_node)  ->  FinancialVectorStore.query(question, ticker)
        -> returns the most relevant report snippets (with page numbers) which
           become `rag_context` in the shared state.

KEY IDEA: "chunking"
    An LLM cannot search a 300-page PDF directly, and whole pages are too coarse.
    So we cut the text into overlapping ~1000-character windows ("chunks"). The
    overlap (200 chars) makes sure a sentence split across a boundary still
    appears whole in at least one chunk.
"""
import logging
from typing import Any, Dict, List

import chromadb
import fitz  # PyMuPDF — reads PDFs and extracts text
from chromadb.utils import embedding_functions

from backend.app.core.config import settings

# Standard logger; used to warn about unreadable PDFs without crashing ingestion.
logger = logging.getLogger(__name__)

# Chunking parameters. A chunk is a window of text; we slide the window forward by
# _CHUNK_STEP each time, so consecutive chunks overlap by (_CHUNK_SIZE - _CHUNK_STEP).
_CHUNK_SIZE = 1000    # characters kept per chunk
_CHUNK_STEP = 800     # how far the window slides; 1000 - 800 = 200 chars of overlap
_MIN_CHUNK_LEN = 100  # ignore fragments shorter than this (page numbers, blank lines)


class FinancialVectorStore:
    """Ingests annual-report PDFs and answers similarity queries scoped to one ticker.

    One instance owns a connection to the on-disk ChromaDB and the local embedding
    model. graph.py creates a single shared instance; ingest_pdf.py creates its own.
    """

    def __init__(self, persist_dir: str = settings.CHROMA_PERSIST_DIR):
        # PersistentClient writes the database to disk at `persist_dir`, so data
        # ingested in one run is still there in the next (no re-ingestion needed).
        self.client = chromadb.PersistentClient(path=persist_dir)

        # The embedding function that turns text -> vector. It MUST be the same
        # model at ingest time and at query time, otherwise the vectors are not
        # comparable. This model runs locally on CPU and costs nothing.
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )

        # A "collection" is like a table in the vector DB. We keep ALL companies'
        # reports in one collection and separate them using a `ticker` metadata
        # tag on every chunk (see the `where` filter in query()).
        self.collection = self.client.get_or_create_collection(
            name="annual_reports",
            embedding_function=self.embedding_fn,
        )

    def ingest_pdf(self, file_path: str, ticker: str) -> int:
        """Read a PDF, split it into page-tagged chunks, and store them in ChromaDB.

        Args:
            file_path: path to the annual-report PDF on disk.
            ticker:    the company symbol to tag every chunk with (e.g. "TCS.NS"),
                       so later queries can be filtered to just this company.
        Returns:
            The number of chunks indexed (0 if the PDF was unreadable/empty).

        A malformed, encrypted, or non-PDF file is skipped (a warning is logged),
        never raised — so a bad file cannot crash a batch ingestion.
        """
        # Open defensively: fitz.open raises on a corrupt/encrypted/non-PDF file.
        try:
            doc = fitz.open(file_path)
        except Exception as exc:
            logger.warning("Skipping unreadable PDF %s: %s", file_path, exc)
            return 0

        # ChromaDB's upsert() wants three parallel lists: the texts, one metadata
        # dict per text, and one unique id per text. We build them together.
        chunks: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []
        chunk_id = 0

        # Go page by page so we can record the EXACT page number on each chunk;
        # that page number later becomes the citation shown in the final report.
        for page_num in range(len(doc)):
            try:
                text = doc[page_num].get_text("text")
            except Exception:
                # A scanned/image-only page has no extractable text — skip it.
                continue
            # Slide the fixed-size window across this page's text with overlap.
            for i in range(0, len(text), _CHUNK_STEP):
                chunk = text[i:i + _CHUNK_SIZE].strip()
                # Drop near-empty fragments so we do not index noise.
                if len(chunk) > _MIN_CHUNK_LEN:
                    chunks.append(chunk)
                    # page_num is 0-based; +1 makes it the human page number.
                    metadatas.append({"ticker": ticker, "page": page_num + 1})
                    # A deterministic id means re-ingesting the same report UPDATES
                    # existing rows (upsert) instead of creating duplicates.
                    ids.append(f"{ticker}_p{page_num + 1}_c{chunk_id}")
                    chunk_id += 1

        # Embed and store everything in one batch. upsert() computes the vectors
        # via self.embedding_fn automatically. Skipped if the PDF yielded nothing.
        if chunks:
            self.collection.upsert(documents=chunks, metadatas=metadatas, ids=ids)
        return len(chunks)

    def query(self, query_text: str, ticker: str, top_k: int = 4) -> List[Dict[str, Any]]:
        """Find the report chunks most relevant to a question, for ONE company.

        Args:
            query_text: the natural-language search query (from the planner).
            ticker:     restrict the search to this company's chunks only.
            top_k:      how many of the closest chunks to return.
        Returns:
            A list of {"content": <chunk text>, "page": <page number>} dicts.
            Called by graph.py's rag_worker_node; the result feeds `rag_context`.
        """
        # ChromaDB embeds `query_text` with the SAME model used at ingest, then
        # returns the top_k nearest chunks. The `where` filter is essential: it
        # limits results to this ticker so, e.g., a TCS query never returns
        # Reliance's text.
        results = self.collection.query(
            query_texts=[query_text],
            n_results=top_k,
            where={"ticker": ticker},  # hard isolation: never bleed across companies
        )
        # ChromaDB returns nested lists (one inner list per query text; we sent 1).
        # Flatten to a simple list of {content, page} dicts that the rest of the
        # app understands.
        output: List[Dict[str, Any]] = []
        if results and results.get("documents") and results["documents"][0]:
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                output.append({"content": doc, "page": meta.get("page")})
        return output
