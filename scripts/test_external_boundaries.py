"""Real configured DeepSeek/Tavily. Small probes + real graph; no search fixtures."""
from __future__ import annotations
import argparse
import asyncio
import copy
import json
import os
import time
from pathlib import Path

import yaml
from sqlalchemy import select
import verify_backend_completion as base
from probe_external_live import safe,errors
from app.config import settings,credential,runtime_snapshot
from app.llm import invoke,stream_reply
from app.store import (create_session,read_state,receive_message,uid,db_factory,OperationRow,ProposalRow,
    JobRow,CostEntryRow,mutate,claim_job,finish_job)
from app.domain import create_plan
from app.gateway import Gateway
from app.search import parse_remote
from contextlib import asynccontextmanager

ROOT=base.ROOT
PROXY=None
BRIEF='为世界读书日读书会写内容活动方案，预算10个模拟单位，开始2030-12-01 08:00，截止2030-12-03 18:00北京时间。先用高规格视觉premium。必须使用真实来源，禁止合成素材。先给计划，不要执行。'


@asynccontextmanager
async def remote_probe(config,key):
    if PROXY:config.mcp['proxy_url']=PROXY
    gateway=Gateway('external-protocol-probe',{}, {'mcp':config.mcp})
    ready=asyncio.get_running_loop().create_future();stop=asyncio.Event()
    owner=asyncio.create_task(gateway._remote_lifetime(key,ready,stop))
    try:yield await ready
    finally:stop.set();await owner


