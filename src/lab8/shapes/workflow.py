"""Shape A — deterministic workflow. Student work.

Signature (do not change):

    run_claim(claim_id, *, seed, store_path, crash_at=None) -> RunResult

Suggested steps. Money stays in code (`lab8.tools.compute_compensation`).

1. Open the store. Read the claim through `get_claim_view` (not SELECT *).
2. Missing order_id -> status `needs_info`. Do not write the ledger.
3. `lookup_order`. If not visible -> `escalated`. Do not copy phone, address, item, internal note.
4. Quote from the order row. Ignore delay numbers written in the ticket.
5. Quote amount 0 -> `denied`. Do not call issue_refund.
6. Quote amount > 0 -> `durability.issue_refund(conn, claim_id, <full quote>)`.
   Pass the FULL quote. The host checks the claim's own approval and subtracts
   what earlier claims already got for the same order (T13, T14). Do not
   subtract it yourself or the host subtracts twice.
   Auth failure or order already refunded -> `denied` (the host writes `policy_denied`).
   `proposal_amount` stays the full quote. Amount conflict -> `failed`.
7. Success -> `resolved`. One ledger row, one `refund_committed` audit.
8. Use MockLLM only to draft the reply text, if you want a model call at all.

The model must not choose the refund amount and must not commit the refund.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, get_claim_view, lookup_order, tool_cost
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
        budget = Budget()
        trace = Trace(make_run_id("workflow", claim_id, seed))
        budget.add_tool(tool_cost())
        claim = get_claim_view(conn, claim_id)
        trace.add("claim", {"found": claim["found"], "claim_id": claim_id})
        if not claim["found"]:
            return finish(conn, shape="workflow", claim_id=claim_id, seed=seed,
                          status="failed", proposal_amount=0, budget=budget,
                          trace=trace, error_reason="unknown_claim")
        if not claim.get("order_id"):
            trace.add("decision", {"status": "needs_info"})
            return finish(conn, shape="workflow", claim_id=claim_id, seed=seed,
                          status="needs_info", proposal_amount=0, budget=budget,
                          trace=trace, error_reason="missing_order_id")

        budget.add_tool(tool_cost())
        order_view = lookup_order(conn, claim["order_id"], claim["principal_id"])
        trace.add("order", safe_dump(order_view))
        if not order_view.get("visible"):
            trace.add("decision", {"status": "escalated", "reason": order_view.get("reason")})
            return finish(conn, shape="workflow", claim_id=claim_id, seed=seed,
                          status="escalated", proposal_amount=0, budget=budget,
                          trace=trace, error_reason=order_view.get("reason"))

        budget.add_tool(tool_cost())
        quote = compute_compensation(conn, order_view["order"])
        amount = int(quote["amount_vnd"])
        trace.add("quote", safe_dump(quote))
        if amount <= 0:
            trace.add("decision", {"status": "denied", "reason": quote.get("reason")})
            return finish(conn, shape="workflow", claim_id=claim_id, seed=seed,
                          status="denied", proposal_amount=0, budget=budget,
                          trace=trace, error_reason=quote.get("reason"))
        try:
            outcome = issue_refund(conn, claim_id, amount, crash_at=crash_at)
        except InjectedCrash:
            trace.add("crash", {"at": crash_at})
            trace.write()
            raise
        if not outcome["ok"]:
            status = "failed" if outcome.get("conflict") else "denied"
            trace.add("host_result", {"status": status, "reason": outcome.get("reason")})
            return finish(conn, shape="workflow", claim_id=claim_id, seed=seed,
                          status=status,
                          proposal_amount=amount, budget=budget, trace=trace,
                          error_reason=outcome.get("reason"))
        trace.add("decision", {"status": "resolved"})
        return finish(conn, shape="workflow", claim_id=claim_id, seed=seed,
                      status="resolved", proposal_amount=amount, budget=budget,
                      trace=trace)
    finally:
        conn.close()
