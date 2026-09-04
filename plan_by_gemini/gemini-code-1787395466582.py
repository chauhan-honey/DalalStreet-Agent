from backend.app.rag.vector_store import FinancialVectorStore
store = FinancialVectorStore()
store.ingest_pdf("data/annual_reports/TCS_FY24.pdf", "TCS.NS")
print("Ingestion complete.")