class External(base.Harness):
    def setup(self,name,mode=None):
        self.name=name;self.sid=create_session()['session_id'];self.prefix='test-'+uid()[:8];self.inputs=[];self.before=None
        config=settings().model_dump();deepseek=next(k for k,v in config['models'].items() if v['provider']=='deepseek')
        config['active_model']=deepseek
        if not credential(config['models'][deepseek].get('api_key_env') or 'DEEPSEEK_API_KEY'):raise ValueError('DeepSeek密钥未配置。')
        if not credential(config['mcp'].get('tavily_key_env','TAVILY_API_KEY')):raise ValueError('Tavily密钥未配置。')
        if not config['mcp']['tavily_url'].startswith('https://mcp.tavily.com/'):raise ValueError('本脚本只访问已授权的Tavily官方远程MCP。')
        config['mcp']['business_url']=f'http://127.0.0.1:{self.business_port}/mcp'
        config['mcp'].update(call_timeout_seconds=240,external_timeout_seconds=60)
        if PROXY:config['mcp']['proxy_url']=PROXY
        config['search'].update(max_results=2,min_results=1,cache_ttl_seconds=0)
        config['fault_injection']={'once_tool':None,'pause_after_failure':False}
        self.config=config;self.path=base.OUTPUT/'configs'/(name+'.yaml');self.path.parent.mkdir(exist_ok=True)
        self.configure();folder=base.OUTPUT/name;folder.mkdir(exist_ok=True)
        (folder/'progress.json').write_text(safe({'session_id':self.sid,'queue_prefix':self.prefix}),encoding='utf8')

    def configure(self):self.path.write_text(yaml.safe_dump(self.config,allow_unicode=True,sort_keys=False),encoding='utf8')
    def operations(self):
        with db_factory() as db:return db.scalars(select(OperationRow).where(OperationRow.session_id==self.sid).order_by(OperationRow.started_at)).all()
    def record(self,checks,error=None):
        super().record(checks,error)
        folder=base.OUTPUT/self.name;data=json.loads((folder/'evidence.json').read_text(encoding='utf8'))
        data.update(external_service_fixture=False,real_external_deepseek=any(c['provider']=='deepseek' for c in data['state']['model_calls']),
            real_external_tavily=any(o.server_started_at and o.tool_name=='search_web_materials' for o in self.operations()))
        with db_factory() as db:proposals=[p.body for p in db.scalars(select(ProposalRow).where(ProposalRow.session_id==self.sid))]
        (folder/'evidence.json').write_text(safe(data),encoding='utf8')
        (folder/'saved-proposals.json').write_text(safe(proposals),encoding='utf8')

    def missing(self):
        self.setup('deepseek-vague');self.say('帮读书会做点内容，预算你看着办吧，最近做完就行。先聊聊，不要执行。')
        state=read_state(self.sid)
        self.record({'no_execution':state['active_run'] is None,'budget_not_invented':state['requirements']['budget'] is None,
            'deadline_not_invented':state['requirements']['deadline'] is None,'no_business_calls':not any(o.run_id for o in self.operations()),
            'clarification_present':any('预算' in m.get('content','') and '截止' in m.get('content','') for m in state['timeline'] if m.get('role')=='assistant')})

    def complete(self):
        self.setup('combined-replan-repeat');self.start(BRIEF);self.drain('run');self.before=read_state(self.sid)
        before_ops=len(self.operations());state=self.before
        self.say('按当前计划再开始执行一次。');self.drain('run');state=read_state(self.sid)
        estimates=[o.output['production_cost'] for o in self.operations() if o.tool_name=='estimate_proposal' and o.status=='success']
        web=[o for o in self.operations() if o.tool_name=='search_web_materials']
        self.record({'saved_once':len(state['proposals'])==1,'completed':state['active_run']['status']=='completed',
            'real_web_success':any(o.status in {'success','partial_success'} for o in web),'only_real_sources':bool(state['materials']) and all(m['source_type']=='web' for m in state['materials']),
            'reestimated_12_to_8':'12' in estimates and estimates[-1]=='8','repeat_no_new_business':len(self.operations())==before_ops,
            'same_completed_run':state['active_run']['run_id']==self.before['active_run']['run_id'],
            'provider_not_switched':all(c['provider']=='deepseek' for c in state['model_calls'])})

    def failure_resume(self):
        self.setup('combined-failure-resume');self.config['fault_injection']={'once_tool':'generate_script','pause_after_failure':True};self.configure()
        self.start(BRIEF.replace('先用高规格视觉premium。','使用精简视觉simple。'));self.drain('run');self.before=read_state(self.sid)
        self.say('从失败处继续，保留已经拿到的真实素材。');self.drain('run');state=read_state(self.sid);ops=self.operations()
        self.record({'first_incomplete':self.before['active_run']['status']=='incomplete','real_sources_kept':state['materials']==self.before['materials'],
            'search_once':sum(o.tool_name=='search_web_materials' for o in ops)==1,'failed_attempt_preserved':any(o.tool_name=='generate_script' and o.status=='failed' for o in ops),
            'resumed_same_run':state['active_run']['run_id']==self.before['active_run']['run_id'],'completed':state['active_run']['status']=='completed','saved_once':len(state['proposals'])==1})

    def automatic_failure_replan(self):
        self.setup('audit-auto-failure-replan')
        self.config['fault_injection']={'once_tool':'generate_script','pause_after_failure':False};self.configure()
        self.start(BRIEF);self.drain('run');state=read_state(self.sid);ops=self.operations()
        scripts=[o for o in ops if o.tool_name=='generate_script'];sources=[o for o in ops if o.tool_name=='search_web_materials']
        estimates=[o for o in ops if o.tool_name=='estimate_proposal' and o.status=='success']
        actual_script=next((o.output['script_id'] for o in scripts if o.status=='success'),None)
        self.record({'completed':state['active_run']['status']=='completed','saved_once':len(state['proposals'])==1,
            'single_injected_failure':sum(o.status=='failed' for o in scripts)==1 and any('INJECTED_ONCE' in (o.error or '') for o in scripts),
            'retry_is_new_attempt':len(scripts)==2 and scripts[0].id!=scripts[1].id and scripts[0].status=='failed' and scripts[1].status=='success',
            'no_manual_resume':all('继续' not in i['content'] for i in self.inputs),
            'real_sources_not_repeated':len(sources)==1 and sources[0].status in {'success','partial_success'},
            'actual_12_to_8':len(estimates)==2 and [o.output['production_cost'] for o in estimates]==['12','8'],
            'changed_plan':len(state['plans'])>=2 and state['plans'][0]['status']=='superseded',
            'script_preserved_through_replan':state['active_run']['script_id']==actual_script,
            'source_ids_preserved':bool(sources) and {m['material_id'] for m in state['materials']}=={m['material_id'] for m in sources[0].output['materials']},
            'production_is_8':state['cost_summary']['production']=='8'})

    def search_timeout(self,strict):
        self.setup('tavily-timeout-'+('strict' if strict else 'fallback'))
        self.config['mcp']['external_timeout_seconds']=.001;self.config['limits']['max_tool_attempts']=1;self.configure()
        brief=BRIEF.replace('先用高规格视觉premium。','使用精简视觉simple。')
        if not strict:brief=brief.replace('必须使用真实来源，禁止合成素材。','优先联网，联网失败时请改用合成素材完成，并明确标注来源。')
        self.start(brief);self.drain('run');state=read_state(self.sid);ops=self.operations()
        web=[o for o in ops if o.tool_name=='search_web_materials'];failed=[o for o in web if o.status=='failed']
        checks={'real_search_attempt':bool(web),'bounded_search':len(web)<=1,'timeout_is_failed':bool(failed),
            'no_fake_web_sources':all(m['source_type']!='web' for m in state['materials']),
            'timeout_error_code':any((o.output or {}).get('error_details',{}).get('code')=='TIMEOUT' for o in failed)}
        if strict:checks.update(incomplete=state['active_run']['status']=='incomplete',not_saved=not state['proposals'],no_synthetic=not state['materials'])
        else:checks.update(completed=state['active_run']['status']=='completed',saved_once=len(state['proposals'])==1,
            source_warning=bool(state['active_run'].get('source_warnings')),synthetic_used=any(m['source_type']=='synthetic' for m in state['materials']))
        self.record(checks)

    def low_budget(self):
        self.setup('deepseek-low-budget');self.config['mcp']['tavily_key_env']='EXTERNAL_TEST_UNCONFIGURED_KEY';self.configure()
        brief=BRIEF.replace('预算10','预算5').replace('先用高规格视觉premium。','使用精简视觉simple。').replace('必须使用真实来源，禁止合成素材。','使用合成素材。')
        self.start(brief);self.drain('run');state=read_state(self.sid)
        self.record({'incomplete':state['active_run']['status']=='incomplete','not_saved':not state['proposals'],
            'known_gap':any(o.tool_name=='estimate_proposal' and o.status=='success' and o.output['cost_gap']=='3' for o in self.operations()),
            'gap_in_final_reason':'预算缺口 3' in (state['active_run'].get('reason') or ''),
            'no_pointless_upgrade':all(p['production_spec']=='simple' for p in state['plans']),
            'results_retained':bool(state['materials'] and state['active_run'].get('script_id'))})

    def cache_and_guards(self):
        self.setup('real-tavily-cache-guards');self.config['search']['cache_ttl_seconds']=600;self.configure()
        previous=os.environ.get('CONTENT_STUDIO_CONFIG');os.environ['CONTENT_STUDIO_CONFIG']=str(self.path)
        try:snapshot=runtime_snapshot()
        finally:
            if previous is None:os.environ.pop('CONTENT_STUDIO_CONFIG',None)
            else:os.environ['CONTENT_STUDIO_CONFIG']=previous
        run_id=uid();brief={'topic':'真实Tavily网关协议测试','budget':'10','deadline':'2030-12-03T18:00:00+08:00','start_at':None,'real_sources_required':True}
        queue=self.prefix+':probe'
        def setup(state,db):
            plan=create_plan(brief,1,'simple','真实网关测试，非完整活动执行');state.update(plans=[plan],requirements=brief)
            state['active_run']={'run_id':run_id,'status':'running','phase':'research','plan_version_id':plan['plan_version_id'],
                'snapshot':snapshot,'requirements':brief,'loaded_skills':[],'control_status':None,'business_calls':0,'context_calls':0}
            db.add(JobRow(session_id=self.sid,kind=queue,status='queued',payload={}))
        mutate(self.sid,setup);job=claim_job(queue,'external-cache-probe');captured=[]
        async def exercise():
            async with Gateway(self.sid,job,snapshot,run_id) as gateway:
                for arguments in [{'query':''},{'query':'x'*2001},{'query':'query','execution':{}},{'query':'query','max_results':999}]:
                    try:await gateway.call('search_web_materials',arguments,{'search_web_materials'});captured.append(False)
                    except ValueError:captured.append(True)
                first=await gateway.call('search_web_materials',{'query':'site:unesco.org World Book and Copyright Day official'}, {'search_web_materials'})
                second=await gateway.call('search_web_materials',{'query':'site:unesco.org World Book and Copyright Day official'}, {'search_web_materials'})
                return first,second
        first,second=asyncio.run(exercise());finish_job(job)
        mutate(self.sid,lambda state,_:state['active_run'].update(status='incomplete',reason='真实网关协议测试结束，不代表完整活动保存。'))
        with db_factory() as db:external=db.scalars(select(CostEntryRow).where(CostEntryRow.session_id==self.sid,CostEntryRow.kind=='external_search')).all()
        self.record({'bad_arguments_rejected_before_dispatch':all(captured),'real_result':first['status'] in {'success','partial_success'},
            'cached_second':second['data'].get('reused') is True,'one_actual_external_charge':len(external)==1,
            'only_two_operations':len(self.operations())==2,'cached_not_recharged':read_state(self.sid)['cost_summary']['tool_simulated']=='0.10'})


