"""Read-only audit of real traces, independent of scenario completion assertions."""
from __future__ import annotations
import argparse
from decimal import Decimal
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/backend'))
from app.domain import BUSINESS_TOOLS
from app.store import read_state


def audit(path: Path) -> dict:
    evidence = json.loads(path.read_text(encoding='utf8'))
    workspace = evidence['workspace']; run = workspace.get('active_run')
    operations = {op['tool_call_id']: op for op in evidence['tool_calls']}
    selected = [(call, model) for model in evidence['model_calls']
                for call in model['response'].get('tool_calls', [])]
    within_whitelist = all(call['function']['name'] in model['available_tools'] for call, model in selected)
    linked = all(any(call['function']['name'] == op['tool_name'] and
                     call['function']['arguments'] == op['input'] and model.get('run_id') == op.get('run_id')
                     for call, model in selected) for op in operations.values())
    success_facts = dependencies = True
    for plan in workspace['plans']:
        steps = {step['key']: step for step in plan['steps']}
        for step in steps.values():
            if step['status'] != 'success': continue
            tools = {'search_synthetic_materials', 'search_web_materials'} if step['key'] == 'materials' else {step['tool']}
            success_facts &= any(operations.get(key, {}).get('status') == 'success' and
                                 operations[key]['tool_name'] in tools for key in step['tool_call_ids'])
            dependencies &= all(steps[key]['status'] == 'success' for key in step['dependencies'])
    fees_match = production_matches = True
    if run:
        state = read_state(workspace['session']['session_id'])
        snapshot = state['runs'][run['run_id']]['run']['snapshot']
        fees = snapshot['production_rules']['tool_simulated_fees']
        total = sum((Decimal(fees.get(op['tool_name'], '0')) for op in operations.values()
                     if op['run_id'] == run['run_id'] and op['status'] == 'success'), Decimal(0))
        fees_match = total == Decimal(workspace['cost_summary']['tool_simulated'])
        estimate = state['estimates'].get(run.get('estimate_id'))
        if estimate and not workspace['cost_summary'].get('estimate_stale'):
            production_matches = Decimal(workspace['cost_summary']['production']) == Decimal(estimate['production_cost'])
    saves = [op for op in operations.values() if op['tool_name'] == 'save_proposal' and op['status'] == 'success']
    estimate_math = True
    for op in operations.values():
        if op['tool_name'] != 'estimate_proposal' or op['status'] != 'success': continue
        result = op['output']
        cost = sum((Decimal(item['cost']) for item in result['items']), Decimal(0))
        hours = sum((Decimal(item['hours']) for item in result['items']), Decimal(0))
        start = datetime.fromisoformat(result['start_at']); finish = datetime.fromisoformat(result['estimated_finish'])
        deadline = datetime.fromisoformat(result['deadline'])
        estimate_math &= (cost == Decimal(result['production_cost']) and hours == Decimal(result['hours']) and
                          Decimal(str((finish-start).total_seconds())) == hours*3600 and
                          max(cost-Decimal(result['budget']), Decimal(0)) == Decimal(result['cost_gap']) and
                          result['is_feasible'] == (cost <= Decimal(result['budget']) and finish <= deadline))
    valid_saves = True
    for save in saves:
        estimates = [op for op in operations.values() if op['tool_name'] == 'estimate_proposal' and op['status'] == 'success'
                     and op['output'].get('estimate_id') == save['input'].get('estimate_id')]
        validations = [op for op in operations.values() if op['tool_name'] == 'validate_proposal' and op['status'] == 'success'
                       and op['output'].get('validation_id') == save['input'].get('validation_id')]
        valid_saves &= bool(estimates and validations and estimates[0]['output']['is_feasible'] and validations[0]['output']['is_valid'])
    completion = not run or run['status'] != 'completed' or bool(saves and workspace['proposals'])
    checks = {'actual_trace': evidence['actual'], 'model_selected_only_offered_tools': within_whitelist,
              'actual_operations_link_to_native_model_calls': linked,
              'successful_steps_have_actual_business_results': bool(success_facts),
              'successful_step_dependencies_satisfied': bool(dependencies),
              'tool_fee_matches_success_ledger': fees_match, 'production_matches_actual_estimate': production_matches,
              'estimate_item_sums_cost_gap_schedule_and_feasibility_correct': bool(estimate_math),
              'saved_only_with_feasible_estimate_and_valid_check': bool(valid_saves),
              'completed_requires_actual_save': completion}
    return {'case': evidence['name'], 'scenario_passed': evidence['passed'],
            'run_status': run['status'] if run else None, 'native_selection_count': len(selected),
            'business_operation_count': sum(op['tool_name'] in BUSINESS_TOOLS for op in operations.values()),
            'checks': checks, 'audit_passed': all(checks.values())}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--stage', default='verification'); args = parser.parse_args()
    folder = ROOT / 'examples/backend-boundaries' / args.stage
    results = [audit(path) for path in sorted(folder.glob('*/evidence.json'))]
    target = folder / 'trace-audit.json'
    target.write_text(json.dumps({'actual': True, 'scope': 'Trace correctness, not scenario completion',
                                  'results': results}, ensure_ascii=False, indent=2), encoding='utf8')
    for result in results:
        print(result['case'], 'TRACE_AUDIT_PASS' if result['audit_passed'] else 'TRACE_AUDIT_FAIL',
              'scenario=' + ('PASS' if result['scenario_passed'] else 'FAIL'),
              [key for key, value in result['checks'].items() if not value])
    if not results or not all(result['audit_passed'] for result in results): raise SystemExit(1)


if __name__ == '__main__': main()
