import json
import pytest

from app.graphs import PREVIEW_SCHEMA
from app.graphs import evaluated_candidates, known_gap_reason
from app.graphs import request_messages
from app.llm import json_content


def test_schema_definition_is_not_accepted_as_a_plan_instance():
    actual_failure_shape = {"type": "object", "properties": {"production_spec": "premium", "summary": "文案", "changes": []},
                            "required": ["production_spec", "summary", "changes"]}
    with pytest.raises(ValueError, match="结构化输出"):
        json_content({"content": json.dumps(actual_failure_shape)}, PREVIEW_SCHEMA)
    value = {"production_spec": "simple", "summary": "预算约束下的方案", "changes": []}
    assert json_content({"content": json.dumps(value)}, PREVIEW_SCHEMA) == value


def test_replanning_reports_lowest_known_gap_in_current_run():
    state = {"active_run": {"run_id": "current", "estimate_id": "expensive", "plan_version_id": "plan-2"},
             "estimates": {
                 "old": {"run_id": "old-run", "production_cost": "1", "budget": "5", "cost_gap": "0", "time_gap_hours": "0"},
                 "cheap": {"run_id": "current", "production_cost": "8", "budget": "5", "cost_gap": "3", "time_gap_hours": "0"},
                 "expensive": {"run_id": "current", "production_cost": "12", "budget": "5", "cost_gap": "7", "time_gap_hours": "0"}}}
    assert len(evaluated_candidates(state)) == 2
    assert "最低制作成本 8" in known_gap_reason(state)
    assert "预算缺口 3" in known_gap_reason(state)


def test_non_core_copy_requirements_survive_without_control_messages():
    state={'timeline':[{'role':'user','content':'读书会，只写3条视频脚本，每条配文字分镜','is_requirement_update':True},
                       {'role':'assistant','content':'预览'}, {'role':'user','content':'预算改为8','is_requirement_update':True},
                       {'role':'user','content':'开始'}]}
    assert request_messages(state)==['读书会，只写3条视频脚本，每条配文字分镜','预算改为8']