async def provider_probes():
    output=base.OUTPUT/'provider';output.mkdir(exist_ok=True);config=settings();profile=config.models['deepseek'].model_dump()
    sid=create_session()['session_id'];checks={};responses={}
    tools=[{'type':'function','function':{'name':'boundary_echo','description':'连通测试工具，原样回传value','parameters':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}}]
    messages=[{'role':'user','content':'必须调用boundary_echo工具，value为external-boundary-ok，然后根据工具结果回答。'}]
    response=await invoke(sid,None,profile,'real_native_tool_selection',messages,tools=tools);responses['tool_selection']=response
    checks['native_tool_call']=bool(response['tool_calls'])
    if response['tool_calls']:
        call=response['tool_calls'][0];tool_result=call['function']['arguments']['value']
        reply=await invoke(sid,None,profile,'real_native_tool_replay',messages+[response,{'role':'tool','tool_call_id':call['id'],'content':json.dumps({'value':tool_result})}])
        responses['tool_replay']=reply;checks['tool_result_replayed']=tool_result in reply['content']
    deltas=[];text=await stream_reply(sid,profile,[{'role':'user','content':'只回复“实时流式验证成功”，不要添加其他内容。'}],deltas.append)
    checks['real_stream_deltas']=bool(deltas) and '成功' in text
    for name,changed in [('bad_model',{'model':'nonexistent-content-boundary-model'}),('truncated',{'max_tokens':1})]:
        try:
            await invoke(sid,None,{**profile,**changed},'real_'+name,[{'role':'user','content':'请输出完整的一百字读书会活动说明。'}])
            checks[name+'_rejected']=False
        except Exception as exc:
            responses[name]=errors(exc);checks[name+'_rejected']=(any(e.get('http_status')==400 for e in responses[name]) if name=='bad_model' else isinstance(exc,ValueError) and '长度' in str(exc))
    # Deliberately invalid credentials test the real authorization response once.
    os.environ['EXTERNAL_BOUNDARY_INVALID_KEY']='invalid-key-for-authorized-boundary-test'
    try:
        await invoke(sid,None,{**profile,'api_key_env':'EXTERNAL_BOUNDARY_INVALID_KEY'},'real_invalid_auth',[{'role':'user','content':'只回复OK'}])
        checks['invalid_auth_rejected']=False
    except Exception as exc:
        responses['invalid_auth']=errors(exc);checks['invalid_auth_rejected']=any(e.get('http_status')==401 for e in responses['invalid_auth'])
    state=read_state(sid)
    checks['real_usage_recorded']=any(c['usage'].get('input_tokens') is not None for c in state['model_calls'])
    checks['truncation_usage_retained']=any(c['kind']=='real_truncated' and c['usage'].get('output_tokens') is not None for c in state['model_calls'])
    result={'session_id':sid,'real_deepseek':True,'local_echo_tool_explicit':True,'checks':checks,'passed':all(checks.values()),'responses':responses,'model_calls':state['model_calls']}
    (output/'evidence.json').write_text(safe(result),encoding='utf8');print('provider probes',checks,flush=True)
    assert result['passed']


