"""B1: parallel read-only investigators with a single host writer."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, get_claim_view, lookup_order, lookup_policy, lookup_refund_history, tool_cost
from lab8.trace import Trace

SHAPE = "orchestrator_parallel"
READ_TASKS = ("lookup_order", "lookup_policy", "lookup_refund_history")


def _read_one(store_path: str, name: str, claim_id: str, principal_id: str) -> tuple[str, dict[str, Any], Budget]:
    conn = connect(store_path)
    budget = Budget()
    try:
        claim = get_claim_view(conn, claim_id)
        budget.add_tool(tool_cost())
        if name == "lookup_order":
            result = lookup_order(conn, claim.get("order_id"), principal_id)
        elif name == "lookup_policy":
            result = lookup_policy(conn)
        else:
            result = lookup_refund_history(conn, claim.get("order_id"), principal_id)
        budget.add_tool(tool_cost())
        return name, result, budget
    finally:
        conn.close()


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, store_path=store_path, crash_at=crash_at)
    finally:
        conn.close()


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal_amount: int, error_reason: str | None = None) -> RunResult:
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal_amount,
            budget=budget,
            trace=trace,
            error_reason=error_reason,
        )

    claim = get_claim_view(conn, claim_id)
    budget.add_tool(tool_cost())
    trace.add("claim", {"found": bool(claim.get("found")), "has_order_id": bool(claim.get("order_id"))})
    if not claim.get("found"):
        return done("failed", 0, "unknown_claim")
    if not claim.get("order_id"):
        return done("needs_info", 0, "missing_order_id")

    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="check_applicable_policy",
        facts={},
        allowed_tools=(*READ_TASKS, "compute_compensation"),
    )
    trace.add("handoff", safe_dump(handoff))

    # Each worker opens its own SQLite connection. Results are collected in a
    # fixed task order, so completion timing cannot affect the trace or quote.
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(READ_TASKS)) as executor:
        futures = {
            name: executor.submit(_read_one, store_path, name, claim_id, claim["principal_id"])
            for name in READ_TASKS
        }
        for name in READ_TASKS:
            result_name, result, local_budget = futures[name].result()
            results[result_name] = result
            budget.tool_calls += local_budget.tool_calls
            budget.cost_estimate += local_budget.cost_estimate
            budget.steps += local_budget.steps

    order_view = results["lookup_order"]
    trace.add(
        "investigator",
        {
            "parallel": True,
            "order_visible": bool(order_view.get("visible")),
            "reason": order_view.get("reason"),
            "order": order_view.get("order"),
            "refunded_vnd": results["lookup_refund_history"].get("refunded_vnd"),
            "policy_count": len(results["lookup_policy"].get("policies") or []),
        },
    )
    if not order_view.get("visible"):
        return done("escalated", 0, order_view.get("reason"))

    quote_payload = compute_compensation(conn, order_view["order"])
    budget.add_tool(tool_cost())
    proposal = int(quote_payload.get("amount_vnd", 0))
    trace.add(
        "quote",
        {
            "amount_vnd": proposal,
            "reason": quote_payload.get("reason"),
            "clause_ids": quote_payload.get("clause_ids"),
        },
    )
    if proposal <= 0:
        return done("denied", 0, quote_payload.get("reason"))

    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise
    trace.add("host", {"ok": bool(outcome["ok"]), "reason": outcome["reason"], "conflict": bool(outcome["conflict"])})
    if not outcome["ok"]:
        return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])
    return done("resolved", proposal)
