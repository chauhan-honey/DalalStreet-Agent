# Skill: Secure API Key Handling

> Environment-variable loading with python-dotenv, zero-leakage checks, and Google Gemini free-tier quota management.

---

## 1. The one rule

Secrets live **only** in the environment, loaded once through `core/config.py`. No secret is ever hardcoded, logged, serialised into an SSE frame, committed, or printed. Everything below enforces that.

---

## 2. Loading via python-dotenv

```python
import os
from dotenv import load_dotenv

load_dotenv()                                    # reads .env into the environment, once


class Settings:
    PROJECT_NAME: str = "DalalStreet-Agent"
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
    MCP_SERVER_SCRIPT: str = os.getenv("MCP_SERVER_SCRIPT",
                                       "backend/app/mcp_server/stock_service.py")


settings = Settings()

# Fail fast, fail loud — but NEVER print the key itself.
if not settings.GOOGLE_API_KEY:
    raise ValueError(
        "GOOGLE_API_KEY is missing. Obtain a free key from Google AI Studio and set it in .env"
    )
```

- Single source of truth: every module imports `settings`, never calls `os.getenv` directly.
- The fail-fast check validates *presence*, and the error message never echoes the value.

---

## 3. Repository hygiene

`.gitignore` (must contain):
```gitignore
.env
.venv/
data/chroma_db/
__pycache__/
*.pyc
```

`.env.example` (committed — placeholders only, never real values):
```dotenv
GOOGLE_API_KEY=your_gemini_api_key_here
CHROMA_PERSIST_DIR=./data/chroma_db
MCP_SERVER_SCRIPT=backend/app/mcp_server/stock_service.py
```

- `.env` is git-ignored; `.env.example` documents the shape with dummy values.
- Verify before every commit: `git status` must never show `.env` as tracked.

---

## 4. Zero-leakage checks

| Surface | Risk | Guard |
| --- | --- | --- |
| Logs | key printed in debug | never `print`/log `settings.GOOGLE_API_KEY`; log presence as a boolean |
| SSE frames | key rides in serialised state | keep secrets out of `AgentState`; audit `node_output` before yielding |
| Tracebacks | exception echoes config | catch at the stream boundary; emit sanitised `error` frame |
| Model dumps | `model_dump()` includes secret field | never put secrets in Pydantic request/response models |
| yfinance/HTTP debug | verbose libs leak headers | keep third-party log level ≥ INFO |

Quick self-audit command:
```bash
# nothing should match your real key value across the tree or logs
grep -R --exclude-dir=.venv --exclude-dir=.git -F "$(printenv GOOGLE_API_KEY)" . || echo "clean"
```

---

## 5. Gemini free-tier quota management

The zero-cost mandate depends on staying inside the free tier. Each research run makes a **bounded** number of Gemini calls:

| Node | Gemini calls per run |
| --- | --- |
| planner | 1 |
| critic | 1 per iteration (max 2) |
| synthesizer | 1 |
| **worst case total** | **≈4 per run** |

Rules:
- The Critic retry loop is hard-capped at 2 iterations — this is also a **cost** guard, not just a correctness one.
- Do not fan out extra LLM calls per RAG chunk or per metric; batch context into a single prompt per node.
- Use `temperature=0.1` and concise prompts; smaller responses = lower quota burn.
- On a `429 RESOURCE_EXHAUSTED`, back off with bounded retry, then surface a clean "quota reached, retry later" message — never hammer the endpoint.

---

## 6. Anti-patterns

- Hardcoding a key in source or a notebook.
- `os.getenv("GOOGLE_API_KEY")` sprinkled across modules.
- Logging or printing the key (even at debug).
- Putting secrets into `AgentState` or Pydantic models (they get serialised).
- Committing `.env`.
- Unbounded LLM retries that silently burn quota.

---

## 7. Validation

1. Remove `GOOGLE_API_KEY` → app fails fast with a non-leaking message.
2. `git check-ignore .env` returns `.env` (ignored).
3. Secret-grep across tree and logs → `clean`.
4. Count Gemini calls in a forced double-retry run → ≤4.