async def tavily_probes():
    import httpx2
    from contextlib import AsyncExitStack
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    config=settings();output=base.OUTPUT/'tavily';output.mkdir(exist_ok=True);checks={};responses={}
    key=credential(config.mcp.get('tavily_key_env','TAVILY_API_KEY'))
    async with remote_probe(config,key) as client:
        tools=await client.list_tools();name=next(t.name for t in tools.tools if t.name in {'tavily_search','tavily-search'})
        for label,args in [('unicode',{'query':'读书会 阅读推广活动 UNESCO 📚','max_results':2,'search_depth':'basic'}),
            ('invalid_domain',{'query':'World Book and Copyright Day','max_results':1,'search_depth':'basic','include_domains':['no-content-boundary-7f13.invalid']}),
            ('empty_filter',{'query':'World Book and Copyright Day','max_results':1,'search_depth':'basic','include_domains':['no-content-boundary-7f13.com']})]:
            started=time.monotonic();response=await asyncio.wait_for(client.call_tool(name,args),timeout=60)
            raw={'structured_content':response.structured_content,'content':[{'type':b.type,'text':getattr(b,'text',None)} for b in response.content],'is_error':response.is_error}
            (output/('raw-'+label+'.json')).write_text(safe(raw),encoding='utf8')
            if response.is_error:
                responses[label]={'arguments':args,'explicit_error':True,'text':'; '.join(getattr(b,'text','') for b in response.content)}
                checks[label]=False
            else:
                try:
                    parsed=parse_remote(response,args['query'],{'search':{'max_results':args['max_results']}})
                    responses[label]={'arguments':args,'result':parsed,'duration_ms':int((time.monotonic()-started)*1000)}
                    checks[label]=bool(parsed['materials']) if label=='unicode' else parsed['empty']
                except Exception as exc:
                    responses[label]={'arguments':args,'errors':errors(exc),'raw':raw}
                    checks[label]=label=='invalid_domain' and 'HTTP 400' in str(exc)
    try:
        async with remote_probe(config,'invalid-key-for-authorized-boundary-test') as client:
            response=await client.call_tool('tavily_search',{'query':'authorization boundary','max_results':1})
            text='; '.join(getattr(b,'text','') for b in response.content)
            checks['invalid_auth_rejected']=bool(response.is_error) and any(word in text.lower() for word in ['unauthorized','invalid api key','invalid_api_key','401','403'])
            responses['invalid_auth']={'is_error':response.is_error,'text':'; '.join(getattr(b,'text','') for b in response.content)[:1000]}
    except Exception as exc:
        responses['invalid_auth']=errors(exc);checks['invalid_auth_rejected']=any(e.get('http_status') in {401,403} for e in responses['invalid_auth'])
    result={'real_tavily':True,'external_service_fixture':False,'checks':checks,'passed':all(checks.values()),'responses':responses}
    (output/'evidence.json').write_text(safe(result),encoding='utf8');print('real Tavily probes',checks,flush=True)
    assert result['passed']


