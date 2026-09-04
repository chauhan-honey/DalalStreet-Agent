import os

# Satisfy the fail-fast config check without a real key during tests.
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-used")
