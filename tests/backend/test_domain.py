from datetime import datetime, timezone, timedelta
from decimal import Decimal
import copy
import json

import pytest

from app import domain
from app.config import ROOT, canonical_hash, runtime_snapshot


@pytest.fixture
def brief():
    return {"topic": "咖啡新品推广", "budget": "10", "deadline": "2030-12-01T18:00:00+08:00",
            "start_at": "2030-12-01T08:00:00+08:00", "real_sources_required": False}


@pytest.fixture
def rules():
    return json.loads((ROOT / "data/production-rules.json").read_text(encoding="utf-8"))


def test_missing_fields_and_zero_budget(brief):
    assert domain.missing_requirements({"topic": "咖啡"}) == ["内容制作预算（模拟单位）", "活动截止日期与时间"]
    brief["budget"] = "0"
    assert domain.missing_requirements(brief) == []
    for value in ("-1", "NaN", "Infinity", "不是数字"):
        brief["budget"] = value
        assert any("预算" in item for item in domain.missing_requirements(brief))


def test_extracted_local_times_use_shanghai_without_changing_explicit_offsets(brief):
    patch = {"deadline": "2030-12-03 18:00", "start_at": "2030-12-01 08:00", "budget": "10"}
    normalized = domain.normalize_requirement_dates(patch)
    assert normalized["deadline"] == "2030-12-03T18:00:00+08:00"
    assert normalized["start_at"] == "2030-12-01T08:00:00+08:00"
    assert patch["deadline"] == "2030-12-03 18:00"
    assert domain.missing_requirements({**brief, **normalized}) == []
    aware = {"deadline": "2030-12-03T18:00:00+09:00"}
    assert domain.normalize_requirement_dates(aware) == aware


def test_date_only_and_invalid_start_are_not_silently_filled(brief):
    patch = domain.normalize_requirement_dates({"deadline": "2030-12-03", "start_at": "not-a-date"})
    assert patch == {"deadline": "2030-12-03", "start_at": "not-a-date"}
    missing = domain.missing_requirements({**brief, **patch})
    assert any("具体时刻" in item for item in missing)
    assert any("开始时间" in item for item in missing)


def test_real_estimates_reject_12_then_accept_8(brief, rules):
    start = datetime(2030, 12, 1, tzinfo=timezone(timedelta(hours=8)))
    expensive = domain.estimate(brief, "premium", rules, start)
    simple = domain.estimate(brief, "simple", rules, start)
    assert expensive["production_cost"] == "12"
    assert expensive["is_feasible"] is False and expensive["cost_gap"] == "2"
    assert simple["production_cost"] == "8" and simple["is_feasible"] is True
    assert Decimal(simple["production_cost"]) == sum(Decimal(item["cost"]) for item in simple["items"])
    assert datetime.fromisoformat(simple["estimated_finish"]) == datetime(2030, 12, 1, 16, tzinfo=start.tzinfo)


def test_budget_and_deadline_both_enforced(brief, rules):
    brief["budget"] = "7"
    brief["deadline"] = "2030-12-01T10:00:00+08:00"
    start = datetime(2030, 12, 1, tzinfo=timezone(timedelta(hours=8)))
    result = domain.estimate(brief, "simple", rules, start)
    assert not result["is_feasible"]
    assert Decimal(result["cost_gap"]) == 1
    assert Decimal(result["time_gap_hours"]) == 6
    with pytest.raises(ValueError, match="未知制作规格"):
        domain.estimate(brief, "invented_free", rules, start)


def test_past_start_does_not_backdate_work(brief, rules):
    now = datetime(2031, 1, 1, tzinfo=timezone.utc)
    result = domain.estimate(brief, "simple", rules, now)
    assert not result["is_feasible"]
    assert datetime.fromisoformat(result["start_at"]) == now


def test_replanning_keeps_only_valid_successes(brief):
    old = domain.create_plan(brief, 1, "premium", "高规格")
    old["steps"][0].update(status="success", tool_call_ids=["material-call"])
    old["steps"][1].update(status="failed", tool_call_ids=["failed-script"])
    old["steps"][2].update(status="success", tool_call_ids=["old-estimate"])
    new = domain.create_plan(brief, 2, "simple", "重规划", previous=old)
    assert new["steps"][0]["tool_call_ids"] == ["material-call"]
    assert new["steps"][0]["status"] == "success"
    assert new["steps"][1]["status"] == "pending" and new["steps"][1]["tool_call_ids"] == []
    assert new["steps"][2]["status"] == "pending"
    assert old["steps"][1]["status"] == "failed"
    changed = {**brief, "topic": "完全不同的主题"}
    assert domain.create_plan(changed, 3, "simple", "新需求", previous=old)["steps"][0]["status"] == "pending"


def test_delivery_rejects_missing_or_invented_source():
    minimal = {"title": "x", "objective": "目标", "audience": "受众", "strategy": "策略", "content": "脚本",
               "schedule": {"hours": "8"}, "budget": {"production_cost": "8"}, "source_ids": ["invented"]}
    result = domain.validate_draft(minimal, [{"material_id": "real-returned-id"}])
    assert not result["is_valid"]
    minimal["source_ids"] = ["real-returned-id"]
    assert domain.validate_draft(minimal, [{"material_id": "real-returned-id"}])["is_valid"]
    del minimal["content"]
    assert not domain.validate_draft(minimal, [{"material_id": "real-returned-id"}])["is_valid"]


def test_runtime_snapshot_versions_and_no_key_values(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-not-a-real-key")
    snapshot = runtime_snapshot()
    assert len(snapshot["skills"]) == 4
    assert snapshot["tavily_enabled"] is True
    assert "test-not-a-real-key" not in json.dumps(snapshot)
    assert "proposal-delivery" in {item["skill_id"] for item in snapshot["skills"]}
    changed = copy.deepcopy(snapshot)
    changed["production_rules"]["version"] = "changed"
    assert canonical_hash(snapshot) != canonical_hash(changed)
