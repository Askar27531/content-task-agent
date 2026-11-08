from __future__ import annotations

import importlib.util
import hashlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .config import ROOT, canonical_hash
from .store import stamp, uid

CHECKER_PATH = ROOT / "skills/proposal-delivery/scripts/check_required_sections.py"
LOADED_CHECKER_HASH = hashlib.sha256(CHECKER_PATH.read_bytes()).hexdigest()
spec = importlib.util.spec_from_file_location("studio_delivery_checker", CHECKER_PATH)
checker = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(checker)

STEP_DEFINITIONS = [
    ("materials", "检索活动素材", "取得适合主题的资料，并保留可追溯来源", "search_synthetic_materials", []),
    ("script", "生成内容脚本", "使用真实取得的资料形成可用文案", "generate_script", ["materials"]),
    ("estimate", "核算制作成本与排期", "由规则核验制作预算与活动完成期限", "estimate_proposal", ["script"]),
    ("validate", "检查方案完整性", "核对必要章节、字段与合法素材引用", "validate_proposal", ["estimate"]),
    ("save", "保存活动方案", "事务保存当前已检查且可行的方案", "save_proposal", ["validate"]),
]
CONTEXT_TOOLS = {"load_skill", "read_skill_resource"}
BUSINESS_TOOLS = {definition[3] for definition in STEP_DEFINITIONS} | {"search_web_materials"}
TOOL_LABELS = {definition[3]: definition[1] for definition in STEP_DEFINITIONS} | {
    "search_web_materials": "检索真实素材", "load_skill": "加载任务 Skill", "read_skill_resource": "读取 Skill 参考"}


def normalize_requirement_dates(patch: dict) -> dict:
    """Apply the agreed Asia/Shanghai default to complete local date/time values.

    Do not invent a clock time for a date-only value or replace an explicit offset.
    """
    result = dict(patch)
    for key in ("deadline", "start_at"):
        value = result.get(key)
        if not isinstance(value, str) or not re.search(r"[T ]\d{2}:\d{2}", value):
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            result[key] = parsed.replace(tzinfo=timezone(timedelta(hours=8), "Asia/Shanghai")).isoformat()
    return result


def missing_requirements(brief: dict, *, require_future: bool = True) -> list[str]:
    missing = []
    if not brief.get("topic"):
        missing.append("活动主题")
    if brief.get("budget") is None:
        missing.append("内容制作预算（模拟单位）")
    else:
        try:
            if Decimal(str(brief["budget"])) < 0 or not Decimal(str(brief["budget"])).is_finite():
                missing.append("有效的非负制作预算")
        except InvalidOperation:
            missing.append("有效的制作预算")
    if not brief.get("deadline"):
        missing.append("活动截止日期与时间")
    else:
        try:
            value = datetime.fromisoformat(brief["deadline"])
            if not re.search(r"[T ]\d{2}:\d{2}", brief["deadline"]):
                missing.append("活动截止日期与时间（需具体时刻）")
            elif value.tzinfo is None:
                missing.append("截止时间的时区")
        except ValueError:
            missing.append("明确的活动截止日期与时间")
    if brief.get("start_at"):
        try:
            value = datetime.fromisoformat(brief["start_at"])
            if not re.search(r"[T ]\d{2}:\d{2}", brief["start_at"]) or value.tzinfo is None:
                missing.append("有效的制作开始时间（含具体时刻与时区）")
        except ValueError:
            missing.append("有效的制作开始时间")
    try:
        deadline = datetime.fromisoformat(brief['deadline']) if brief.get('deadline') else None
        start = datetime.fromisoformat(brief['start_at']) if brief.get('start_at') else None
        if require_future and deadline and deadline.tzinfo and deadline <= datetime.now(timezone.utc):
            missing.append('活动截止时间已经过去，请提供未来的截止时间。')
        if start and deadline and start.tzinfo and deadline.tzinfo and start >= deadline:
            missing.append('制作开始时间必须早于活动截止时间。')
    except (ValueError, TypeError):
        pass
    return missing


