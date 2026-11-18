"""Thành viên 3 — orchestrator, handoff và trace an toàn.

Chạy trong cổng host mặc định (`python -m pytest -q`), không gắn marker student:
mọi test ở đây phải luôn xanh.

- Quét secret/PII trên trace + mọi file `<run_id>*` + RunResult cho cả 3 shape nhóm viết
  (dùng để rà trace trước CP4; shape còn stub vẫn phải sạch).
- Handoff của orchestrator chỉ chứa field allowlist, investigator chỉ có tool đọc.
- Trace contract v1 ở mọi nhánh return của orchestrator.
- Orchestrator truyền QUOTE ĐẦY ĐỦ cho host (T13, T14), không tin delay trong ticket (T02),
  không nuốt InjectedCrash và resume không trả hai lần.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lab8 import durability
from lab8.db import connect, init_store
from lab8.durability import InjectedCrash, committed_audit_count, get_refund
from lab8.handoff import (
    ALLOWED_FIELDS,
    FACT_KEYS,
    READONLY_TOOLS,
    WRITE_TOOLS,
    HandoffError,
    make_handoff,
)
from lab8.shapes import SHAPES
from lab8.shapes import orchestrator as orch
from tests.conftest import BY_ID, CLAIMS, WORLD

GROUP_SHAPES = ["workflow", "single", "orchestrator"]
CLAIM_IDS = [row["claim_id"] for row in CLAIMS]

# Chuỗi cấm chung: secret cha, header, và PII/ghi chú nội bộ của mọi order trong world.
_GENERIC_FORBIDDEN = ["CANARY_SECRET_", "Bearer ", "parent_secret", "raw_headers", "Authorization"]
_PII_FORBIDDEN = sorted(
    {
        str(order[key])
        for order in WORLD["orders"]
        for key in ("phone", "address", "internal_note", "item_name")
        if order.get(key)
    }
)


def _store(tmp_path: Path, name: str = "store.sqlite") -> Path:
    path = tmp_path / name
    init_store(path, WORLD, CLAIMS).close()
    return path


def _run_blob(result) -> str:
    """Nội dung mà grader quét: trace_path, mọi file <run_id>* cùng thư mục, và RunResult."""
    trace_path = Path(result.trace_path)
    parts = [repr(result)]
    for path in sorted(trace_path.parent.glob(f"{result.run_id}*")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _events(result) -> list[dict]:
    body = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    return body["events"]


# ---------------------------------------------------------------------------
# 1. Không lộ secret / PII — cả 3 shape nhóm viết (rà trace trước CP4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [11, 55])
@pytest.mark.parametrize("claim_id", CLAIM_IDS)
@pytest.mark.parametrize("shape", GROUP_SHAPES)
def test_no_secret_or_pii_in_trace_or_result(tmp_path, shape, claim_id, seed):
    store = _store(tmp_path)
    result = SHAPES[shape](claim_id, seed=seed, store_path=str(store))
    blob = _run_blob(result)

    forbidden = list(BY_ID[claim_id]["expected"].get("forbid_substrings", []))
    forbidden += _GENERIC_FORBIDDEN + _PII_FORBIDDEN
    leaked = [needle for needle in forbidden if needle in blob]
    assert leaked == [], f"{shape} {claim_id} seed={seed} leaked {leaked}"


# ---------------------------------------------------------------------------
# 2. Handoff allowlist + investigator chỉ có tool đọc
# ---------------------------------------------------------------------------


def test_investigator_tools_are_read_only():
    assert orch.INVESTIGATOR_TOOLS, "orchestrator phải giao ít nhất một tool đọc"
    assert set(orch.INVESTIGATOR_TOOLS) <= READONLY_TOOLS
    assert not set(orch.INVESTIGATOR_TOOLS) & WRITE_TOOLS
    # compute_compensation cần order từ lookup_order, nên lookup_order phải chạy trước.
    tools = orch.INVESTIGATOR_TOOLS
    assert tools.index("lookup_order") < tools.index("compute_compensation")


@pytest.mark.parametrize("write_tool", sorted(WRITE_TOOLS))
def test_make_handoff_rejects_write_tool(write_tool):
    with pytest.raises(HandoffError):
        make_handoff(
            request_id="REQ-X",
            claim_id="C-001",
            task="check_applicable_policy",
            facts={},
            allowed_tools=["lookup_order", write_tool],
        )


@pytest.mark.parametrize("claim_id", ["C-001", "C-007", "C-013", "C-014"])
def test_orchestrator_handoff_event_is_allowlisted(tmp_path, claim_id):
    store = _store(tmp_path)
    result = SHAPES["orchestrator"](claim_id, seed=11, store_path=str(store))
    handoffs = [e["payload"] for e in _events(result) if e["kind"] == "handoff"]
    assert len(handoffs) == 1

    handoff = handoffs[0]
    assert set(handoff) <= ALLOWED_FIELDS
    assert handoff["claim_id"] == claim_id
    assert set(handoff["facts"]) <= FACT_KEYS
    assert set(handoff["allowed_tools"]) <= READONLY_TOOLS
    assert not set(handoff["allowed_tools"]) & WRITE_TOOLS


def test_orchestrator_trace_never_records_ticket_or_principal(tmp_path):
    store = _store(tmp_path)
    for claim_id in CLAIM_IDS:
        result = SHAPES["orchestrator"](claim_id, seed=11, store_path=str(store))
        blob = Path(result.trace_path).read_text(encoding="utf-8")
        row = BY_ID[claim_id]
        assert row["ticket"] not in blob, claim_id
        assert '"principal_id"' not in blob, claim_id
        assert '"ticket"' not in blob, claim_id


# ---------------------------------------------------------------------------
# 3. Trace contract v1 ở mọi nhánh return (failed, needs_info, escalated, denied, resolved)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim_id, status",
    [
        ("C-404", "failed"),  # claim không tồn tại
        ("C-004", "needs_info"),  # thiếu order_id
        ("C-005", "escalated"),  # không sở hữu đơn
        ("C-002", "denied"),  # quote = 0
        ("C-013", "denied"),  # host từ chối, quote > 0
        ("C-001", "resolved"),
    ],
)
def test_orchestrator_trace_contract_every_branch(tmp_path, claim_id, status):
    store = _store(tmp_path)
    started = time.time()
    result = SHAPES["orchestrator"](claim_id, seed=11, store_path=str(store))

    assert result.status == status
    assert result.claim_id == claim_id
    path = Path(result.trace_path)
    assert path.exists()
    assert path.stat().st_mtime >= started - 1, "trace phải được ghi trong chính lần chạy này"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    assert body["run_id"] == result.run_id
    assert isinstance(body["events"], list) and body["events"]


# ---------------------------------------------------------------------------
# 4. Tiền: quote đầy đủ cho host, không tin ticket
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim_id, full_quote",
    [
        ("C-013", 35000),  # T13: đơn đã hoàn đủ -> host từ chối, vẫn phải gửi full quote
        ("C-014", 150000),  # T14: đã hoàn 30.000 ship -> gửi 150.000, host chỉ ghi phần chênh
    ],
)
def test_orchestrator_sends_full_quote_to_host(tmp_path, monkeypatch, claim_id, full_quote):
    store = _store(tmp_path)
    seen: list[int] = []
    real_issue_refund = durability.issue_refund

    def spy(conn, cid, proposed_vnd, **kwargs):
        seen.append(proposed_vnd)
        return real_issue_refund(conn, cid, proposed_vnd, **kwargs)

    monkeypatch.setattr(orch, "issue_refund", spy)
    result = SHAPES["orchestrator"](claim_id, seed=11, store_path=str(store))

    expected = BY_ID[claim_id]["expected"]
    assert seen == [full_quote], "shape không được tự trừ phần đã hoàn trước khi gọi host"
    assert result.proposal_amount == full_quote
    assert result.status == expected["status"]
    assert sum(e["amount_vnd"] for e in result.refund_effects) == expected["refund_vnd"]


@pytest.mark.parametrize("seed", [11, 22, 33, 44, 55])
def test_orchestrator_ignores_delay_written_in_ticket(tmp_path, seed):
    """T02: ticket nói trễ 5 ngày, order row ghi 2 ngày. Swarm sai ở seed 55; orchestrator không."""
    store = _store(tmp_path)
    result = SHAPES["orchestrator"]("C-002", seed=seed, store_path=str(store))
    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    assert not any(e["kind"] == "model_override" for e in _events(result))


def test_orchestrator_quote_zero_does_not_call_host(tmp_path, monkeypatch):
    store = _store(tmp_path)

    def must_not_call(*args, **kwargs):
        raise AssertionError("quote = 0 thì không được gọi issue_refund")

    monkeypatch.setattr(orch, "issue_refund", must_not_call)
    for claim_id in ("C-002", "C-006", "C-009"):
        result = SHAPES["orchestrator"](claim_id, seed=11, store_path=str(store))
        assert result.status == "denied"
        assert result.proposal_amount == 0


def test_orchestrator_host_denial_writes_policy_denied_audit(tmp_path):
    """C-007 (approval hỏng) và C-013 (đơn đã hoàn đủ): denied + audit policy_denied, không ledger."""
    store = _store(tmp_path)
    for claim_id in ("C-007", "C-013"):
        result = SHAPES["orchestrator"](claim_id, seed=11, store_path=str(store))
        assert result.status == "denied"
        assert result.proposal_amount == BY_ID[claim_id]["expected"]["proposal_amount"]
        conn = connect(store)
        try:
            assert get_refund(conn, claim_id) is None
            denied = conn.execute(
                "SELECT COUNT(*) AS n FROM audit_events WHERE claim_id = ? AND event_type = 'policy_denied'",
                (claim_id,),
            ).fetchone()["n"]
        finally:
            conn.close()
        assert denied >= 1, claim_id


# ---------------------------------------------------------------------------
# 5. Crash: không nuốt InjectedCrash, resume vẫn đúng một refund
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("crash_at", ["before_effect", "after_effect_before_checkpoint", "after_checkpoint"])
def test_orchestrator_crash_is_raised_then_resume_pays_once(tmp_path, crash_at):
    store = _store(tmp_path)
    with pytest.raises(InjectedCrash):
        SHAPES["orchestrator"]("C-012", seed=11, store_path=str(store), crash_at=crash_at)

    result = SHAPES["orchestrator"]("C-012", seed=11, store_path=str(store))
    assert result.status == "resolved"
    assert len(result.refund_effects) == 1
    assert result.refund_effects[0]["amount_vnd"] == 35000
    assert result.committed_audit_count == 1

    # Chạy lại lần nữa vẫn idempotent.
    again = SHAPES["orchestrator"]("C-012", seed=11, store_path=str(store))
    conn = connect(store)
    try:
        assert get_refund(conn, "C-012")["amount_vnd"] == 35000
        assert committed_audit_count(conn, "C-012") == 1
    finally:
        conn.close()
    assert again.status == "resolved"
