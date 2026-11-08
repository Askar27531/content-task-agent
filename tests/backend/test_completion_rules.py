import copy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import runtime_snapshot, ModelConfig
from app.contracts import validate_dependencies, require_dependencies, normalize_search, tool_error
from app.ledger import model_amount
from app.reuse import resource_only_update, writing_hash, script_signature, reusable


@pytest.mark.parametrize('steps',[
    [{'key':'a','dependencies':['a']}],
    [{'key':'a','dependencies':['b']},{'key':'b','dependencies':['a']}],
    [{'key':'a','dependencies':['missing']}],
    [{'key':'a'},{'key':'a'}],
])
def test_dependency_contract_rejects_invalid_graphs(steps):
    with pytest.raises(ValueError):validate_dependencies(steps)


def test_dependencies_require_actual_usable_result():
    steps=[{'key':'sources','status':'failed'},{'key':'script','dependencies':['sources']}]
    with pytest.raises(ValueError):require_dependencies({'steps':steps},steps[1])
    steps[0]['status']='partial_success'
    require_dependencies({'steps':steps},steps[1])


@pytest.mark.parametrize('results,count,partial',[
    ([],0,False),
    ([{'url':'https://example.invalid/fixture','content':'明确标注的协议测试资料'}],1,False),
    ([{'url':'https://example.invalid/fixture','content':'协议资料'},{'url':'javascript:alert(1)','content':'不合法'}],1,True),
    ([{'url':'https://example.invalid/fixture','content':'one'},{'url':'https://example.invalid/fixture','content':'duplicate'}],1,False),
])
def test_search_normalizes_usable_empty_and_partial_results(results,count,partial):
    result=normalize_search({'results':results},'fixture',requested=4,now='2030-01-01T00:00:00+00:00')
    assert len(result['materials'])==count and result['partial']==partial
    assert result['empty']==(count==0)
    assert all(item['retrieved_at']=='2030-01-01T00:00:00+00:00' for item in result['materials'])


@pytest.mark.parametrize('data',[{}, {'results':None}, {'results':'wrong'}])
def test_malformed_search_is_not_an_empty_success(data):
    with pytest.raises(ValueError):normalize_search(data,'query',requested=4)


def test_unknown_model_price_is_not_zero_and_configured_price_is_exact():
    assert model_amount({}, {'input_tokens':10,'output_tokens':20}) is None
    profile={'input_cost_per_million':'1','output_cost_per_million':'2'}
    assert model_amount(profile,{'input_tokens':10,'output_tokens':20})==Decimal('0.00005')
    assert model_amount(profile,{'input_tokens':None,'output_tokens':20}) is None
    assert tool_error(TimeoutError(),result_unknown=True)['retryable']


@pytest.mark.parametrize('rate',['abc','NaN','Infinity','-1'])
def test_model_rates_reject_invalid_values_with_validation_error(rate):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ModelConfig(provider='ollama',base_url='http://localhost',model='fixture',input_cost_per_million=rate)


def test_budget_only_updates_can_reuse_writing_but_content_changes_cannot():
    assert resource_only_update('预算改为8个模拟单位，请先给新计划，不要执行。',{'budget':'8'})
    assert not resource_only_update('预算改为8，少做一张海报',{'budget':'8'})
    assert not resource_only_update('文案改活泼一些',{})
    initial={'role':'user','content':'咖啡活动，预算10，文案温暖','is_requirement_update':True}
    state={'timeline':[initial]};before=writing_hash(state)
    state['timeline'].append({'role':'user','content':'预算改为8','is_requirement_update':True,'resource_only_update':True})
    assert writing_hash(state)==before


def test_script_dependency_signature_changes_with_model_skill_and_material_content():
    snapshot=runtime_snapshot();run={'requirements':{'topic':'咖啡'},'writing_hash':'same'}
    materials=[{'material_id':'s','source_type':'synthetic','summary':'fixture'}]
    original=script_signature(run,snapshot,materials)
    modified=copy.deepcopy(snapshot);modified['selected_model']['model']='different'
    assert script_signature(run,modified,materials)!=original
    modified=copy.deepcopy(materials);modified[0]['summary']='changed'
    assert script_signature(run,snapshot,modified)!=original


@pytest.mark.asyncio
@pytest.mark.parametrize('count',[0,1])
async def test_search_retry_notice_does_not_claim_preserved_results_when_empty(monkeypatch,count):
    from app import graphs
    run={'run_id':'fixture','requirements':{},'research_retries':0}
    state={'active_run':run,'materials':[{'source_type':'web'}]*count,'timeline':[]}
    async def decision(*args,**kwargs):return {'action':'retry','reason':'测试夹具：再次检索。'}
    monkeypatch.setattr(graphs,'controlled_json',decision)
    monkeypatch.setattr(graphs,'assert_lease',lambda *args:None)
    monkeypatch.setattr(graphs,'mutate',lambda sid,fn,*args:fn(state,None))
    runner=graphs.ExecutionRunner({'session_id':'fixture','payload':{'run_id':'fixture'}},
        {'search':{'min_results':1},'limits':{'max_tool_attempts':3}},None)
    runner.run=lambda:(state,run)
    await runner.review_materials({})
    text=state['timeline'][-1]['content']
    assert '有限重试' in text
    assert ('未取得可用素材' in text) == (count==0)
    assert ('已保留1项有效素材' in text) == (count==1)


@pytest.mark.asyncio
async def test_exhausted_run_suggests_new_plan_instead_of_promising_more_retries(monkeypatch):
    from app import graphs
    run={'status':'incomplete','reason':'搜索重试上限','reason_code':'LIMIT_REACHED','last_tool_failed':True}
    messages=[]
    monkeypatch.setattr(graphs,'append_assistant',lambda sid,text:messages.append(text))
    runner=graphs.ExecutionRunner({'session_id':'fixture','payload':{'run_id':'fixture'}},{},None)
    runner.run=lambda:({'materials':[],'scripts':{}},run)
    await runner.finalize_run({})
    assert '从失败处继续' not in messages[0] and '新计划' in messages[0]
