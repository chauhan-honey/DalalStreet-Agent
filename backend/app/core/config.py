"""
CONFIGURATION LAYER  ──  backend/app/core/config.py
====================================================

WHAT THIS FILE IS
    The single place where the whole project reads its settings (secrets, file
    paths, model name, loop limits). Every other module imports the `settings`
    object from here instead of reading environment variables on its own. That
    keeps configuration in ONE spot, so there is exactly one thing to change.

HOW CONFIG REACHES THE APP (the flow)
    .env file on disk  ->  load_dotenv() copies it into the OS environment  ->
    os.getenv(...) reads each value  ->  the `settings` object holds them  ->
    other files do `from backend.app.core.config import settings`.

WHO USES THIS FILE
    - graph.py            reads GOOGLE_API_KEY, GEMINI_MODEL, MAX_CRITIC_ITERATIONS,
                          and MCP_SERVER_SCRIPT.
    - rag/vector_store.py reads CHROMA_PERSIST_DIR (where the vector database lives).
    - Basically anything that needs a secret or a path.

WHY "python-dotenv"
    In development we do not want to type secrets into the shell every time. The
    `python-dotenv` library reads a local `.env` file (which is git-ignored) and
    loads those key=value pairs into the process environment automatically.
"""
import os

from dotenv import load_dotenv

# Read the local ".env" file (if present) and load its KEY=VALUE lines into the
# process environment so os.getenv(...) below can see them. Called once at import.
load_dotenv()


class Settings:
    """Typed container for every configuration value the app needs.

    This is a plain class used as a namespace. We create ONE instance below
    (`settings`) and import that everywhere. Reading `settings.GOOGLE_API_KEY`
    is clearer and safer than scattering `os.getenv("GOOGLE_API_KEY")` calls
    across many files.
    """

    # Human-readable project name (used in the FastAPI docs / metadata).
    PROJECT_NAME: str = "DalalStreet-Agent"

    # SECRET: the Google Gemini API key. os.getenv returns "" if it is unset, so
    # the validation block at the bottom can catch a missing key and stop early.
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

    # Folder on disk where ChromaDB stores the embedded PDF chunks. Persisting to
    # disk means ingested reports survive a restart (no need to re-ingest).
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")

    # Path to the FastMCP market-data server script. graph.py launches this file
    # as a separate Python process (a "tool server") to fetch live stock data.
    MCP_SERVER_SCRIPT: str = os.getenv(
        "MCP_SERVER_SCRIPT", "backend/app/mcp_server/stock_service.py"
    )

    # The LLM model id. Gemini 3.6 Flash is the current free-tier flash model
    # Google recommends for new keys (1.5 and 2.5 are retired). graph.py passes
    # this to the LangChain client.
    GEMINI_MODEL: str = "gemini-3.6-flash"

    # Safety limit for the critic/auditor retry loop in graph.py. After this many
    # passes the loop is forced to stop, preventing an endless (and costly) cycle.
    MAX_CRITIC_ITERATIONS: int = 2


# Build the ONE shared settings instance. Importing modules use this object.
settings = Settings()

# Fail fast: if the API key is missing we stop the program immediately at import
# time with a clear message, instead of crashing deep inside an LLM call later.
# NOTE: we never print the key value itself, only report that it is absent.
if not settings.GOOGLE_API_KEY:
    raise ValueError(
        "GOOGLE_API_KEY environment variable is missing. "
        "Obtain a free key from Google AI Studio and set it in .env"
    )