async def truncated_stream_probe():
    config=settings();profile=config.models['deepseek'].model_dump();sid=create_session()['session_id'];deltas=[];error=None
    try:await stream_reply(sid,profile,[{'role':'user','content':'请从1一直输出到10000，每个数字占一行，不要省略、不要简写、不要解释。'}],deltas.append)
    except Exception as exc:error=errors(exc)
    state=read_state(sid);call=state['model_calls'][-1]
    checks={'truncated_not_success':bool(error),'partial_text_kept':bool(call['response'].get('content')),
        'provider_usage_retained':call['usage'].get('input_tokens') is not None and call['usage'].get('output_tokens') is not None}
    result={'session_id':sid,'real_deepseek':True,'checks':checks,'passed':all(checks.values()),'error':error,'model_calls':state['model_calls']}
    (base.OUTPUT/'provider-stream-truncation.json').write_text(safe(result),encoding='utf8');print('real truncated stream',checks,flush=True)
    assert result['passed']


def main():
    global PROXY
    parser=argparse.ArgumentParser();parser.add_argument('--only',nargs='*');parser.add_argument('--proxy');args=parser.parse_args();PROXY=args.proxy
    base.OUTPUT=ROOT/'examples/external-live/after';base.OUTPUT.mkdir(parents=True,exist_ok=True)
    if args.only==['provider']:
        asyncio.run(provider_probes());return
    if args.only==['tavily']:
        asyncio.run(tavily_probes());return
    if args.only==['stream-truncation']:
        asyncio.run(truncated_stream_probe());return
    harness=External()
    cases={'vague':harness.missing,'complete':harness.complete,'failure-resume':harness.failure_resume,
        'timeout-strict':lambda:harness.search_timeout(True),'timeout-fallback':lambda:harness.search_timeout(False),
        'low-budget':harness.low_budget,'cache-guards':harness.cache_and_guards,'auto-failure-replan':harness.automatic_failure_replan}
    try:
        for name,action in cases.items():
            if args.only and name not in args.only:continue
            try:action()
            except Exception as exc:
                print(name,'HARNESS_ERROR',safe(errors(exc)),flush=True);harness.record({},safe(errors(exc)))
        if any(not item['passed'] for item in harness.index):raise SystemExit(1)
    finally:harness.close()


if __name__=='__main__':main()
