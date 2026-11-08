"""Actual business MCP; explicit fixture preconditions, no LLM selection claim."""
from __future__ import annotations
import asyncio
import copy
import json
from verify_backend_completion import Harness, OUTPUT
from app.config import runtime_snapshot, canonical_hash
from app.domain import create_plan, estimate
from app.gateway import Gateway
from app.store import create_session, mutate, db_factory, JobRow, OperationRow, claim_job, finish_job, read_state, uid, tool_view
from sqlalchemy import select


async def exercise(harness):
    snapshot=runtime_snapshot();snapshot['mcp']['business_url']=f'http://127.0.0.1:{harness.business_port}/mcp'
    snapshot['tavily_enabled']=False
    skill=next(s for s in snapshot['skills'] if s['skill_id']=='campaign-planning')
    current_version=skill['version'];skill['body']='旧版 Skill 正文：冻结快照协议测试夹具。'
    resource=next(iter(skill['resources']));skill['resources'][resource]='旧版参考：冻结快照协议测试夹具。'
    skill['version']=canonical_hash(skill)
    sid=create_session()['session_id'];run_id=uid();queue='test-'+uid()[:8]
    proposal=json.loads((OUTPUT/'search-normal/saved-proposals.json').read_text(encoding='utf8'))[0]
    brief=proposal['requirements'];payload=copy.deepcopy(proposal['structured'])
    with db_factory.begin() as db:db.add(JobRow(session_id=sid,kind=queue,status='queued',payload={'snapshot':snapshot}))
    job=claim_job(queue,'mcp-guard-fixture')
    def setup(state,_):
        plan=create_plan(brief,1,'simple','MCP拒绝路径测试夹具，前置状态由测试明确设置，非模型执行。')
        for step in plan['steps']:
            if step['key'] in {'materials','script','estimate'}:step['status']='success'
        state.update(requirements=brief,plans=[plan],materials=copy.deepcopy(proposal['materials']),
            active_run={'run_id':run_id,'status':'running','plan_version_id':plan['plan_version_id'],'phase':'delivery',
                'requirements':brief,'snapshot':snapshot,'loaded_skills':[],'control_status':None},
            draft={'draft_id':'guard-draft','payload':payload,'plan_version_id':plan['plan_version_id']})
        result=estimate(brief,'simple',snapshot['production_rules']);result.update(plan_version_id=plan['plan_version_id'],run_id=run_id)
        state['estimates'][result['estimate_id']]=result;state['active_run']['estimate_id']=result['estimate_id']
    mutate(sid,setup,'fixture.setup');checks={};results=[]
    try:
        async with Gateway(sid,job,snapshot,run_id) as gateway:
            loaded=await gateway.call('load_skill',{'skill_id':skill['skill_id']},{'load_skill'});results.append(loaded)
            checks['old_skill_body_preserved']=loaded['status']=='success' and loaded['data']['body']==skill['body'] and loaded['data']['version']!=current_version
            read=await gateway.call('read_skill_resource',{'skill_id':skill['skill_id'],'resource_id':resource},{'read_skill_resource'});results.append(read)
            checks['old_reference_preserved']=read['status']=='success' and read['data']['body']==skill['resources'][resource]
            missing=await gateway.call('read_skill_resource',{'skill_id':skill['skill_id'],'resource_id':'references/unavailable.md'},{'read_skill_resource'});results.append(missing)
            checks['missing_reference_fails']=missing['status']=='failed'
            def incompatible(state,_):
                next(s for s in state['active_run']['snapshot']['skills'] if s['skill_id']=='proposal-delivery')['script_hashes']['scripts/check_required_sections.py']='unavailable-old-code'
            mutate(sid,incompatible)
            check=await gateway.call('validate_proposal',{'draft_id':'guard-draft'},{'validate_proposal'});results.append(check)
            checks['missing_checker_version_blocks']=check['status']=='failed' and '不兼容' in check['error']
            def restore(state,_):
                target=next(s for s in state['active_run']['snapshot']['skills'] if s['skill_id']=='proposal-delivery')
                target.update(copy.deepcopy(next(s for s in snapshot['skills'] if s['skill_id']=='proposal-delivery')))
            mutate(sid,restore)
            good=await gateway.call('validate_proposal',{'draft_id':'guard-draft'},{'validate_proposal'});results.append(good)
            assert good['status']=='success' and good['data']['is_valid']
            valid=good['data']['validation_id'];estimate_id=read_state(sid)['active_run']['estimate_id']
            mutate(sid,lambda state,_:state['draft']['payload'].update(title='修改后的标题使旧检查失效'))
            stale=await gateway.call('save_proposal',{'validation_id':valid,'estimate_id':estimate_id},{'save_proposal'});results.append(stale)
            checks['changed_content_rejects_old_validation']=stale['status']=='failed' and '匹配当前内容' in stale['error']
            def outdated(state,_):
                state['draft']['payload']=copy.deepcopy(payload);state['estimates'][estimate_id]['plan_version_id']='old-plan'
            mutate(sid,outdated)
            stale=await gateway.call('save_proposal',{'validation_id':valid,'estimate_id':estimate_id},{'save_proposal'});results.append(stale)
            checks['old_plan_estimate_rejected']=stale['status']=='failed' and '已过期' in stale['error']
            mutate(sid,lambda state,_:state['active_run'].update(phase='writing'))
            wrong=await gateway.call('save_proposal',{'validation_id':valid,'estimate_id':estimate_id},{'save_proposal'});results.append(wrong)
            checks['server_rechecks_phase']=wrong['status']=='failed' and '阶段不允许' in wrong['error']
        def break_schema(state,_):state['active_run']['snapshot']['tool_catalog']['load_skill']['function']['description']='incompatible-fixture'
        mutate(sid,break_schema)
        error=None
        try:
            async with Gateway(sid,job,snapshot,run_id):pass
        except ValueError as exc:error=str(exc)
        checks['schema_mismatch_blocks_resume']=bool(error and 'schema' in error)
        checks['no_proposal_saved']=not read_state(sid)['proposals']
    finally:
        mutate(sid,lambda state,_:state['active_run'].update(status='incomplete',reason='MCP拒绝路径夹具验证结束，不交付活动方案。'))
        finish_job(job)
    with db_factory() as db:ops=[tool_view(op) for op in db.scalars(select(OperationRow).where(OperationRow.session_id==sid))]
    folder=OUTPUT/'mcp-guards';folder.mkdir(exist_ok=True)
    (folder/'evidence.json').write_text(json.dumps({'session_id':sid,'actual_mcp':True,'llm_selection_test':False,
        'fixture_preconditions':True,'checks':checks,'passed':all(checks.values()),'results':results,'tool_calls':ops,'schema_error':error},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(checks,ensure_ascii=False));assert all(checks.values())


def main():
    harness=Harness()
    try:asyncio.run(exercise(harness))
    finally:harness.close()


if __name__=='__main__':main()
