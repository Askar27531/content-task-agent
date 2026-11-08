"""Recompute fees/dependencies from actual evidence and export actual saved rows."""
from __future__ import annotations
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src/backend'))
from sqlalchemy import select
from app.store import db_factory, ProposalRow


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='examples/backend-completion');args=parser.parse_args()
    root=(ROOT/args.root).resolve()
    if not root.is_relative_to((ROOT/'examples').resolve()):raise ValueError('审计产物仅可写入本项目examples目录。')
    reports=[]
    for item in json.loads((root/'index.json').read_text(encoding='utf8')):
        folder=root/item['name'];data=json.loads((folder/'evidence.json').read_text(encoding='utf8'))
        state=data['state'];operations={op['tool_call_id']:op for op in data['tool_calls']}
        ledger={entry['charge_key']:entry for entry in data['cost_entries']}
        fees=True;facts=True;dependencies=True
        for archive in state['runs'].values():
            run=archive['run'];rates=run['snapshot']['production_rules']['tool_simulated_fees'];total=Decimal(0)
            for op in operations.values():
                if op['run_id']!=run['run_id']:continue
                fee=Decimal(rates.get(op['tool_name'],'0')) if op['status'] in {'success','partial_success'} and not (op.get('output') or {}).get('reused') else Decimal(0)
                total+=fee
                if op['status']!='running':fees &= 'tool:'+op['tool_call_id'] in ledger and Decimal(ledger['tool:'+op['tool_call_id']]['amount'])==fee
            fees &= total==Decimal(archive['cost_summary']['tool_simulated'])
        for plan in state['plans']:
            steps={step['key']:step for step in plan['steps']}
            for step in steps.values():
                if step['status'] not in {'success','partial_success'}:continue
                allowed={'search_synthetic_materials','search_web_materials'} if step['key']=='materials' else {step['tool']}
                facts &= any(operations.get(key,{}).get('status') in {'success','partial_success'} and operations[key]['tool_name'] in allowed for key in step['tool_call_ids'])
                dependencies &= all(steps[key]['status'] in {'success','partial_success'} for key in step['dependencies'])
        with db_factory() as db:
            proposals=[row.body for row in db.scalars(select(ProposalRow).where(ProposalRow.session_id==item['session_id']).order_by(ProposalRow.version))]
        (folder/'saved-proposals.json').write_text(json.dumps(proposals,ensure_ascii=False,indent=2),encoding='utf8')
        for proposal in proposals:
            (folder/f"proposal-v{proposal['version']}.md").write_text(proposal['markdown'],encoding='utf8')
        budget=all(sum((Decimal(i['cost']) for i in p['save_estimate']['items']),Decimal(0))==Decimal(p['production_cost'])<=Decimal(p['requirements']['budget']) for p in proposals)
        schedule=all(datetime.fromisoformat(p['estimated_finish'])<=datetime.fromisoformat(p['requirements']['deadline']) for p in proposals)
        models=all('model:'+call['request_id'] in ledger and (call['usage'].get('input_tokens') is not None or ledger['model:'+call['request_id']]['amount'] is None) for call in state['model_calls'])
        checks={'scenario_checks_pass':item['passed'],'one_ledger_entry_per_charge':len(ledger)==len(data['cost_entries']),
            'actual_tool_fees_match_run_and_ledger':bool(fees),'usable_steps_have_actual_results':bool(facts),
            'dependencies_satisfied':bool(dependencies),'saved_budget_valid':budget,'saved_schedule_valid':schedule,
            'model_attempts_recorded_unknown_not_zero':models,
            'completed_means_actual_saved_row':not state.get('active_run') or state['active_run']['status']!='completed' or any(p['run_id']==state['active_run']['run_id'] for p in proposals)}
        reports.append({'name':item['name'],'proposal_count':len(proposals),'model_calls':len(state['model_calls']),
            'tool_calls':len(operations),'checks':checks,'passed':all(checks.values())})
        print(item['name'],'PASS' if all(checks.values()) else 'FAIL',[k for k,v in checks.items() if not v])
    (root/'audit.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf8')
    assert all(report['passed'] for report in reports)


if __name__=='__main__':main()
