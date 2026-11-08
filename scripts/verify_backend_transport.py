"""Actual HTTP/SSE + PostgreSQL replay. Own fixture session, no LLM or business calls."""
from __future__ import annotations
import json
from pathlib import Path
from sqlalchemy import delete, select, func
from verify_backend_completion import Harness, OUTPUT
from app.store import create_session, mutate, read_state, add_message, db_factory, EventRow, JobRow, OperationRow, CostEntryRow


def frames(client, sid, after=0, count=1, headers=None):
    result=[]
    with client.stream('GET',f'/sessions/{sid}/events',params={'after':after},headers=headers or {}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith('data: '):
                result.append(json.loads(line[6:]))
                if len(result)>=count:break
    return result


def main():
    harness=Harness()
    try:
        sid=create_session()['session_id']; captured={};checks={}
        def append(text):
            return mutate(sid,lambda state,_:add_message(state,text), 'message.completed')
        snapshot=harness.http.get(f'/sessions/{sid}').json()
        append('HTTP/SSE协议验证：快照后、订阅前写入。')
        captured['snapshot_subscription_gap']=frames(harness.http,sid,snapshot['last_event_seq'])
        checks['snapshot_subscription_gap']=captured['snapshot_subscription_gap'][0]['session_seq']==1
        append('HTTP/SSE协议验证：断线期间第一条。');append('HTTP/SSE协议验证：断线期间第二条。')
        captured['header_reconnect']=frames(harness.http,sid,0,2,{'Last-Event-ID':sid+':1'})
        checks['last_event_id_precedence']= [f['session_seq'] for f in captured['header_reconnect']]==[2,3]
        captured['repeated_replay']=frames(harness.http,sid,1,2)
        checks['stable_replay_ids']= [f['event_id'] for f in captured['repeated_replay']]==[f['event_id'] for f in captured['header_reconnect']]
        checks['no_duplicate_messages']=len(read_state(sid)['timeline'])==3
        captured['future_cursor']=frames(harness.http,sid,99)
        checks['future_resync']=captured['future_cursor'][0]['type']=='resync_required'
        response=harness.http.get(f'/sessions/{sid}/events',headers={'Last-Event-ID':'other-session:1'})
        captured['wrong_session']=response.json();checks['wrong_session_rejected']=response.status_code==409
        with db_factory.begin() as db:db.execute(delete(EventRow).where(EventRow.session_id==sid,EventRow.seq==1))
        captured['expired_cursor']=frames(harness.http,sid,0)
        checks['expired_cursor_resync']=captured['expired_cursor'][0]['type']=='resync_required'
        with db_factory.begin() as db:db.execute(delete(EventRow).where(EventRow.session_id==sid))
        captured['all_events_expired']=frames(harness.http,sid,0)
        checks['all_events_expired_resync']=captured['all_events_expired'][0]['type']=='resync_required'
        counts={}
        with db_factory() as db:
            for model in [JobRow,OperationRow,CostEntryRow]:
                counts[model.__tablename__]=db.scalar(select(func.count()).select_from(model).where(model.session_id==sid))
        checks['read_only_replay_has_no_jobs_tools_costs']=not any(counts.values())
        folder=OUTPUT/'transport';folder.mkdir(exist_ok=True)
        (folder/'evidence.json').write_text(json.dumps({'session_id':sid,'fixture_messages_only':True,'actual_http_sse':True,
            'checks':checks,'passed':all(checks.values()),'captured_frames':captured,'counts':counts,
            'workspace':harness.http.get(f'/sessions/{sid}').json()},ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(checks,ensure_ascii=False));assert all(checks.values())
    finally:
        harness.http.close()
        for process in reversed(harness.children):
            from verify_backend_completion import stop
            stop(process)
        for stream in harness.files:stream.close()


if __name__=='__main__':main()
