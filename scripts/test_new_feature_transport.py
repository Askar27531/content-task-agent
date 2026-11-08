"""Actual HTTP/SSE batch, gaps and pagination. Own fixture events; no LLM."""
import argparse
import json
from sqlalchemy import delete, select, func
import verify_backend_completion as base
from verify_backend_transport import frames
from app.store import create_session, mutate, add_message, db_factory, EventRow, JobRow, OperationRow, CostEntryRow, read_state


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['before','after'],default='after');args=parser.parse_args()
    base.OUTPUT=base.ROOT/'examples/new-feature-boundaries'/args.stage
    harness=base.Harness();checks={};captured={}
    try:
        sid=create_session()['session_id']
        for number in range(140):mutate(sid,lambda state,_,n=number:add_message(state,f'仅传输验证夹具 #{n}'),'message.completed')
        captured['batch']=frames(harness.http,sid,0,140)
        checks['more_than_100_events_without_gaps']=[f['session_seq'] for f in captured['batch']]==list(range(1,141))
        first=harness.http.get(f'/sessions/{sid}/messages',params={'limit':50}).json()
        mutate(sid,lambda state,_:add_message(state,'分页读取期间新追加的夹具'),'message.completed')
        second=harness.http.get(f'/sessions/{sid}/messages',params={'limit':50,'cursor':first['next_cursor']}).json()
        a={m['item_id'] for m in first['items']};b={m['item_id'] for m in second['items']}
        checks['pagination_stays_disjoint_when_new_messages_append']=len(a)==50 and len(b)==50 and not (a&b)
        checks['pagination_does_not_mutate_messages']=len(read_state(sid)['timeline'])==141
        captured['reconnect']=frames(harness.http,sid,0,41,{'Last-Event-ID':sid+':100'})
        checks['header_reconnect_crosses_batch_boundary']=[f['session_seq'] for f in captured['reconnect']]==list(range(101,142))
        captured['replay']=frames(harness.http,sid,100,41)
        checks['replay_event_ids_stable']=[f['event_id'] for f in captured['replay']]==[f['event_id'] for f in captured['reconnect']]
        with db_factory.begin() as db:db.execute(delete(EventRow).where(EventRow.session_id==sid,EventRow.seq==6))
        captured['internal_gap']=frames(harness.http,sid,5)
        checks['internal_event_gap_requires_resync']=captured['internal_gap'][0]['type']=='resync_required'
        response=harness.http.get(f'/sessions/{sid}/events',headers={'Last-Event-ID':sid+':'+'9'*4400})
        captured['oversized_header']={'status':response.status_code,'body':response.text[:200]}
        checks['oversized_header_is_validation_error']=response.status_code==422
        response=harness.http.get(f'/sessions/{sid}/events',params={'after':-1})
        checks['negative_watermark_rejected']=response.status_code==422
        captured['future']=frames(harness.http,sid,9999)
        checks['future_watermark_requires_resync']=captured['future'][0]['type']=='resync_required'
        for cursor in ['%%%','LTI=','OTk5OQ==']:
            response=harness.http.get(f'/sessions/{sid}/messages',params={'cursor':cursor})
            checks['invalid_cursor_'+cursor]=response.status_code==422
        with db_factory() as db:
            counts={model.__tablename__:db.scalar(select(func.count()).select_from(model).where(model.session_id==sid)) for model in [JobRow,OperationRow,CostEntryRow]}
        checks['transport_never_dispatches_or_charges']=not any(counts.values())
        folder=base.OUTPUT/'transport';folder.mkdir(exist_ok=True)
        (folder/'evidence.json').write_text(json.dumps({'session_id':sid,'actual_http_sse':True,'fixture_only':True,'checks':checks,
            'passed':all(checks.values()),'captured':captured,'counts':counts},ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(checks,ensure_ascii=False));assert all(checks.values())
    finally:harness.close()


if __name__=='__main__':main()
