"""Independent rubric audit of the actual external-service run facts."""
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]


def main():
    base=ROOT/'examples/external-live/after'
    data=json.loads((base/'audit-auto-failure-replan/evidence.json').read_text(encoding='utf8'))
    state=data['state'];ops=data['tool_calls'];run=state['active_run']
    sources=[o for o in ops if o['tool_name']=='search_web_materials']
    scripts=[o for o in ops if o['tool_name']=='generate_script']
    estimates=[o for o in ops if o['tool_name']=='estimate_proposal' and o['status']=='success']
    validations=[o for o in ops if o['tool_name']=='validate_proposal' and o['status']=='success']
    saves=[o for o in ops if o['tool_name']=='save_proposal' and o['status']=='success']
    proposals=json.loads((base/'audit-auto-failure-replan/saved-proposals.json').read_text(encoding='utf8'))
    ledger={row['charge_key']:row for row in data['cost_entries']}
    rules=run['snapshot']['production_rules']['tool_simulated_fees']
    expected_fee=sum((Decimal(rules.get(o['tool_name'],'0')) for o in ops if o['status'] in {'success','partial_success'} and not (o['output'] or {}).get('reused')),Decimal(0))
    by_id={o['tool_call_id']:o for o in ops};dependencies=[]
    for plan in state['plans']:
        lookup={step['key']:step for step in plan['steps']}
        for step in plan['steps']:
            if step['status'] not in {'success','partial_success'}:continue
            usable=[by_id[key] for key in step['tool_call_ids'] if by_id[key]['status'] in {'success','partial_success'} and by_id[key]['tool_name']==step['tool']]
            dependencies.append({'plan':plan['version'],'step':step['key'],'has_successful_tool':bool(usable),
                'parents_have_results':all(lookup[key]['status'] in {'success','partial_success'} for key in step['dependencies'])})
    old,new=estimates[0]['output'],estimates[1]['output'];proposal=proposals[0]
    checks={
        'budget_estimates_are_actual_12_then_8':[old['production_cost'],new['production_cost']]==['12','8'],
        'both_costs_equal_decimal_item_sums':all(sum(Decimal(i['cost']) for i in e['output']['items'])==Decimal(e['output']['production_cost']) for e in estimates),
        'first_is_infeasible_with_gap_2':not old['is_feasible'] and Decimal(old['cost_gap'])==2,
        'replacement_is_feasible_within_budget':new['is_feasible'] and Decimal(new['production_cost'])<=Decimal(new['budget']),
        'saved_cost_and_schedule_valid':Decimal(proposal['production_cost'])==8 and datetime.fromisoformat(proposal['estimated_finish'])<=datetime.fromisoformat(proposal['requirements']['deadline']),
        'dependency_and_actual_result_checks':all(d['has_successful_tool'] and d['parents_have_results'] for d in dependencies),
        'single_failure_is_still_failed':len(scripts)==2 and scripts[0]['status']=='failed' and 'INJECTED_ONCE' in scripts[0]['error'],
        'failed_attempt_has_no_script_artifact':'script_id' not in scripts[0]['output'],
        'only_one_business_failure':sum(o['status']=='failed' for o in ops)==1,
        'failure_retry_uses_new_attempt':scripts[1]['status']=='success' and scripts[0]['tool_call_id']!=scripts[1]['tool_call_id'] and scripts[1]['attempt']==2,
        'failure_is_not_charged_simulated_fee':Decimal(ledger['tool:'+scripts[0]['tool_call_id']]['amount'])==0,
        'automatic_without_resume_messages':len(data['inputs'])==2,
        'one_source_search_result_preserved':len(sources)==1 and {m['material_id'] for m in state['materials']}=={m['material_id'] for m in sources[0]['output']['materials']},
        'successful_script_reused_after_replan':run['script_id']==scripts[1]['output']['script_id'] and all(p['steps'][1]['tool_call_ids']==state['plans'][0]['steps'][1]['tool_call_ids'] for p in state['plans']),
        'replan_was_an_actual_model_decision':any(c['kind']=='replan' and c['provider']=='deepseek' for c in state['model_calls']),
        'replan_has_new_version_and_old_superseded':len(state['plans'])==2 and state['plans'][0]['status']=='superseded' and state['plans'][1]['production_spec']=='simple',
        'order_source_failure_retry_estimate_check_save':datetime.fromisoformat(sources[0]['started_at'])<datetime.fromisoformat(scripts[0]['started_at'])<datetime.fromisoformat(scripts[1]['started_at'])<datetime.fromisoformat(estimates[0]['started_at'])<datetime.fromisoformat(estimates[1]['started_at'])<datetime.fromisoformat(validations[-1]['started_at'])<datetime.fromisoformat(saves[0]['started_at']),
        'save_has_actual_passed_check':validations[-1]['output']['is_valid'] and proposal['final_validation']['is_valid'],
        'save_check_hash_matches_actual_validation':proposal['validated_draft_hash']==validations[-1]['output']['content_hash'],
        'saved_script_is_successful_artifact':proposal['structured']['content']==scripts[1]['output']['script'],
        'new_estimate_and_save_reference_current_plan':new['plan_version_id']==run['plan_version_id']==proposal['plan_version_id'],
        'budget_is_never_changed_to_fit_result':run['requirements']['budget']=='10' and all(p['requirements']['budget']=='10' for p in state['plans']),
        'completed_means_exactly_one_actual_save':run['status']=='completed' and len(saves)==1 and len(proposals)==1 and proposal['run_id']==run['run_id'],
        'simulated_tool_fees_are_separate_and_exact':expected_fee==Decimal('0.55')==Decimal(state['cost_summary']['tool_simulated']),
        'real_sources_in_saved_document':all(m['source_type']=='web' and m['url'].startswith('https://') and 'example.invalid' not in m['url'] for m in proposal['materials']),
        'provider_not_switched':all(c['provider']=='deepseek' for c in state['model_calls']),
    }
    generic=json.loads((base/'audit.json').read_text(encoding='utf8'))
    checks['all_external_scenario_fact_audits_pass']=all(item['passed'] for item in generic)
    result={'session_id':data['session_id'],'actual_deepseek_and_tavily':True,'checks':checks,'passed':all(checks.values()),
        'costs':{'budget':'10','observed_estimates':['12','8'],'saved_production':'8','production_gap_before':'2',
            'tool_simulated':'0.55','model_price':None,'external_search_price':None},'step_dependencies':dependencies,
        'operation_ids':{'source':sources[0]['tool_call_id'],'failed_script':scripts[0]['tool_call_id'],'successful_script':scripts[1]['tool_call_id'],
            'estimates':[o['tool_call_id'] for o in estimates],'save':saves[0]['tool_call_id']},
        'actual_model_replan':[c for c in state['model_calls'] if c['kind']=='replan']}
    (base/'review-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('review audit:',sum(checks.values()),'/',len(checks),[k for k,v in checks.items() if not v]);assert result['passed']


if __name__=='__main__':main()
