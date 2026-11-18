"""Deterministic latency model for the B1 bonus experiment."""

from __future__ import annotations

import random
from pathlib import Path


def _config_path() -> Path:
    return Path(__file__).with_name("latency.yaml")


def load_latency_config() -> tuple[dict[str, int], int, int]:
    tools: dict[str, int] = {}
    model_call_ms = 0
    jitter_ms = 0
    section = ""
    for raw_line in _config_path().read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            section = line[:-1]
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if section == "tools":
            tools[key] = int(value)
        elif key == "model_call_ms":
            model_call_ms = int(value)
        elif key == "jitter_ms":
            jitter_ms = int(value)
    return tools, model_call_ms, jitter_ms


def critical_path_ms(seed: int, *, parallel: bool) -> int:
    tools, _, jitter_ms = load_latency_config()
    rng = random.Random(seed)
    reads = [tools[name] + rng.randint(0, jitter_ms) for name in (
        "lookup_order",
        "lookup_policy",
        "lookup_refund_history",
    )]
    quote = tools["compute_compensation"] + rng.randint(0, jitter_ms)
    return (max(reads) if parallel else sum(reads)) + quote


def summarize_latency(seeds: list[int], *, parallel: bool) -> dict[str, float]:
    values = [critical_path_ms(seed, parallel=parallel) for seed in seeds]
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(0.95 * len(ordered)) - 1))
    mean = sum(values) / len(values)
    return {"mean_ms": round(mean, 3), "p95_ms": ordered[index], "samples": values}
