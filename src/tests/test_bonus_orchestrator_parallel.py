from __future__ import annotations

import json
from pathlib import Path

from bonus.latency import critical_path_ms
from bonus.orchestrator_parallel import run_claim
from lab8.db import init_store, load_json, load_jsonl
from lab8.config_loader import repo_path


def test_parallel_latency_has_shorter_critical_path():
    seeds = [11, 22, 33, 44, 55]
    assert sum(critical_path_ms(seed, parallel=True) for seed in seeds) < sum(
        critical_path_ms(seed, parallel=False) for seed in seeds
    )


def test_parallel_result_matches_sequential_shape(public_db: Path, tmp_path: Path):
    claims = load_jsonl(repo_path("data", "public_claims.jsonl"))
    claim = next(row for row in claims if row["claim_id"] == "C-001")
    world = load_json(repo_path("data", "public_world_state.json"))
    parallel_db = tmp_path / "parallel.sqlite"
    sequential_db = tmp_path / "sequential.sqlite"
    init_store(parallel_db, world, claims).close()
    init_store(sequential_db, world, claims).close()

    parallel = run_claim("C-001", seed=11, store_path=str(parallel_db))
    from lab8.shapes.orchestrator import run_claim as sequential_run

    sequential = sequential_run("C-001", seed=11, store_path=str(sequential_db))
    assert parallel.status == sequential.status == "resolved"
    assert parallel.proposal_amount == sequential.proposal_amount
    assert parallel.refund_effects == sequential.refund_effects
    assert parallel.committed_audit_count == sequential.committed_audit_count
