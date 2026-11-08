"""Local guards for actual-service configuration; no billed calls."""
import pytest
from jsonschema import validate,ValidationError
from app.search import WEB_DEFINITION


def test_logical_search_rejects_unimplemented_parameters_before_billed_dispatch():
    with pytest.raises(ValidationError):
        validate({'query':'真实查询','max_results':999},WEB_DEFINITION['function']['parameters'])


@pytest.mark.asyncio
async def test_deepseek_json_mode_includes_actual_schema_in_prompt(monkeypatch):
    import json
    import httpx
    from app import llm
    from app.graphs import INTENT_SCHEMA
    original=httpx.AsyncClient
    def handler(request):
        body=json.loads(request.content)
        assert body['response_format']=={'type':'json_object'}
        assert '"explanation"' in body['messages'][0]['content']
        assert '"capability_evidence": {"type": "string"}' in body['messages'][0]['content']
        assert body['messages'][0]['role']=='system'
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"action":"question","updates":{},"evidence":{},"explanation":"fixture"}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':3,'completion_tokens':2}})
    monkeypatch.setenv('EXTERNAL_SCHEMA_TEST_KEY','fixture-key')
    monkeypatch.setattr(llm.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(llm,'record_model_call',lambda *args,**kw:kw.get('request_id') or 'fixture-request')
    reply=await llm.invoke('fixture',None,{'provider':'deepseek','model':'fixture','base_url':'https://fixture.invalid','api_key_env':'EXTERNAL_SCHEMA_TEST_KEY'},
        'schema-fixture',[{'role':'user','content':'你好'}],schema=INTENT_SCHEMA)
    assert llm.json_content(reply,INTENT_SCHEMA)['action']=='question'


def test_tavily_http_200_error_envelope_is_not_misclassified_as_empty_results():
    from types import SimpleNamespace
    from app.search import parse_remote
    response=SimpleNamespace(is_error=False,structured_content={'error':'Search failed','status':400,'detail':{'error':'invalid domains'}},content=[])
    with pytest.raises(ValueError,match='HTTP 400.*invalid domains'):parse_remote(response,'query',{})


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,expected',[('connection',3),('auth',1),('invalid_config',0)])
async def test_mcp_connection_retries_are_bounded_and_auth_is_not_retried(monkeypatch,failure,expected):
    import asyncio
    import httpx2
    from contextlib import asynccontextmanager
    from app import gateway as module
    calls=[]
    @asynccontextmanager
    async def fake_client(transport,**kwargs):
        calls.append(kwargs)
        if failure=='auth':
            response=httpx2.Response(401,request=httpx2.Request('POST','https://fixture.invalid/mcp'))
            raise ExceptionGroup('auth fixture',[httpx2.HTTPStatusError('401 fixture',request=response.request,response=response)])
        raise ExceptionGroup('connection fixture',[httpx2.ConnectError('TLS fixture')])
        yield
    monkeypatch.setattr(module,'Client',fake_client)
    gateway=module.Gateway('fixture',{}, {'mcp':{'tavily_url':'https://fixture.invalid/mcp','connect_attempts':'invalid' if failure=='invalid_config' else 3}})
    ready=asyncio.get_running_loop().create_future();stop=asyncio.Event()
    owner=asyncio.create_task(gateway._remote_lifetime('fixture-not-secret',ready,stop))
    try:
        with pytest.raises((ConnectionError,httpx2.HTTPStatusError,ValueError)):
            await asyncio.wait_for(ready,timeout=3)
    finally:stop.set();await owner
    assert len(calls)==expected


def budget_fixture(budget,spec):
    from app.config import runtime_snapshot
    from app.domain import create_plan,estimate
    snapshot=runtime_snapshot();brief={'topic':'审核守卫夹具','budget':budget,'start_at':'2030-12-01T08:00:00+08:00','deadline':'2030-12-03T18:00:00+08:00','real_sources_required':False}
    plan=create_plan(brief,1,spec,'前置夹具');result=estimate(brief,spec,snapshot['production_rules']);result.update(run_id='fixture',plan_version_id=plan['plan_version_id'])
    run={'run_id':'fixture','plan_version_id':plan['plan_version_id'],'requirements':brief,'estimate_id':result['estimate_id'],
        'snapshot':snapshot,'replans':0,'script_id':'kept-script','status':'running','loaded_skills':[]}
    state={'active_run':run,'plans':[plan],'estimates':{result['estimate_id']:result},'draft':None,'timeline':[],'cost_summary':{}}
    return state,snapshot,result


@pytest.mark.asyncio
@pytest.mark.parametrize('budget,spec,candidates',[('5','simple',[]),('10','premium',['simple'])])
async def test_replan_never_offers_a_more_expensive_non_improving_candidate(monkeypatch,budget,spec,candidates):
    from app import graphs
    state,snapshot,result=budget_fixture(budget,spec);captured={};reasons=[]
    async def decision(sid,snapshot,gateway,kind,prompt,schema,run_id):
        captured['schema']=schema
        return {'production_spec':candidates[0] if candidates else spec,'reason':'测试夹具决策','changes':[],'stop':not candidates}
    monkeypatch.setattr(graphs,'controlled_json',decision)
    monkeypatch.setattr(graphs,'mutate',lambda sid,fn,*args:fn(state,None))
    monkeypatch.setattr(graphs,'assert_lease',lambda *args:None)
    runner=graphs.ExecutionRunner({'session_id':'fixture','payload':{'run_id':'fixture'}},snapshot,None)
    runner.run=lambda:(state,state['active_run']);runner.facts=lambda _:{'estimate':result};runner.mark_incomplete=reasons.append
    await runner.replan({})
    assert captured['schema']['properties']['production_spec']['enum']==(candidates or [spec])
    if not candidates:
        assert captured['schema']['properties']['stop']['const'] is True
        assert '预算缺口 3' in reasons[0] and len(state['plans'])==1
    else:
        assert len(state['plans'])==2 and state['plans'][-1]['production_spec']=='simple' and state['active_run']['script_id']=='kept-script'


def test_terminal_failure_preserves_known_constraint_gap(monkeypatch):
    from app import graphs
    state,snapshot,_=budget_fixture('5','simple')
    monkeypatch.setattr(graphs,'mutate',lambda sid,fn,*args:fn(state,None))
    monkeypatch.setattr(graphs,'assert_lease',lambda *args:None)
    runner=graphs.ExecutionRunner({'session_id':'fixture','payload':{'run_id':'fixture'}},snapshot,None)
    runner.mark_incomplete('模型决策轮数达到上限，已有结果保留。')
    assert '预算缺口 3' in state['active_run']['reason']


def test_estimation_uses_current_plan_instead_of_historical_brief_spec():
    from app.graphs import ExecutionRunner
    runner=ExecutionRunner({'session_id':'fixture','payload':{'run_id':'fixture'}},{},None)
    runner.facts=lambda _:{'phase':'estimation','requirements':{},'production_spec':'simple','plan_version':2,
        'evaluated_candidates':[],'last_error':None,'loaded_skills':[],'content_requests':['原计划必须premium，先不要执行'],'materials':['long source']}
    facts=runner.decision_facts({})
    assert facts['production_spec']=='simple' and 'content_requests' not in facts and 'materials' not in facts


def test_single_injected_fault_is_explicitly_retryable():
    from app.contracts import tool_error
    error=tool_error(ValueError('INJECTED_ONCE: 单次测试故障'))
    assert error['code']=='INJECTED_FAILURE' and error['retryable'] is True
