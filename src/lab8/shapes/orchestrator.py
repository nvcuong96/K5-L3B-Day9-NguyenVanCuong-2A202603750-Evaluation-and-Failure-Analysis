"""Shape C — orchestrator plus read-only investigators. Student work.

1. Build a handoff with `lab8.handoff.make_handoff`. Allowed tools are read-only.
   Never put parent_secret, raw headers, or the full claim row into facts.
2. `lab8.runtime.run_readonly_investigators` runs those tools.
3. The orchestrator quotes from the investigator's order view and calls
   `durability.issue_refund`. Investigators have no write registry.
   Pass the full quote; the host subtracts earlier refunds on the same order.
   `lookup_refund_history` is a read-only tool you may hand to an investigator.
4. Trace the sanitized handoff, not the parent context.

An LLM summary of the parent context is not a sanitizer. Use the allowlist.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "orchestrator"

# TODO 1 (done): tool chỉ đọc giao cho investigator. THỨ TỰ QUAN TRỌNG:
# run_readonly_investigators chạy lần lượt, compute_compensation cần kết quả lookup_order đã có.
INVESTIGATOR_TOOLS: list[str] = [
    "lookup_order",  # đơn đã allowlist + kiểm principal; phải đứng trước compute_compensation
    "lookup_policy",  # chỉ policy is_current và effective_date <= as_of
    "lookup_refund_history",  # chỉ để quan sát/trace; KHÔNG dùng để tự trừ quote
    "compute_compensation",  # quote do code tính từ order row, không từ ticket
]


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


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal_amount: int, error_reason: str | None = None) -> RunResult:
        """Mọi nhánh return đi qua đây -> luôn có trace, luôn lấy ledger/audit từ host."""
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

    # ------------------------------------------------------------------
    # Bước 1 — Orchestrator đọc claim qua view an toàn (không SELECT *).
    # ------------------------------------------------------------------
    claim = get_claim_view(conn, claim_id)
    budget.add_tool(tool_cost())

    # TODO 2 (done): chỉ ghi cờ an toàn; không ghi ticket, principal_id hay nguyên dict claim.
    trace.add(
        "claim",
        {"found": bool(claim.get("found")), "has_order_id": bool(claim.get("order_id"))},
    )

    # TODO 3 (done): claim không tồn tại.
    if not claim.get("found"):
        return done("failed", 0, "unknown_claim")

    # TODO 4 (done): thiếu order_id -> needs_info. Không đoán mã đơn từ ticket.
    if not claim.get("order_id"):
        return done("needs_info", 0, "missing_order_id")

    # ------------------------------------------------------------------
    # Bước 2 — Dựng handoff bằng allowlist rồi giao cho investigator.
    # ------------------------------------------------------------------
    # TODO 5 (done): handoff qua allowlist. facts rỗng: không đưa claim row / ticket / secret.
    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="check_applicable_policy",
        facts={},
        allowed_tools=INVESTIGATOR_TOOLS,
    )

    # TODO 6 (done): trace handoff đã sanitize, không phải parent context.
    trace.add("handoff", safe_dump(handoff))

    # TODO 7 (done): investigator chỉ thấy handoff + tool đọc có tên trong đó.
    # principal_id truyền qua tham số để tool lọc scope, không nằm trong handoff/trace.
    investigated = run_readonly_investigators(
        conn, budget, handoff, principal_id=claim["principal_id"]
    )
    tools: dict = investigated["tools"]

    # ------------------------------------------------------------------
    # Bước 3 — Orchestrator đọc kết quả investigator.
    # ------------------------------------------------------------------
    order_view = tools.get("lookup_order") or {}

    # TODO 8 (done): trace kết quả investigator. order đã qua allowlist ORDER_FIELDS
    # (không có SĐT, địa chỉ, tên hàng, internal note). refunded_vnd chỉ để quan sát,
    # KHÔNG dùng để tự trừ vào quote (host trừ; tự trừ thì T14 bị trừ hai lần).
    history = tools.get("lookup_refund_history") or {}
    trace.add(
        "investigator",
        {
            "order_visible": bool(order_view.get("visible")),
            "reason": order_view.get("reason"),
            "order": order_view.get("order"),
            "refunded_vnd": history.get("refunded_vnd"),
            "policy_count": len((tools.get("lookup_policy") or {}).get("policies") or []),
        },
    )

    # TODO 9 (done): đơn không thấy / không thuộc principal -> escalated.
    if not order_view.get("visible"):
        return done("escalated", 0, order_view.get("reason"))

    # TODO 10 (done): quote do CODE tính từ order row + policy hiện hành.
    # Không đọc số ngày trễ trong ticket (đó là REFERENCE_WEAKNESS của swarm).
    quote_payload = tools.get("compute_compensation") or {}
    proposal = int(quote_payload.get("amount_vnd", 0))
    trace.add(
        "quote",
        {
            "amount_vnd": proposal,
            "reason": quote_payload.get("reason"),
            "clause_ids": quote_payload.get("clause_ids"),
        },
    )

    # TODO 11 (done): quote = 0 -> denied, không gọi issue_refund.
    if proposal <= 0:
        return done("denied", 0, quote_payload.get("reason"))

    # ------------------------------------------------------------------
    # Bước 4 — Chỉ orchestrator ghi tiền, qua cổng host.
    # ------------------------------------------------------------------
    # TODO 12 (done, CP3): quote ĐẦY ĐỦ + crash_at. Không nuốt InjectedCrash:
    # flush trace rồi ném lại để test resume thấy crash thật.
    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise

    # TODO 13 (done): chỉ ghi cờ kết quả của host.
    trace.add(
        "host",
        {"ok": bool(outcome["ok"]), "reason": outcome["reason"], "conflict": bool(outcome["conflict"])},
    )

    # TODO 14 (done): host từ chối. proposal_amount vẫn là quote đầy đủ.
    if not outcome["ok"]:
        status = "failed" if outcome["conflict"] else "denied"
        return done(status, proposal, outcome["reason"])

    # TODO 15 (done): thành công.
    return done("resolved", proposal)
