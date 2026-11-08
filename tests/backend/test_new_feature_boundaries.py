"""New feature boundary contracts. Explicit domain fixtures; no LLM-selection claim."""
import copy
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import runtime_snapshot
from app.contracts import normalize_search
from app.domain import create_plan
from app.reuse import reusable, script_signature
from app.store import (CostEntryRow, JobRow, create_session, db_factory, read_state, record_model_call,
                       receive_message, claim_job, finish_job, uid, mutate, heartbeat, utcnow)

PG=pytest.mark.skipif(os.getenv('CONTENT_STUDIO_DB_TESTS')!='1',reason='Opt-in PostgreSQL boundary tests')


def reuse_fixture():
    snapshot=runtime_snapshot()
    brief={'topic':'边界测试夹具','budget':'20','deadline':'2030-12-03T18:00:00+08:00','start_at':None,'real_sources_required':False}
    old={'run_id':'old','requirements':brief,'snapshot':copy.deepcopy(snapshot),'writing_hash':'same','script_id':'script'}
    plan=create_plan(brief,1,'simple','边界测试前置夹具');old['plan_version_id']=plan['plan_version_id']
    for step in plan['steps']:step['status']='success'
    materials=[{'material_id':'web-fixture','source_type':'web','title':'协议夹具','summary':'非真实资料',
                'url':'https://example.invalid/boundary','retrieved_at':datetime.now(timezone.utc).isoformat()}]
    old['script_signature']=script_signature(old,snapshot,materials)
    new={'run_id':'new','requirements':copy.deepcopy(brief),'snapshot':snapshot,'writing_hash':'same'}
    state={'plans':[plan],'scripts':{'script':{'script_id':'script','run_id':'old','script':'前置文字夹具'}},
           'runs':{'old':{'run':old,'materials':materials}}}
    return state,new


def test_reuse_respects_increased_minimum_sources():
    state,new=reuse_fixture();new['snapshot']['search']['min_results']=2
    assert reusable(state,new) is None


def test_failed_script_step_does_not_reuse_unconfirmed_script():
    state,new=reuse_fixture();state['plans'][0]['steps'][1]['status']='failed'
    result=reusable(state,new)
    assert result and result['script'] is None


@pytest.mark.parametrize('date',['invalid',None,'2030-01-01T00:00:00','2099-01-01T00:00:00+00:00'])
def test_reuse_rejects_unusable_or_future_source_timestamp_without_crashing(date):
    state,new=reuse_fixture();state['runs']['old']['materials'][0]['retrieved_at']=date
    assert reusable(state,new) is None


@pytest.mark.parametrize('change',['model','skill','writing','catalog','endpoint','expired','strict_synthetic','failed_sources'])
def test_reuse_invalidation_matrix(change):
    state,new=reuse_fixture()
    if change=='model':new['snapshot']['selected_model']['model']='changed-model'
    if change=='skill':next(s for s in new['snapshot']['skills'] if s['skill_id']=='script-writing')['version']='changed-version'
    if change=='writing':new['writing_hash']='new-copy-requirements'
    if change=='catalog':new['snapshot']['material_catalog_hash']='changed-catalog'
    if change=='endpoint':new['snapshot']['mcp']['tavily_url']='https://different.invalid/mcp'
    if change=='expired':state['runs']['old']['materials'][0]['retrieved_at']=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
    if change=='strict_synthetic':
        state['runs']['old']['materials'][0]['source_type']='synthetic';new['requirements']['real_sources_required']=True
    if change=='failed_sources':state['plans'][0]['steps'][0]['status']='failed'
    result=reusable(state,new)
    if change in {'model','skill'}:assert result and result['script'] is None
    else:assert result is None


@pytest.mark.parametrize('bad_url',['http://[invalid','https://[::1','javascript:alert(1)','file:///local'])
def test_one_malformed_url_cannot_discard_other_usable_results(bad_url):
    data={'results':[{'url':bad_url,'content':'错误链接夹具'},
                     {'url':'https://example.invalid/valid','title':'可用协议夹具','content':'有效文字'}]}
    result=normalize_search(data,'query',requested=4)
    assert result['partial'] and len(result['materials'])==1 and result['available_count']==1


@pytest.mark.parametrize('title',[{},[],True,42])
def test_non_text_search_title_is_not_forwarded_to_react(title):
    result=normalize_search({'results':[{'url':'https://example.invalid/a','title':title,'content':'协议文字'}]},'q',requested=4)
    assert not result['materials'] and result['empty'] and result['missing']


