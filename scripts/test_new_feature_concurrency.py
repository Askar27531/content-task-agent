"""Real PG contention; own test queues and sessions, no model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src/backend'))
from sqlalchemy import select
from app.store import create_session, db_factory, JobRow, claim_job, finish_job, uid, record_model_call, read_state, CostEntryRow


def main():
    rounds=[]
    for number in range(40):
        sid=create_session()['session_id'];queue='test-'+uid()[:8]+':run'
        with db_factory.begin() as db:
            for _ in range(8):db.add(JobRow(session_id=sid,kind=queue,status='queued',payload={}))
        errors=[]
        def claim(n):
            try:return claim_job(queue,'stress-'+str(n))
            except Exception as exc:errors.append(type(exc).__name__+':'+str(exc)[:160]);return None
        with ThreadPoolExecutor(max_workers=8) as pool:jobs=[job for job in pool.map(claim,range(8)) if job]
        checks={'one_claim':len(jobs)==1,'no_worker_exception':not errors}
        rounds.append({'round':number,'session_id':sid,'claims':len(jobs),'errors':errors,'checks':checks})
        for job in jobs:finish_job(job)
        with db_factory.begin() as db:
            for row in db.scalars(select(JobRow).where(JobRow.session_id==sid)):row.status='done'
    sid=create_session()['session_id'];request_id=uid();profile={'provider':'ollama','model':'cost-fixture','input_cost_per_million':'1','output_cost_per_million':'2'}
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _:record_model_call(sid,None,'fixture',profile,{'content':'并发同一响应夹具'},
            {'input_tokens':10,'output_tokens':20},request_id=request_id),range(20)))
    with db_factory() as db:entries=db.scalars(select(CostEntryRow).where(CostEntryRow.session_id==sid)).all()
    state=read_state(sid)
    charges={'one_attempt':len(state['model_calls'])==1,'one_charge':len(entries)==1,'tokens_once':state['cost_summary']['model_input_tokens']==10}
    result={'real_postgresql':True,'llm_called':False,'worker_claims':rounds,'duplicate_charge_checks':charges,
        'passed':all(all(r['checks'].values()) for r in rounds) and all(charges.values())}
    path=ROOT/'examples/new-feature-boundaries/after/concurrency.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('claim rounds:',len(rounds),'passed:',sum(all(r['checks'].values()) for r in rounds),'charge:',charges)
    assert result['passed']


if __name__=='__main__':main()
