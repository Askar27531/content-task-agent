"""Audit recorded evidence against immutable tool facts, fees and dependencies; no LLM calls."""
from decimal import Decimal
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))
from app.store import read_state
from app.domain import BUSINESS_TOOLS


def main():
    reports = []
    for folder in sorted((ROOT / "examples/live").iterdir()):
        if not (folder / "checks.json").exists():
            continue
        output = json.loads((folder / "output.json").read_text(encoding="utf-8"))
        trace = json.loads((folder / "trace.json").read_text(encoding="utf-8"))
        checks = json.loads((folder / "checks.json").read_text(encoding="utf-8"))
        workspace = output["workspace"]
        state = read_state(workspace["session"]["session_id"])
        run = workspace["active_run"]
        operations = {op["tool_call_id"]: op for op in trace["tool_calls"]}
        success_facts = True; dependencies = True
        for plan in workspace["plans"]:
            steps = {step["key"]: step for step in plan["steps"]}
            for step in steps.values():
                if step["status"] == "success":
                    expected = {"search_synthetic_materials", "search_web_materials"} if step["key"] == "materials" else {step["tool"]}
                    success_facts &= any(operations.get(op_id, {}).get("status") == "success" and
                                         operations[op_id]["tool_name"] in expected for op_id in step["tool_call_ids"])
                    dependencies &= all(steps[key]["status"] == "success" for key in step["dependencies"])
        fees_match = True; budget_valid = True; deadline_valid = True
        rules_version = None
        if run:
            snapshot = state["runs"][run["run_id"]]["run"]["snapshot"]
            rules = snapshot["production_rules"]; rules_version = rules["version"]
            total_fees = sum((Decimal(rules["tool_simulated_fees"].get(op["tool_name"], "0")) for op in operations.values()
                              if op["run_id"] == run["run_id"] and op["status"] == "success"), Decimal(0))
            fees_match = total_fees == Decimal(workspace["cost_summary"]["tool_simulated"])
        for proposal in output["proposals"]:
            estimate = proposal["save_estimate"]
            cost = sum(Decimal(item["cost"]) for item in estimate["items"])
            budget_valid &= cost == Decimal(proposal["production_cost"]) <= Decimal(proposal["requirements"]["budget"])
            deadline_valid &= datetime.fromisoformat(proposal["estimated_finish"]) <= datetime.fromisoformat(proposal["requirements"]["deadline"])
            budget_valid &= proposal["structured"]["schedule"]["production_cost"] == proposal["production_cost"]
        failed_kept = True
        if (folder / "before-resume.json").exists():
            before = json.loads((folder / "before-resume.json").read_text(encoding="utf-8"))
            failed_kept = all(operations.get(op["tool_call_id"], {}).get("status") == "failed"
                              for op in before["tool_calls"] if op["status"] == "failed")
        assertions = {"acceptance_checks_pass": all(checks.values()), "actual_trace": trace["actual"] is True,
                      "success_steps_have_tool_facts": bool(success_facts), "step_dependencies_satisfied": bool(dependencies),
                      "tool_fees_match_ledger": fees_match, "saved_production_cost_valid": bool(budget_valid),
                      "saved_deadline_valid": bool(deadline_valid), "failed_attempts_preserved": failed_kept}
        report = {"example": folder.name, "rules_version": rules_version, "proposal_count": len(output["proposals"]),
                  "model_call_count": len(trace["model_calls"]), "tool_call_count": len(operations), "checks": assertions}
        reports.append(report)
        print(folder.name, assertions)
    target = ROOT / "examples/test-results/evidence-audit.json"
    target.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    assert len(reports) >= 4 and all(all(report["checks"].values()) for report in reports)


if __name__ == "__main__":
    main()
