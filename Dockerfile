# syntax=docker/dockerfile:1
#
# PRODUCTION DOCKERFILE — DalalStreet-Agent backend
# ===================================================
# Multi-stage build: stage 1 ("builder") installs Python dependencies into an
# isolated virtualenv; stage 2 ("runtime") copies ONLY that venv + the app
# source into a clean slim image. This keeps the final image free of build
# tools, pip's download cache, and other build-time cruft — smaller image,
# faster cold starts, smaller attack surface.
#
# Runtime pinned to Python 3.12 (not whatever version your laptop's venv
# uses) because it has mature, well-tested wheel support across every heavy
# dependency here (torch, chromadb, pymupdf) — a container should target a
# deliberately chosen, well-supported runtime, not mirror the dev machine.

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

# Native build tools needed to compile any dependency that ships no prebuilt
# wheel for this platform (e.g. some chromadb/tokenizers transitive deps).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Build dependencies into a self-contained venv (not the system interpreter),
# so stage 2 can copy this one directory and get everything it needs.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY backend/requirements.txt .

# Install the CPU-only torch wheel FIRST, from PyTorch's own CPU index. The
# default PyPI torch wheel bundles CUDA libraries for GPU support we will
# never use on a free-tier CPU host — that alone is ~300MB of dead weight.
# Pinning to the cpu index keeps sentence-transformers' embedding model
# (used by chromadb's SentenceTransformerEmbeddingFunction) fully functional
# at a fraction of the size.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

# Run as a non-root user — never run a production container as root.
RUN groupadd --system app && useradd --system --gid app --create-home app

# Bring in the fully-built venv from stage 1. This is the only thing we take
# from the builder — no compilers, no caches, no source downloads.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Application code.
COPY backend/ backend/
COPY scripts/ scripts/

# Bake the already-ingested vector store + source PDFs into the image itself.
# Render's free tier wipes any writable disk on every redeploy, so the app
# cannot depend on runtime-written state — the image has to be self-contained.
# Re-ingesting a new report means rebuilding the image (via `git push`, which
# CI does automatically), not editing a running container.
COPY data/chroma_db/ data/chroma_db/
COPY data/annual_reports/ data/annual_reports/

# Config defaults; GOOGLE_API_KEY is intentionally NOT set here — it is
# injected as a secret by the hosting platform at deploy time, never baked
# into the image (see .claude/skills/secure-api-key-handling.md).
ENV CHROMA_PERSIST_DIR=/app/data/chroma_db \
    MCP_SERVER_SCRIPT=backend/app/mcp_server/stock_service.py \
    PORT=8000

RUN chown -R app:app /app
USER app

EXPOSE 8000

# Liveness probe used by `docker run --health-cmd` locally and readable by
# any orchestrator that inspects container health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/api/v1/health').read()" || exit 1

# Exec-form CMD invoking `sh -c`: gets both worlds — ${PORT} is still expanded
# at container start (Render assigns it dynamically per instance, so it can't
# be hardcoded), while `sh` still runs as PID 1 and correctly forwards SIGTERM
# to uvicorn for graceful shutdown, instead of swallowing it (plain shell-form
# CMD would run via an implicit shell that does not forward signals cleanly).
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT}"]
