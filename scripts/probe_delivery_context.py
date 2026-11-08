"""Compare native tool choices against frozen real artifacts; never dispatch tools."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/backend'))
from app.gateway import Gateway
from app.graphs import ExecutionRunner
from app.llm import invoke
from app.store import create_session, mutate, read_state


async def main(rounds):
    sid = create_session()['session_id']
    mutate(sid, lambda state, _: state['session'].update(title='交付上下文探测（不派发业务工具）'))
    rows = []
    fixture_path = ROOT / 'examples/backend-boundaries/delivery-fix/diagnostic/fixtures.json'
    if not fixture_path.exists():
        fixtures = {}
        for case in ('20-video-script-supported', '23-stop-resume'):
            evidence = json.loads((ROOT / 'examples/backend-boundaries/verification' / case / 'evidence.json').read_text(encoding='utf8'))
            state = read_state(evidence['session_id'])
            fixtures[case] = {key: state[key] for key in ('active_run', 'plans', 'validations', 'estimates', 'materials', 'draft')}
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding='utf8')
    fixtures = json.loads(fixture_path.read_text(encoding='utf8'))
    for case, required in [('20-video-script-supported', 'validate_proposal'), ('23-stop-resume', 'save_proposal')]:
        state = fixtures[case]; run = state['active_run']; snapshot = run['snapshot']
        job = {'session_id': sid, 'payload': {'run_id': run['run_id']}}
        runner = ExecutionRunner(job, snapshot, None)
        system = (await runner.select_execution_skills({}))['model_messages']
        async with Gateway(sid, job, snapshot) as gateway:
            tools = await gateway.tools({required, 'load_skill', 'read_skill_resource'})
        for variant, facts in [('previous', runner.facts(state)), ('phase_specific', runner.decision_facts(state))]:
            for repeat in range(rounds):
                messages = system + [{'role': 'user', 'content': '这是最新已验证事实。请选择本阶段必要工具；没有数据时不要猜标识。\n' + json.dumps(facts, ensure_ascii=False)}]
                response = await invoke(sid, None, snapshot['selected_model'], 'delivery_context_probe', messages, tools=tools)
                names = [call['function']['name'] for call in response['tool_calls']]
                row = {'case': case, 'variant': variant, 'repeat': repeat+1, 'expected_business_tool': required,
                       'selected_tools': names, 'business_tool_selected': required in names,
                       'messages': messages, 'available_tools': tools, 'response': response}
                rows.append(row)
                target = ROOT / 'examples/backend-boundaries/delivery-fix/diagnostic/context-probe.json'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps({'actual_model_calls': True, 'dispatches_business_tools': False,
                                             'session_id': sid, 'results': rows}, ensure_ascii=False, indent=2), encoding='utf8')
                print(case, variant, repeat+1, names, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--rounds', type=int, default=2)
    asyncio.run(main(parser.parse_args().rounds))
