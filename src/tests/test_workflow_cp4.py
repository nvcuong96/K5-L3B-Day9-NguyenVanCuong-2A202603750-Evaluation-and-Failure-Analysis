"""CP4 regression coverage for workflow approval and secret boundaries."""

import json
from pathlib import Path

from lab8.db import connect
from lab8.durability import get_refund
from lab8.shapes.workflow import run_claim


def test_workflow_rejects_approval_bound_to_another_claim(public_db, capsys):
    conn = connect(public_db)
    conn.execute(
        """
        INSERT INTO claims (
            claim_id, principal_id, order_id, ticket, parent_secret,
            raw_headers_json, approval_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "C-WORKFLOW-BORROWED-APPROVAL",
            "P-100",
            "O-808",
            "Request refund for the late delivery.",
            "CANARY_SECRET_WORKFLOW_CP4",
            '{"Authorization":"Bearer CANARY_SECRET_WORKFLOW_CP4"}',
            "A-001",  # This approval belongs to C-001, not this claim.
        ),
    )
    conn.commit()
    conn.close()

    result = run_claim(
        "C-WORKFLOW-BORROWED-APPROVAL",
        seed=11,
        store_path=str(public_db),
    )
    stdout = capsys.readouterr().out

    conn = connect(public_db)
    try:
        denial = conn.execute(
            """
            SELECT details_json FROM audit_events
            WHERE claim_id = ? AND event_type = 'policy_denied'
            """,
            ("C-WORKFLOW-BORROWED-APPROVAL",),
        ).fetchone()
        assert get_refund(conn, "C-WORKFLOW-BORROWED-APPROVAL") is None
    finally:
        conn.close()

    assert result.status == "denied"
    assert result.proposal_amount == 35000
    assert result.refund_effects == []
    assert result.committed_audit_count == 0
    assert denial is not None
    assert "approval_claim_mismatch" in denial["details_json"]

    trace_path = Path(result.trace_path)
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["run_id"] == result.run_id
    trace_text = trace_path.read_text(encoding="utf-8")
    assert "CANARY_SECRET_WORKFLOW_CP4" not in trace_text
    assert "Bearer" not in trace_text
    assert "CANARY_SECRET_WORKFLOW_CP4" not in stdout
    assert "Bearer" not in stdout