def create_plan(brief: dict, version: int, production_spec: str, summary: str, changes: list[str] | None = None,
                previous: dict | None = None) -> dict:
    import copy
    plan = {"plan_version_id": uid(), "version": version, "label": "精简制作方案" if production_spec == "simple" else "高规格内容方案",
            "production_spec": production_spec, "status": "ready", "requirements": copy.deepcopy(brief), "summary": summary,
            "change_summary": changes or [], "steps": []}
    for key, title, goal, tool, dependencies in STEP_DEFINITIONS:
        old = next((step for step in (previous or {}).get("steps", []) if step["key"] == key), None)
        reusable = previous is not None and key in {"materials", "script"} and old and old["status"] == "success" and previous["requirements"] == brief
        plan["steps"].append({"step_id": uid(), "key": key, "title": title, "goal": goal,
                              "tool": "search_web_materials" if key == "materials" and (brief.get("real_sources_required") or brief.get('prefer_web')) else tool,
                              "dependencies": dependencies, "status": "success" if reusable else "pending",
                              "tool_call_ids": list(old["tool_call_ids"]) if reusable else []})
    return plan


def estimate(brief: dict, production_spec: str, rules: dict, start: datetime | None = None) -> dict:
    if production_spec not in rules["specifications"]:
        raise ValueError("未知制作规格，不能按零成本或零工期估算。")
    items = rules["specifications"][production_spec]["items"]
    total = sum((Decimal(item["cost"]) for item in items), Decimal(0))
    hours = sum((Decimal(item["hours"]) for item in items), Decimal(0))
    requested = datetime.fromisoformat(brief["start_at"]) if brief.get("start_at") else (start or datetime.now(timezone.utc))
    if requested.tzinfo is None:
        raise ValueError("制作开始时间缺少时区。")
    actual_start = max(requested, start or datetime.now(timezone.utc))
    finish = actual_start + timedelta(hours=float(hours))
    deadline = datetime.fromisoformat(brief["deadline"])
    if deadline.tzinfo is None:
        raise ValueError("活动截止时间缺少时区。")
    budget = Decimal(str(brief["budget"]))
    if not budget.is_finite() or budget < 0:
        raise ValueError("预算必须为非负有限值。")
    return {"estimate_id": uid(), "production_spec": production_spec, "production_cost": str(total), "budget": str(budget),
            "unit": "模拟单位", "hours": str(hours), "items": items, "start_at": actual_start.isoformat(),
            "estimated_finish": finish.isoformat(), "deadline": deadline.isoformat(), "is_feasible": total <= budget and finish <= deadline,
            "cost_gap": str(max(total - budget, Decimal(0))), "time_gap_hours": str(max(Decimal(str((finish - deadline).total_seconds() / 3600)), Decimal(0))),
            "rules_version": rules["version"], "checked_at": stamp()}


def validate_draft(draft: dict, materials: list[dict]) -> dict:
    return {"validation_id": uid(), "content_hash": canonical_hash(draft), **checker.validate_payload(draft, {item["material_id"] for item in materials})}


def render_proposal(draft: dict, result: dict, materials: list[dict]) -> str:
    lines = [f"# {draft['title']}", "", "## 活动目标", draft["objective"], "", "## 受众与渠道假设", draft["audience"],
             "", "## 内容策略", draft["strategy"], "", "## 内容与脚本", draft["content"], "", "## 制作与活动执行排期",
             f"自然小时、单团队串行；预计完成：{result['estimated_finish']}。", "", "| 制作项 | 工期（小时） | 制作成本 |", "| --- | --- | --- |"]
    lines += [f"| {item['name']} | {item['hours']} | {item['cost']} |" for item in result["items"]]
    lines += ["", "## 制作成本与预算", f"制作成本 **{result['production_cost']} 个模拟单位**；制作预算 **{result['budget']}**。工具和模型开销独立记录，不计入制作预算。", "", "## 素材与来源"]
    for material in materials:
        title = f"[{material['title']}]({material['url']})" if material.get("url") else material["title"]
        lines.append(f"- {title}（{'真实来源' if material['source_type'] == 'web' else '项目合成素材'}）：{material['summary']}")
    lines += ["", "## 调整与限制", "制作成本和工期采用项目规则估算，活动尚未在现实中执行。合成资料用于创意，不作为真实市场数据。"]
    lines += list(draft.get('source_warnings',[]))
    return "\n".join(lines)
