import asyncio
import os
from decimal import Decimal

import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.api import app
from app.config import runtime_snapshot
from app.store import (CostEntryRow, JobRow, create_session, uid, db_factory, claim_job, finish_job,
                       read_state, mutate, record_model_call)

pytestmark=pytest.mark.skipif(os.getenv('CONTENT_STUDIO_DB_TESTS')!='1',reason='Opt-in PostgreSQL tests')


def test_model_request_intent_completion_and_cost_are_durable_and_idempotent():
    sid=create_session()['session_id']
    profile={'provider':'ollama','model':'protocol-fixture','currency':'USD',
             'input_cost_per_million':'1','output_cost_per_million':'2'}
    request=record_model_call(sid,None,'fixture',profile,{'status':'running'}, {'input_tokens':None,'output_tokens':None})
    with db_factory() as db:
        pending=db.scalar(select(CostEntryRow).where(CostEntryRow.session_id==sid))
        assert pending.amount is None and pending.status=='pending'
    for _ in range(2):
        record_model_call(sid,None,'fixture',profile,{'content':'fixture'}, {'input_tokens':10,'output_tokens':20},request_id=request)
    state=read_state(sid)
    assert len(state['model_calls'])==1 and state['cost_summary']['model_input_tokens']==10
    assert Decimal(state['cost_summary']['model_cost'])==Decimal('0.00005')
    with db_factory() as db:
        rows=db.scalars(select(CostEntryRow).where(CostEntryRow.session_id==sid)).all()
        assert len(rows)==1 and rows[0].amount==Decimal('0.00005')


def test_active_job_exclusion_keeps_message_order_and_allows_other_sessions():
    sid=create_session()['session_id'];other=create_session()['session_id'];queue='test-'+uid()[:8]
    with db_factory.begin() as db:
        for session in [sid,sid,other]:db.add(JobRow(id=uid(),session_id=session,kind=queue,status='queued',payload={}))
    first=claim_job(queue,'first');second=claim_job(queue,'second')
    assert first['session_id']!=second['session_id']
    assert claim_job(queue,'third') is None
    finish_job(first);finish_job(second)
    third=claim_job(queue,'third');assert third and third['session_id']==sid
    finish_job(third)


def test_plans_and_proposals_pagination_preserves_versions_without_mutating_state():
    sid=create_session()['session_id']
    def setup(state,_):
        state['plans']=[{'plan_version_id':str(i)} for i in range(5)]
        state['proposals']=[{'proposal_id':str(i)} for i in range(5)]
    mutate(sid,setup)
    client=TestClient(app)
    for resource in ['plans','proposals']:
        response=client.get(f'/api/sessions/{sid}/{resource}?limit=2');assert response.status_code==200
        value=response.json();assert value['has_more'] and len(value['items'])==2
        next_page=client.get(f'/api/sessions/{sid}/{resource}',params={'limit':2,'cursor':value['next_cursor']}).json()
        assert next_page['items']!=value['items']
    assert len(read_state(sid)['plans'])==5


@pytest.mark.asyncio
async def test_worker_losing_lease_cancels_dispatch_without_claiming_job_finished(monkeypatch):
    from app import worker
    sid=create_session()['session_id'];queue='test-'+uid()[:8]
    with db_factory.begin() as db:db.add(JobRow(id=uid(),session_id=sid,kind=queue,status='queued',payload={}))
    job=claim_job(queue,'fixture',3);cancelled=asyncio.Event()
    async def blocked(*args):
        try:await asyncio.sleep(30)
        finally:cancelled.set()
    monkeypatch.setattr(worker,'execute_job',blocked)
    monkeypatch.setattr(worker,'heartbeat',lambda *args:False)
    await worker.process_job(job,'message',None,lease_seconds=3)
    assert cancelled.is_set()
    with db_factory() as db:assert db.get(JobRow,job['job_id']).status=='running'
    finish_job(job)


def test_search_cache_deduplicates_query_and_honors_expiry_and_endpoint():
    from app import search
    from app.store import SearchCacheRow, utcnow
    from datetime import timedelta
    snapshot=runtime_snapshot();query='fixture '+uid()
    data={'materials':[{'material_id':'fixture','retrieved_at':utcnow().isoformat()}]}
    search.store_cache(snapshot,query,data)
    assert search.cached(snapshot,' '+query+' ')['reused']
    changed={**snapshot,'mcp':{**snapshot['mcp'],'tavily_url':'http://different.invalid/mcp'}}
    assert search.cached(changed,query) is None
    with db_factory.begin() as db:db.get(SearchCacheRow,search.cache_key(snapshot,query)).expires_at=utcnow()-timedelta(seconds=1)
    assert search.cached(snapshot,query) is None


def test_event_associations_and_snapshot_to_subscribe_gap_are_durable():
    from app.store import EventRow, event_envelope, add_message
    sid=create_session()['session_id'];water=read_state(sid)['last_event_seq']
    identifier=mutate(sid,lambda state,_:add_message(state,'gap fixture'),'message.completed',{'message_id':'fixture-id'})
    with db_factory() as db:
        rows=db.scalars(select(EventRow).where(EventRow.session_id==sid,EventRow.seq>water)).all()
        assert len(rows)==1
        event=event_envelope(rows[0]);assert event['message_id']=='fixture-id'
        assert event['session_seq']==water+1 and event['event_id']==f'{sid}:{water+1}'
