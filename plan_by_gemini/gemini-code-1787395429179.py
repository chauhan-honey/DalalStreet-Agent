import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "DalalStreet-Agent"
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
    MCP_SERVER_SCRIPT: str = os.getenv("MCP_SERVER_SCRIPT", "backend/app/mcp_server/stock_service.py")

settings = Settings()

if not settings.GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable is missing. Obtain a free key from Google AI Studio.")