"""Write the reproducible B1 critical-path report."""

from __future__ import annotations

import json
from pathlib import Path

from bonus.latency import summarize_latency


def main() -> int:
    seeds = [11, 22, 33, 44, 55]
    report = {
        "seeds": seeds,
        "sequential": summarize_latency(seeds, parallel=False),
        "parallel": summarize_latency(seeds, parallel=True),
    }
    out = Path("reports/bonus_latency.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
