"""Opt-in real PostgreSQL tests. Never truncate or connect to an unconfigured service."""
import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.store import (JobRow, assert_lease, claim_job, create_session, db_factory, finish_job,
                       mutate, read_state, receive_message, uid, utcnow)

pytestmark = pytest.mark.skipif(os.getenv("CONTENT_STUDIO_DB_TESTS") != "1", reason="Set CONTENT_STUDIO_DB_TESTS=1 after initdb")


def test_message_idempotence_is_transactional():
    sid = create_session()["session_id"]
    key = uid()
    first = receive_message(sid, "[集成测试] 消息幂等", key, {})
    second = receive_message(sid, "[集成测试] 消息幂等", key, {})
    assert second["duplicate"] and first["message_id"] == second["message_id"]
    assert first["processing_id"] == second["processing_id"]
    with pytest.raises(ValueError, match="不同内容"):
        receive_message(sid, "另一条不同的内容", key, {})
    with db_factory.begin() as db:
        row = db.get(JobRow, first["processing_id"], with_for_update=True)
        row.status = "done"  # This test verifies acceptance, without invoking a model.
    assert len(read_state(sid)["timeline"]) == 1


def test_expired_worker_cannot_commit_after_reclaim():
    sid = create_session()["session_id"]
    kind = "lease-test-" + uid()[:8]
    with db_factory.begin() as db:
        db.add(JobRow(id=uid(), session_id=sid, kind=kind, status="queued", payload={}))
    original = claim_job(kind, "old-worker")
    assert original is not None
    with db_factory.begin() as db:
        db.get(JobRow, original["job_id"], with_for_update=True).lease_until = utcnow() - timedelta(seconds=1)
    replacement = claim_job(kind, "new-worker")
    assert replacement["job_id"] == original["job_id"]
    assert replacement["lease_epoch"] == original["lease_epoch"] + 1
    def stale_write(state, db):
        assert_lease(db, original)
        state["requirements"]["topic"] = "stale modification"
    with pytest.raises(RuntimeError, match="租约已失效"):
        mutate(sid, stale_write)
    assert read_state(sid)["requirements"]["topic"] is None
    finish_job(replacement)


@pytest.mark.asyncio
async def test_late_tool_result_does_not_overwrite_failed_attempt():
    from mcp.server.mcpserver.exceptions import ToolError
    from app.config import runtime_snapshot
    from app.mcp_server import ExecutionContext, perform
    from app.store import OperationRow
    sid = create_session()["session_id"]
    kind = "late-result-" + uid()[:8]
    with db_factory.begin() as db:
        db.add(JobRow(id=uid(), session_id=sid, kind=kind, status="queued", payload={"snapshot": runtime_snapshot()}))
    job = claim_job(kind, "late-result-test")
    op_id = uid()
    arguments = {"skill_id": "campaign-planning"}
    with db_factory.begin() as db:
        db.add(OperationRow(id=op_id, session_id=sid, run_id=None, step_id="test-context", operation_key=op_id,
                            tool_name="load_skill", display_name="test", server_name="test", status="running", public_input=arguments))
    execution = ExecutionContext(operation_id=op_id, session_id=sid, run_id=None,
                                 job_id=job["job_id"], owner=job["owner"], lease_epoch=job["lease_epoch"])
    async def delayed(_state, _snapshot):
        # Reproduce a transport timeout recorded while server computation was in flight.
        with db_factory.begin() as db:
            row = db.get(OperationRow, op_id, with_for_update=True)
            row.status = "failed"; row.error = "transport timed out"; row.output = {"result_unknown": True}
        return {"skill_id": "campaign-planning", "version": "test-version"}
    with pytest.raises(ToolError, match="迟到结果"):
        await perform("load_skill", execution, arguments, delayed)
    with db_factory() as db:
        row = db.get(OperationRow, op_id)
        assert row.status == "failed" and row.error == "transport timed out"
    assert read_state(sid)["skill_loads"] == []
    finish_job(job)
