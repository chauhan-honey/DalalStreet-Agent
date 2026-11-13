"""Critic/Auditor detection-accuracy evaluation on seeded cases with known answers.

Each case pairs one management claim (as a cited report snippet) with live-style
fundamentals where the correct verdict is known in advance: the claim either
matches the numbers, contradicts them, or cannot be checked against them. Only
critic_node runs, so the cost is exactly one Gemini call per case (6 total) —
mind the free tier's ~20 calls/day.

    python scripts/eval_critic.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.graph import critic_node  # noqa: E402

RATIOS = {"operatingMargins": 0.2396, "profitMargins": 0.191, "revenueGrowth": 0.047,
          "debtToEquity": 9.8, "returnOnEquity": 0.52, "trailingPE": 24.1}

# (name, claim from the "annual report", expected status)
CASES = [
    ("margin overstated", "Our operating margin expanded to 26.5% this year, our best ever.", "contradiction"),
    ("margin accurate", "We delivered an operating margin of 24.0% for the year.", "aligned"),
    ("growth overstated", "Revenue grew at a robust double-digit rate of 12% year on year.", "contradiction"),
    ("growth accurate", "Revenue growth for the year was a modest 4.7%.", "aligned"),
    ("leverage misstated", "The company carries significant debt, with debt exceeding equity.", "contradiction"),
    ("not checkable", "Our employee engagement score rose to an all-time high.", "unsupported"),
]


async def run_case(claim: str) -> list[str]:
    state = {
        "user_query": "Do management's claims match the reported fundamentals?",
        "rag_context": [{"content": claim, "page": 12}],
        "market_data": {"quote": {"symbol": "TCS.NS"}, "ratios": RATIOS},
        "iteration_count": 0,
    }
    out = await critic_node(state)
    return [str(f.get("status", "")).lower() for f in out["audit_findings"]]


async def main() -> None:
    correct = 0
    for name, claim, expected in CASES:
        statuses = await run_case(claim)
        ok = expected in statuses
        correct += ok
        print(f"{'PASS' if ok else 'MISS'}  {name:<20} expected={expected:<14} got={statuses}")
    print(f"\nCritic accuracy: {correct}/{len(CASES)}")


if __name__ == "__main__":
    asyncio.run(main())
