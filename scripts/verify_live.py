"""Actual local-model acceptance, not a mock. Requires API, MCP, both workers and DB.

Writes inputs, HTTP outputs, model tool decisions, real operation rows and event trace.
Temporarily enables one persisted tool failure for its own new run, restoring YAML.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))

import httpx
import yaml
from sqlalchemy import select

from app.config import runtime_snapshot
from app.store import EventRow, JobRow, OperationRow, ProposalRow, db_factory, event_envelope, read_state, tool_view

OUTPUT = ROOT / "examples/live"
CLIENT = httpx.Client(base_url="http://127.0.0.1:8000/api", timeout=30, trust_env=False)


def get(path):
    response = CLIENT.get(path); response.raise_for_status(); return response.json()


def wait_jobs(sid, timeout=900):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with db_factory() as db:
            pending = db.scalar(select(JobRow.id).where(JobRow.session_id == sid, JobRow.status.in_(["running", "queued"])).limit(1))
        if not pending:
            return get(f"/sessions/{sid}")
        time.sleep(0.5)
    raise TimeoutError(f"Jobs still active for {sid}")


def say(sid, content, client_id=None):
    workspace = get(f"/sessions/{sid}")
    context = {"plan_version_id": workspace["plans"][-1]["plan_version_id"]} if workspace["plans"] else {}
    payload = {"content": content, "client_message_id": client_id or str(uuid.uuid4()), "view_context": context}
    response = CLIENT.post(f"/sessions/{sid}/messages", json=payload); response.raise_for_status()
    print("message accepted:", content, flush=True)
    return payload, response.json()


def new():
    response = CLIENT.post("/sessions"); response.raise_for_status(); return response.json()["session_id"]


def record(name, sid, inputs, checks):
    target = OUTPUT / name; target.mkdir(parents=True, exist_ok=True)
    state = read_state(sid)
    with db_factory() as db:
        operations = [tool_view(row) for row in db.scalars(select(OperationRow).where(OperationRow.session_id == sid).order_by(OperationRow.started_at))]
        events = [event_envelope(row) for row in db.scalars(select(EventRow).where(EventRow.session_id == sid).order_by(EventRow.seq))]
        proposals = [row.body for row in db.scalars(select(ProposalRow).where(ProposalRow.session_id == sid))]
        jobs = [{"job_id": row.id, "kind": row.kind, "status": row.status, "lease_epoch": row.lease_epoch, "error": row.error}
                for row in db.scalars(select(JobRow).where(JobRow.session_id == sid).order_by(JobRow.created_at))]
    files = {"input.json": inputs, "output.json": {"workspace": get(f"/sessions/{sid}"), "messages": state["timeline"], "proposals": proposals},
             "trace.json": {"actual": True, "model": state["model_display"], "model_calls": state["model_calls"], "tool_calls": operations, "events": events, "jobs": jobs},
             "checks.json": checks}
    for filename, data in files.items():
        (target / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for proposal in proposals:
        (target / f"proposal-v{proposal['version']}.md").write_text(proposal["markdown"], encoding="utf-8")
    print(name, checks, flush=True)
    assert all(checks.values()), f"Acceptance failed: {name}: {checks}"
    return state, operations


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    get("/health")
    config_path = Path(os.getenv("CONTENT_STUDIO_CONFIG", "config/app.yaml"))
    config_path = config_path if config_path.is_absolute() else ROOT / config_path
    original = config_path.read_text(encoding="utf-8")
    try:
        config = yaml.safe_load(original)
        config["fault_injection"] = {"once_tool": None, "pause_after_failure": False}
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")

        sid = new(); inputs = [say(sid, "为咖啡新品做一场内容推广，只先讨论需求。")]
        view = wait_jobs(sid)
        state = read_state(sid)
        reply = " ".join(item.get("content", "") for item in state["timeline"] if item.get("role") == "assistant")
        record("01-missing-input", sid, inputs, {"no_run": view["active_run"] is None, "no_plan": not view["plans"],
               "asks_budget_and_deadline": "预算" in reply and ("截止" in reply or "时间" in reply), "no_business_calls": not state["materials"]})

        sid = new(); inputs = [say(sid, "为咖啡新品做内容推广，先采用高规格视觉 premium。制作预算 10 个模拟单位，开始时间2030-12-01 08:00，截止2030-12-03 18:00，均为北京时间，允许合成素材。先展示计划。")]
        view = wait_jobs(sid)
        assert view["plans"] and view["active_run"] is None, "Preview did not form"
        inputs.append(say(sid, "先别开始，解释一下执行过程。")); view = wait_jobs(sid)
        assert view["active_run"] is None, "Negative execution command incorrectly executed"
        client_id = str(uuid.uuid4())
        payload, accepted = say(sid, "按这份计划开始执行。", client_id); inputs.append((payload, accepted))
        duplicate = CLIENT.post(f"/sessions/{sid}/messages", json=payload); duplicate.raise_for_status()
        assert duplicate.json()["message_id"] == accepted["message_id"] and duplicate.json()["duplicate"]
        view = wait_jobs(sid)
        before = len(view["proposals"])
        inputs.append(say(sid, "开始")); view = wait_jobs(sid)
        state, ops = record("02-complete-replan-idempotence", sid, inputs, {"completed": (view["active_run"] or {}).get("status") == "completed",
               "saved_once": len(view["proposals"]) == before == 1, "cost_8": view["cost_summary"]["production"] == "8",
               "no_reviewed_invented_claims": all(term not in get(f"/proposals/{view['proposals'][0]['proposal_id']}")["structured"]["content"] for term in ("冷萃工艺", "送出", "新品尝鲜优惠", "抽取三位", "免费试饮")) if view["proposals"] else False,
               "real_12_then_8": [o["output"].get("production_cost") for o in get_operations(sid) if o["tool_name"] == "estimate_proposal" and o["status"] == "success"] == ["12", "8"]})

        config["fault_injection"] = {"once_tool": "generate_script", "pause_after_failure": True}
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        sid = new(); inputs = [say(sid, "为读书分享活动做内容方案，用精简视觉 simple，预算10个模拟单位，截止2030-12-03 18:00北京时间，允许合成资料。先给计划。")]
        wait_jobs(sid); inputs.append(say(sid, "开始")); failed = wait_jobs(sid)
        failed_ops = get_operations(sid)
        failed_snapshot = {"workspace": failed, "tool_calls": failed_ops}
        target = OUTPUT / "03-tool-failure-resume"; target.mkdir(parents=True, exist_ok=True)
        (target / "before-resume.json").write_text(json.dumps(failed_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        assert failed["active_run"]["status"] == "incomplete" and failed["materials"]
        inputs.append(say(sid, "从失败处继续，保留已经取得的素材。")); view = wait_jobs(sid)
        ops = get_operations(sid)
        record("03-tool-failure-resume", sid, inputs, {"completed_after_resume": view["active_run"]["status"] == "completed",
               "same_run": failed["active_run"]["run_id"] == view["active_run"]["run_id"], "material_call_once": sum(o["tool_name"] == "search_synthetic_materials" for o in ops) == 1,
               "failed_attempt_preserved": sum(o["tool_name"] == "generate_script" and o["status"] == "failed" for o in ops) == 1,
               "new_script_attempt_success": sum(o["tool_name"] == "generate_script" and o["status"] == "success" for o in ops) == 1,
               "saved_once": len(view["proposals"]) == 1})

        config["fault_injection"] = {"once_tool": None, "pause_after_failure": False}
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        sid = new(); inputs = [say(sid, "做一份读书活动推广方案，精简视觉 simple，预算5个模拟单位，截止2030-12-03 18:00北京时间，允许合成素材。先展示计划。")]
        wait_jobs(sid); inputs.append(say(sid, "开始")); view = wait_jobs(sid)
        record("04-no-feasible-budget", sid, inputs, {"incomplete": view["active_run"]["status"] == "incomplete", "not_saved": not view["proposals"],
               "lowest_known_gap_3": "预算缺口 3" in (view["active_run"].get("reason") or ""),
               "no_repeated_candidate_estimate": len([op for op in get_operations(sid) if op["tool_name"] == "estimate_proposal" and op["status"] == "success"]) == len({op["output"]["production_spec"] for op in get_operations(sid) if op["tool_name"] == "estimate_proposal" and op["status"] == "success"}),
               "known_gap_reported": "缺口" in (view["active_run"].get("reason") or ""), "successful_materials_kept": bool(view["materials"])})
    finally:
        config_path.write_text(original, encoding="utf-8")


def get_operations(sid):
    with db_factory() as db:
        return [tool_view(row) for row in db.scalars(select(OperationRow).where(OperationRow.session_id == sid).order_by(OperationRow.started_at))]


if __name__ == "__main__":
    main()
