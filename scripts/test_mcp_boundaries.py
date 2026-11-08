"""Real MCP protocol/business guard tests; explicitly not LLM tool-selection evidence."""
from __future__ import annotations
import asyncio
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src/backend'))

from mcp import Client
from app.config import runtime_snapshot
from app.domain import create_plan
from app.gateway import Gateway
from app.mcp_server import ExecutionContext
from app.store import (JobRow, OperationRow, create_session, claim_job, db_factory, mutate, read_state,
                       finish_job, tool_view, uid)
from sqlalchemy import select


async def main():
    snapshot=runtime_snapshot();sid=create_session()['session_id'];run_id=uid();kind='mcp-boundary-'+uid()[:8]
    with db_factory.begin() as db:
        db.add(JobRow(id=uid(),session_id=sid,kind=kind,status='queued',payload={'snapshot':snapshot}))
    job=claim_job(kind,'boundary-probe')
    brief={'topic':'MCP协议守卫验证（非模型运行）','budget':'10','deadline':'2030-12-03T18:00:00+08:00','start_at':None,'real_sources_required':False}
    def setup(state,_db):
        plan=create_plan(brief,1,'simple','仅验证拒绝路径，不交付活动方案')
        state['requirements']=brief;state['session']['title']=brief['topic'];state['plans']=[plan]
        state['active_run']={'run_id':run_id,'status':'running','plan_version_id':plan['plan_version_id'],'snapshot':snapshot,
                             'requirements':brief,'loaded_skills':[],'control_status':None}
    mutate(sid,setup,'boundary.setup');results=[]
    try:
        async with Client(snapshot['mcp']['business_url']) as client:
            tools=await client.list_tools();names=[t.name for t in tools.tools]
            results.append({'case':'registered-tools','passed':set(names)=={'load_skill','read_skill_resource','search_synthetic_materials','generate_script','estimate_proposal','validate_proposal','save_proposal'},'tools':names})
            async def rejected(name,args,registered=True):
                op_id=uid();execution=ExecutionContext(operation_id=op_id,session_id=sid,run_id=run_id,job_id=job['job_id'],owner=job['owner'],lease_epoch=job['lease_epoch'])
                if registered:
                    with db_factory.begin() as db:
                        db.add(OperationRow(id=op_id,session_id=sid,run_id=run_id,step_id='boundary-probe',operation_key=op_id,
                                            tool_name=name,display_name=name,server_name='content-studio-business',status='running',public_input=args))
                response=await client.call_tool(name,{**args,'execution':execution.model_dump()})
                text='; '.join(getattr(block,'text','') for block in response.content)
                results.append({'case':name,'arguments':args,'registered_test_context':registered,'is_error':response.is_error,
                                'message':text,'passed':response.is_error})
                return execution
            await rejected('save_proposal',{'validation_id':'forged','estimate_id':'forged'},False)
            await rejected('save_proposal',{'validation_id':'stale-check','estimate_id':'stale-estimate'})
            await rejected('load_skill',{'skill_id':'not-registered'})
            execution=await rejected('read_skill_resource',{'skill_id':'campaign-planning','resource_id':'../../.env'})
            again=await client.call_tool('read_skill_resource',{'skill_id':'campaign-planning','resource_id':'../../.env','execution':execution.model_dump()})
            results.append({'case':'failed-attempt-replay','passed':again.is_error,'is_error':again.is_error})
        async with Gateway(sid,job,snapshot,run_id) as gateway:
            for name,args,allowed in [('save_proposal',{'validation_id':'x','estimate_id':'y'},{'generate_script'}),
                                      ('load_skill',{'skill_id':'campaign-planning','execution':{}},{'load_skill'})]:
                with db_factory() as db: before=len(db.scalars(select(OperationRow).where(OperationRow.session_id==sid)).all())
                error=None
                try:await gateway.call(name,args,allowed)
                except ValueError as exc:error=str(exc)
                with db_factory() as db: after=len(db.scalars(select(OperationRow).where(OperationRow.session_id==sid)).all())
                results.append({'case':'phase-or-context-guard-'+name,'passed':bool(error) and before==after,'error':error,'actual_dispatches':after-before})
        state=read_state(sid)
        results.append({'case':'no-save-no-charge','passed':not state['proposals'] and state['cost_summary']['tool_simulated']=='0.00'})
    finally:
        def end(state,_db):
            state['active_run']['status']='incomplete';state['active_run']['reason']='MCP协议守卫验证结束，未执行内容活动、未保存活动方案。';state['plans'][0]['status']='incomplete'
        mutate(sid,end,'boundary.finished');finish_job(job)
    with db_factory() as db: calls=[tool_view(row) for row in db.scalars(select(OperationRow).where(OperationRow.session_id==sid))]
    folder=ROOT/'examples/backend-boundaries/mcp';folder.mkdir(parents=True,exist_ok=True)
    (folder/'evidence.json').write_text(json.dumps({'actual_mcp':True,'llm_selection_test':False,'session_id':sid,'results':results,'tool_calls':calls},ensure_ascii=False,indent=2),encoding='utf-8')
    for result in results:print(result['case'],'PASS' if result['passed'] else 'FAIL',flush=True)
    assert all(result['passed'] for result in results)


if __name__=='__main__':asyncio.run(main())
