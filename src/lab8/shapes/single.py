"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.trace import Trace


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    run_id = make_run_id("single", claim_id, seed)
    trace = Trace(run_id)

    # Kick off ReAct loop — model chỉ được gọi read tools
    observation: dict[str, Any] = {"claim": {"claim_id": claim_id}}
    decide_fn = _make_decide(claim_id, seed, budget, trace)

    react_result = run_react(
        conn,
        budget,
        observation=observation,
        decide=decide_fn,
        max_steps=8,
        worker=False,
    )

    final = react_result.get("final", {})
    status: str = final.get("status", "failed")
    proposal_amount: int = int(final.get("proposal_amount", 0))
    error_reason: str | None = final.get("error_reason")

    # Sau vòng ReAct: CODE (không phải model) gọi issue_refund
    # CP3: truyền crash_at, KHÔNG bắt InjectedCrash
    if status == "ready_to_refund":
        outcome = issue_refund(conn, claim_id, proposal_amount, crash_at=crash_at)
        if not outcome["ok"]:
            status = "failed" if outcome.get("conflict") else "denied"
            error_reason = outcome.get("reason")
        else:
            status = "resolved"
            error_reason = None

    trace.add("decision", {"status": status, "proposal_amount": proposal_amount})
    return finish(
        conn,
        shape="single",
        claim_id=claim_id,
        seed=seed,
        status=status,
        proposal_amount=proposal_amount,
        budget=budget,
        trace=trace,
        error_reason=error_reason,
    )


def _make_decide(
    claim_id: str,
    seed: int,
    budget: Budget,
    trace: Trace,
):
    """
    Hàm decide cho vòng ReAct. Mỗi lần được gọi với observation hiện tại,
    trả về tool call tiếp theo hoặc final decision.

    Luồng:
      1. get_claim_view  → kiểm order_id
      2. lookup_order    → kiểm visibility / ownership
      3. compute_compensation → lấy quote từ order (CODE tính, không phải model)
      4. final: ready_to_refund / denied / escalated / needs_info
    """
    _state: dict[str, Any] = {}

    def decide(obs: dict[str, Any]) -> dict[str, Any]:
        last_tool: str | None = obs.get("last_tool")
        last_result: dict[str, Any] = obs.get("last_result") or {}

        # ── Bước 1: Chưa làm gì → gọi get_claim_view ──────────────────────
        if last_tool is None:
            complete(budget, seed=seed, purpose="peer", prompt=claim_id)
            return {"tool": "get_claim_view", "args": {"claim_id": claim_id}}

        # ── Bước 2: Đọc được claim view → kiểm order_id ───────────────────
        if last_tool == "get_claim_view":
            complete(budget, seed=seed, purpose="parse", prompt=str(last_result))
            if not last_result.get("found"):
                trace.add("early_exit", {"reason": "unknown_claim"})
                return {
                    "final": {
                        "status": "failed",
                        "proposal_amount": 0,
                        "error_reason": "unknown_claim",
                    }
                }
            if not last_result.get("order_id"):
                complete(budget, seed=seed, purpose="draft", prompt="needs_info")
                trace.add("early_exit", {"reason": "missing_order_id"})
                return {
                    "final": {
                        "status": "needs_info",
                        "proposal_amount": 0,
                        "error_reason": "missing_order_id",
                    }
                }
            # Lưu thông tin claim để dùng lại
            _state["claim_view"] = last_result
            return {
                "tool": "lookup_order",
                "args": {
                    "order_id": last_result["order_id"],
                    "principal_id": last_result["principal_id"],
                },
            }

        # ── Bước 3: Đọc được order → kiểm visibility / ownership ──────────
        if last_tool == "lookup_order":
            complete(budget, seed=seed, purpose="parse", prompt=str(last_result))
            if not last_result.get("visible"):
                complete(budget, seed=seed, purpose="draft", prompt="escalated")
                trace.add("early_exit", {"reason": last_result.get("reason")})
                return {
                    "final": {
                        "status": "escalated",
                        "proposal_amount": 0,
                        "error_reason": last_result.get("reason"),
                    }
                }
            _state["order_view"] = last_result
            order = last_result["order"]
            # Dùng order field từ DB (không tin số ngày trong ticket)
            return {"tool": "compute_compensation", "args": {"order": order}}

        # ── Bước 4: Có quote → quyết định ─────────────────────────────────
        if last_tool == "compute_compensation":
            amount = int(last_result.get("amount_vnd", 0))
            complete(budget, seed=seed, purpose="draft", prompt=f"proposal={amount}")
            trace.add("quote", {
                "amount_vnd": amount,
                "reason": last_result.get("reason"),
                "clause_ids": last_result.get("clause_ids"),
            })
            if amount <= 0:
                return {
                    "final": {
                        "status": "denied",
                        "proposal_amount": 0,
                        "error_reason": last_result.get("reason"),
                    }
                }
            # Truyền full quote — host sẽ tự trừ phần đã hoàn trước (T13/T14)
            return {
                "final": {
                    "status": "ready_to_refund",
                    "proposal_amount": amount,
                    "error_reason": None,
                }
            }

        # ── Fallback: step_cap hoặc trạng thái bất ngờ ────────────────────
        complete(budget, seed=seed, purpose="draft", prompt="failed_unexpected")
        trace.add("early_exit", {"reason": "unexpected_state", "last_tool": last_tool})
        return {
            "final": {
                "status": "failed",
                "proposal_amount": 0,
                "error_reason": "unexpected_state",
            }
        }

    return decide
