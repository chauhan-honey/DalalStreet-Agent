# Skill: ChromaDB Hybrid RAG

> PDF ingestion with PyMuPDF, chunking strategy, local MiniLM embeddings, and metadata-filtered querying by stock ticker — the zero-cost retrieval layer.

---

## 1. Design goals

- **Zero cost:** embeddings run locally on CPU via `sentence-transformers/all-MiniLM-L6-v2`; the store is embedded/persistent ChromaDB. No hosted vector DB, no paid embedding API.
- **Ticker isolation:** every chunk is tagged with its `ticker` so retrieval never bleeds one company's report into another's.
- **Citation-ready:** every chunk carries its source `page` so the synthesizer can cite it.

---

## 2. Store construction

```python
import chromadb
from chromadb.utils import embedding_functions

class FinancialVectorStore:
    def __init__(self, persist_dir: str = settings.CHROMA_PERSIST_DIR):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="sentence-transformers/all-MiniLM-L6-v2"    # local, CPU, free
        )
        self.collection = self.client.get_or_create_collection(
            name="annual_reports",
            embedding_function=self.embedding_fn,
        )
```

- `PersistentClient(path=...)` writes to disk (`./data/chroma_db`) so ingestion survives restarts.
- The embedding function is attached to the collection, so `query_texts` are embedded automatically at query time with the **same** model used at ingest — never mix models.

---

## 3. PDF ingestion with PyMuPDF (fitz)

```python
import fitz   # PyMuPDF

def ingest_pdf(self, file_path: str, ticker: str) -> int:
    doc = fitz.open(file_path)
    chunks, metadatas, ids = [], [], []
    chunk_id = 0
    for page_num in range(len(doc)):
        text = doc[page_num].get_text("text")
        step, size = 800, 1000                 # 200-char overlap window
        for i in range(0, len(text), step):
            chunk = text[i:i + size].strip()
            if len(chunk) > 100:               # drop headers/whitespace noise
                chunks.append(chunk)
                metadatas.append({"ticker": ticker, "page": page_num + 1})
                ids.append(f"{ticker}_p{page_num + 1}_c{chunk_id}")
                chunk_id += 1
    if chunks:
        self.collection.upsert(documents=chunks, metadatas=metadatas, ids=ids)
    return len(chunks)
```

### Chunking strategy
| Parameter | Value | Rationale |
| --- | --- | --- |
| `size` (window) | 1000 chars | fits MiniLM context comfortably, keeps a full paragraph |
| `step` (stride) | 800 chars | 200-char overlap preserves context across boundaries |
| min length | >100 chars | discards page numbers, headers, blank fragments |
| id scheme | `{ticker}_p{page}_c{n}` | deterministic, `upsert`-safe (re-ingest overwrites, no duplicates) |

- **Page-aware:** iterate page-by-page so `page` metadata is exact for citations.
- **`upsert` over `add`:** re-ingesting the same report updates in place instead of duplicating.

### Ingestion resilience (see QA skill)
- Wrap `fitz.open` — corrupted/encrypted/non-PDF files must skip, not crash.
- A page with no extractable text (scanned image) yields zero chunks; that's acceptable, log and continue.

---

## 4. Metadata-filtered query by ticker

```python
def query(self, query_text: str, ticker: str, top_k: int = 4) -> list[dict]:
    results = self.collection.query(
        query_texts=[query_text],
        n_results=top_k,
        where={"ticker": ticker},        # hard isolation per company
    )
    output = []
    if results and results["documents"]:
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            output.append({"content": doc, "page": meta["page"]})
    return output
```

- The `where={"ticker": ticker}` filter is **mandatory** — without it, a query for TCS could retrieve Reliance's chunks.
- Return shape `{"content": str, "page": int}` is the contract the RAG worker and synthesizer depend on.
- `top_k` default 4; the RAG worker may request fewer per sub-query (e.g. `top_k=2` across 2 queries).

---

## 5. "Hybrid" retrieval note

Semantic (vector) retrieval is filtered by structured metadata (`ticker`, and optionally `page` ranges). That metadata pre-filter + dense-vector search **is** the hybrid strategy here — keep both halves: never drop the `where` filter for "broader" results.

---

## 6. Anti-patterns

- Different embedding models at ingest vs query time.
- Querying without the `ticker` filter.
- `add` instead of `upsert` (duplicate chunks on re-ingest).
- No minimum-length gate (indexing whitespace/noise).
- Letting a malformed PDF raise out of ingestion.
- Any paid/hosted embedding or vector backend.

---

## 7. Validation

1. Ingest a known report; assert chunk count > 0 and ids are unique.
2. Query with a ticker filter; assert every result's page exists in that report.
3. Query a ticker with no ingested data; assert `[]` (empty, no crash).
4. Feed an encrypted/garbage PDF; assert graceful skip.
