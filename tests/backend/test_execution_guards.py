"""Isolated graph-guard checks, not evidence of real model tool selection."""
import asyncio
from types import SimpleNamespace

from app import graphs


def test_validation_to_save_resets_obsolete_tool_context(monkeypatch):
    run = {'status': 'running', 'phase': 'delivery', 'plan_version_id': 'p',
           'last_tool_failed': False, 'validation_id': 'v', 'business_calls': 4, 'context_calls': 12}
    state = {'active_run': run, 'draft': {'plan_version_id': 'p'}, 'materials': [{}],
             'validations': {'v': {'is_valid': True}}}
    run['script_id'] = 's'
    runner = graphs.ExecutionRunner({'session_id': 'test', 'payload': {'run_id': 'r'}},
        {'limits': {'run_timeout_seconds': 900, 'max_context_calls': 12, 'max_business_calls': 30}}, None)
    runner.run = lambda: (state, run)
    monkeypatch.setattr(graphs, 'current_estimate', lambda _: {'is_feasible': True})
    messages = [{'role': 'system', 'content': 'agent'}, {'role': 'assistant', 'content': 'old validation context'}]
    result = asyncio.run(runner.assess_progress({'model_messages': messages, 'allowed': ['validate_proposal'], 'pending_calls': []}))
    assert result['allowed'] == ['save_proposal'] and result['route'] == 'decide'
    assert result['model_messages'] == messages[:1]


def test_three_empty_native_responses_stop_without_fabricating_calls(monkeypatch):
    run = {'model_rounds': 0, 'no_action_rounds': 0}
    state = {'active_run': run}
    async def tools(_): return []
    async def empty(*args, **kwargs): return {'role': 'assistant', 'content': '', 'tool_calls': []}
    monkeypatch.setattr(graphs, 'invoke', empty)
    monkeypatch.setattr(graphs, 'mutate', lambda sid, fn, *args: fn(state, None))
    runner = graphs.ExecutionRunner({'session_id': 'test', 'payload': {'run_id': 'r'}},
        {'selected_model': {}, 'limits': {'max_model_rounds': 24}}, SimpleNamespace(tools=tools))
    runner.run = lambda: (state, run)
    runner.decision_facts = lambda _: {}
    reasons = []
    runner.mark_incomplete = reasons.append
    async def exercise():
        context = {'model_messages': [{'role': 'system', 'content': 'agent'}], 'allowed': ['validate_proposal']}
        for index in range(3):
            result = await runner.decide_next_action(context)
            assert result['pending_calls'] == []
            assert bool(reasons) == (index == 2)
            context.update(result)
    asyncio.run(exercise())
    assert run['model_rounds'] == 3 and run['no_action_rounds'] == 3
    assert '未提供可执行工具调用' in reasons[0]


def test_delivery_context_uses_real_artifact_ids_without_replaying_writing_controls():
    runner = graphs.ExecutionRunner({'session_id': 'test', 'payload': {'run_id': 'r'}}, {}, None)
    original = {'phase': 'delivery', 'draft_id': 'actual-draft', 'loaded_skills': ['script-writing'], 'last_error': None,
                'content_requests': ['写3条视频脚本，先给计划，不要执行'], 'materials': [{'summary': 'long source'}],
                'estimate': {'estimate_id': 'actual-estimate', 'plan_version_id': 'p', 'production_cost': '8',
                             'budget': '10', 'is_feasible': True, 'items': ['irrelevant pricing detail']}, 'validation': None}
    runner.facts = lambda _: original
    result = runner.decision_facts({})
    assert result['draft_id'] == 'actual-draft' and result['validation'] is None
    assert result['estimate']['estimate_id'] == 'actual-estimate'
    assert '检查' in result['current_task']
    assert 'content_requests' not in result and 'materials' not in result and 'items' not in result['estimate']
    assert original['content_requests'] == ['写3条视频脚本，先给计划，不要执行']
    original['validation'] = {'validation_id': 'actual-check', 'draft_id': 'actual-draft', 'is_valid': True, 'checks': ['sections']}
    result = runner.decision_facts({})
    assert result['validation'] == {'validation_id': 'actual-check', 'draft_id': 'actual-draft', 'is_valid': True}
    assert '保存' in result['current_task']


def test_non_delivery_context_keeps_original_content_requirements():
    runner = graphs.ExecutionRunner({'session_id': 'test', 'payload': {'run_id': 'r'}}, {}, None)
    original = {'phase': 'writing', 'content_requests': ['写3条视频脚本'], 'materials': [{'material_id': 'real-id'}]}
    runner.facts = lambda _: original
    assert runner.decision_facts({}) == original


def test_capability_question_with_explicit_brief_persists_gaps_for_workspace(monkeypatch):
    text = '我不要方案，要你直接生成3条4K成片，登录抖音发布并付款买广告。预算10，截止2030-12-03 18:00北京时间。'
    state = {'requirements': {'topic': None, 'budget': None, 'deadline': None, 'start_at': None, 'real_sources_required': False},
             'session': {'title': '新会话'}, 'plans': [], 'timeline': [], 'cost_summary': {}}
    monkeypatch.setattr(graphs, 'read_state', lambda _: state)
    monkeypatch.setattr(graphs, 'assert_lease', lambda *args: None)
    monkeypatch.setattr(graphs, 'mutate', lambda sid, fn, *args: fn(state, None))
    runner = graphs.DialogueRunner({'session_id': 'test', 'payload': {'content': text}}, {}, None)
    result = asyncio.run(runner.validate_requirements({'intent': {'action': 'question', 'updates': {}, 'evidence': {}}}))
    assert result['route'] == 'clarify'
    assert state['requirements']['budget'] == '10'
    assert set(state['capability_gaps']) == {'video_generation', 'publishing', 'payment'}
    assert any('真实视频' in issue for issue in result['missing'])
    assert any('发布工具' in issue for issue in result['missing'])
    assert any('支付' in issue for issue in result['missing'])


def test_capability_question_without_a_new_brief_does_not_change_requirements(monkeypatch):
    state = {'requirements': {'topic': None, 'budget': None, 'deadline': None, 'start_at': None, 'real_sources_required': False},
             'plans': [], 'timeline': []}
    monkeypatch.setattr(graphs, 'read_state', lambda _: state)
    runner = graphs.DialogueRunner({'session_id': 'test', 'payload': {'content': '你有付款或登录抖音的工具吗？只询问能力，不改活动需求。'}}, {}, None)
    result = asyncio.run(runner.validate_requirements({'intent': {'action': 'question', 'updates': {}, 'evidence': {}}}))
    assert result['route'] == 'respond'
    assert state['requirements']['topic'] is None and 'capability_gaps' not in state
