import fitz  # PyMuPDF
import chromadb
from chromadb.utils import embedding_functions
from typing import List, Dict
from backend.app.core.config import settings

class FinancialVectorStore:
    def __init__(self, persist_dir: str = settings.CHROMA_PERSIST_DIR):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
        self.collection = self.client.get_or_create_collection(
            name="annual_reports",
            embedding_function=self.embedding_fn
        )

    def ingest_pdf(self, file_path: str, ticker: str):
        """Extracts text from PDF and indexes chunks with ticker and page metadata."""
        doc = fitz.open(file_path)
        chunks, metadatas, ids = [], [], []
        chunk_id = 0

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            step, size = 800, 1000
            for i in range(0, len(text), step):
                chunk = text[i:i+size].strip()
                if len(chunk) > 100:
                    chunks.append(chunk)
                    metadatas.append({"ticker": ticker, "page": page_num + 1})
                    ids.append(f"{ticker}_p{page_num+1}_c{chunk_id}")
                    chunk_id += 1

        if chunks:
            self.collection.upsert(documents=chunks, metadatas=metadatas, ids=ids)

    def query(self, query_text: str, ticker: str, top_k: int = 4) -> List[Dict]:
        """Performs metadata-filtered vector search for a specific company ticker."""
        results = self.collection.query(
            query_texts=[query_text],
            n_results=top_k,
            where={"ticker": ticker}
        )
        output = []
        if results and results["documents"]:
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                output.append({"content": doc, "page": meta["page"]})
        return output