@PG
@pytest.mark.parametrize('changed',['disabled','min_results','material_expired'])
def test_existing_cache_is_not_reused_when_new_policy_forbids_it(changed):
    from app import search
    snapshot=runtime_snapshot();query='cache-policy-'+uid()
    data={'materials':[{'material_id':'fixture','retrieved_at':(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()}]}
    search.store_cache(snapshot,query,data)
    assert search.cached(snapshot,query)
    if changed=='disabled':snapshot['search']['cache_ttl_seconds']=0
    if changed=='min_results':snapshot['search']['min_results']=2
    if changed=='material_expired':snapshot['search']['material_ttl_seconds']=1
    assert search.cached(snapshot,query) is None


@PG
def test_terminal_model_request_cannot_be_regressed_by_replayed_intent():
    sid=create_session()['session_id'];profile={'provider':'ollama','model':'fixture','input_cost_per_million':'1','output_cost_per_million':'2'}
    request=record_model_call(sid,None,'fixture',profile,{'content':'actual fixture response'},{'input_tokens':10,'output_tokens':20})
    record_model_call(sid,None,'fixture',profile,{'status':'running'},{'input_tokens':None,'output_tokens':None},request_id=request)
    state=read_state(sid)
    assert len(state['model_calls'])==1 and state['cost_summary']['model_input_tokens']==10
    with db_factory() as db:
        row=db.scalar(select(CostEntryRow).where(CostEntryRow.session_id==sid))
        assert row.status=='estimated' and row.amount==Decimal('0.00005')


@PG
def test_concurrent_duplicate_acceptance_creates_exactly_one_job_and_message():
    sid=create_session()['session_id'];key=uid();queue='test-'+uid()[:8]+':message'
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts=list(pool.map(lambda _:receive_message(sid,'并发重复受理夹具',key,{},queue_kind=queue),range(12)))
    assert sum(not item['duplicate'] for item in receipts)==1
    assert len({item['processing_id'] for item in receipts})==1 and len(read_state(sid)['timeline'])==1
    with db_factory() as db:assert len(db.scalars(select(JobRow).where(JobRow.session_id==sid)).all())==1


@PG
def test_concurrent_workers_cannot_claim_two_jobs_in_same_session_role():
    sid=create_session()['session_id'];queue='test-'+uid()[:8]+':run'
    with db_factory.begin() as db:
        for _ in range(8):db.add(JobRow(session_id=sid,kind=queue,status='queued',payload={}))
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims=list(pool.map(lambda n:claim_job(queue,'worker-'+str(n)),range(8)))
    claimed=[job for job in claims if job]
    assert len(claimed)==1
    finish_job(claimed[0])
    # Leave no running tasks; all jobs are in the private test queue.
    with db_factory.begin() as db:
        for job in db.scalars(select(JobRow).where(JobRow.session_id==sid)):job.status='done'


@PG
def test_mixed_currencies_and_unknown_usage_never_become_one_money_total():
    sid=create_session()['session_id']
    for currency,usage in [('USD',{'input_tokens':10,'output_tokens':20}),('CNY',{'input_tokens':10,'output_tokens':20}),('USD',{'input_tokens':None,'output_tokens':None})]:
        profile={'provider':'deepseek','model':'protocol-fixture','currency':currency,'input_cost_per_million':'1','output_cost_per_million':'2'}
        record_model_call(sid,None,'fixture',profile,{'content':'fixture'},usage)
    summary=read_state(sid)['cost_summary']
    assert summary['model_cost'] is None and summary['model_unknown_usage_calls']==1
    assert summary['model_input_tokens']==20 and summary['model_output_tokens']==40


@PG
@pytest.mark.parametrize('action',['heartbeat','finish','finish_again'])
def test_expired_or_terminal_worker_cannot_renew_or_rewrite_job(action):
    sid=create_session()['session_id'];queue='test-'+uid()[:8]
    with db_factory.begin() as db:db.add(JobRow(session_id=sid,kind=queue,status='queued',payload={}))
    job=claim_job(queue,'expired-worker')
    if action=='finish_again':finish_job(job)
    else:
        with db_factory.begin() as db:db.get(JobRow,job['job_id']).lease_until=utcnow()-timedelta(seconds=1)
    if action=='heartbeat':assert heartbeat(job) is False
    else:
        finish_job(job,'late failure')
        with db_factory() as db:
            row=db.get(JobRow,job['job_id'])
            assert row.status==('done' if action=='finish_again' else 'running') and row.error is None


@PG
def test_cleanup_error_after_committed_save_cannot_announce_task_incomplete():
    from app.worker import record_failure
    sid=create_session()['session_id'];queue='test-'+uid()[:8]
    with db_factory.begin() as db:db.add(JobRow(session_id=sid,kind=queue,status='queued',payload={'run_id':'saved-fixture'}))
    job=claim_job(queue,'cleanup-fixture')
    mutate(sid,lambda state,_:state.update(active_run={'run_id':'saved-fixture','status':'completed','plan_version_id':'saved-plan-fixture'}))
    record_failure(job,'run','测试夹具：保存后的连接清理异常')
    state=read_state(sid)
    assert state['active_run']['status']=='completed' and not state['timeline']
    finish_job(job)


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,elapsed',[('LIMIT_REACHED',0),('AGENT_TIMEOUT',0),(None,900)])
async def test_resume_does_not_requeue_exhausted_or_timed_out_run(monkeypatch,reason,elapsed):
    from app import graphs
    state={'plans':[],'active_run':{'status':'incomplete','reason_code':reason,'elapsed_seconds':elapsed,
        'snapshot':{'limits':{'max_model_rounds':24,'max_business_calls':30,'run_timeout_seconds':900}}}}
    monkeypatch.setattr(graphs,'mutate',lambda sid,fn,*args:fn(state,None))
    monkeypatch.setattr(graphs,'assert_lease',lambda *args:None)
    runner=graphs.DialogueRunner({'session_id':'fixture','payload':{}},{},None)
    reply=await runner.apply_chat_action({'intent':{'action':'resume'}})
    assert '不能通过续跑重置' in reply['reply'] and state['active_run']['status']=='incomplete'


@PG
def test_old_run_cost_endpoint_matches_its_declared_session_model_scope():
    from fastapi.testclient import TestClient
    from app.api import app
    sid=create_session()['session_id'];profile={'provider':'ollama','model':'fixture','input_cost_per_million':'1','output_cost_per_million':'1'}
    old_id='old-cost-'+uid();new_id='new-cost-'+uid()
    def activate(state,_,run_id):
        state['active_run']={'run_id':run_id,'status':'completed','plan_version_id':'plan-'+run_id,'snapshot':{}}
    mutate(sid,lambda state,db:activate(state,db,old_id))
    record_model_call(sid,old_id,'fixture',profile,{'content':'one'},{'input_tokens':10,'output_tokens':20})
    mutate(sid,lambda state,db:activate(state,db,new_id))
    record_model_call(sid,new_id,'fixture',profile,{'content':'two'},{'input_tokens':30,'output_tokens':40})
    body=TestClient(app).get(f'/api/runs/{old_id}/costs').json()
    assert body['model_usage_scope']=='session'
    assert body['model_input_tokens']==40 and body['model_output_tokens']==60
    assert Decimal(body['model_cost'])==sum(Decimal(row['amount']) for row in body['entries'])


@pytest.mark.parametrize('key,fragment,value',[
    ('budget','主题和预算不变','20'),('deadline','截止时间沿用原来的','2030-12-03T18:00:00+08:00'),
    ('budget','预算不变，只作废旧文案','20'),('start_at','开始时间保持原值','2030-12-01T08:00:00+08:00')])
def test_explicit_keep_existing_fields_does_not_clear_them_during_copy_change(key,fragment,value):
    from app.input_rules import inspect_input
    current={'topic':'读书会',key:value}
    analysis=inspect_input({'updates':{key:value},'evidence':{key:fragment}},fragment+'，改成口语文案，先给新计划。',current)
    assert key not in analysis['field_issues'] and analysis['patch'].get(key,value)==value


@pytest.mark.asyncio
async def test_strict_insufficient_search_review_cannot_choose_synthetic_or_accept(monkeypatch):
    from app import graphs
    captured={};state={'materials':[]};run={'requirements':{'real_sources_required':True},'brief_messages':['真实资料不足时先重检一次。']}
    async def decision(sid,snapshot,gateway,kind,prompt,schema,run_id):
        captured.update(prompt=prompt,schema=schema);return {'action':'stop','reason':'明确的测试夹具停止'}
    monkeypatch.setattr(graphs,'controlled_json',decision)
    monkeypatch.setattr(graphs,'source_limit_reached',lambda *args:False)
    runner=graphs.ExecutionRunner({'session_id':'fixture','payload':{'run_id':'fixture'}},
        {'search':{'min_results':2},'limits':{'max_tool_attempts':3}},None)
    runner.run=lambda:(state,run);runner.mark_incomplete=lambda *args:None
    await runner.review_materials({})
    assert set(captured['schema']['properties']['action']['enum'])=={'stop','retry'}
    assert '真实资料不足时先重检一次' in captured['prompt']


@pytest.mark.parametrize('message,limit',[
    ('资料不齐先重检一次，仍不足就停止。',1),('搜索失败最多重试2次。',2),
    ('搜索失败禁止重试。',0),('文案失败重试一次。',2),('资料失败最多重试9次。',2)])
def test_explicit_source_retry_count_cannot_be_exceeded(message,limit):
    from app.graphs import source_retry_limit
    assert source_retry_limit({'snapshot':{'limits':{'max_tool_attempts':3}},'brief_messages':[message]})==limit
