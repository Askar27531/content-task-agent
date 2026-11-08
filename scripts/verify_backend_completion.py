"""Real Qwen + business MCP + PG workers; external search is a labeled local fixture.

Uses dedicated test queues and separate child services. Never changes app.yaml,
normal worker queues, real provider keys, or existing business sessions.
"""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/backend'))
import httpx
import yaml
from sqlalchemy import select
from app.config import settings
from app.store import (create_session, receive_message, read_state, db_factory, JobRow, OperationRow,
                       CostEntryRow, EventRow, tool_view, event_envelope, uid)
from app.ledger import entry_view

OUTPUT=ROOT/'examples/backend-completion'
BRIEF='为读书会写内容活动方案，制作预算20个模拟单位，开始2030-12-01 08:00，截止2030-12-03 18:00北京时间，精简视觉simple。允许合成素材。先给计划，不要执行。'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def stop(process):
    if process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:process.kill()
    process.wait(timeout=15)


class Harness:
    def __init__(self):
        OUTPUT.mkdir(parents=True,exist_ok=True);self.children=[];self.files=[]
        self.index=json.loads((OUTPUT/'index.json').read_text(encoding='utf8')) if (OUTPUT/'index.json').exists() else []
        self.env={**os.environ,'PYTHONPATH':str(ROOT/'src/backend'),'PYTHONUTF8':'1',
                  'BACKEND_TEST_TAVILY_KEY':'fixture-not-a-real-key'}
        self.business_port=free_port();self.api_port=free_port()
        self.launch('business-service',['-m','app.cli','mcp','--port',str(self.business_port)])
        self.launch('api-service',['-m','app.cli','api','--port',str(self.api_port)])
        self.wait_port(self.business_port);self.wait_port(self.api_port)
        self.http=httpx.Client(base_url=f'http://127.0.0.1:{self.api_port}/api',timeout=30,trust_env=False)

    def launch(self,name,args,extra=None):
        log=OUTPUT/'logs';log.mkdir(exist_ok=True)
        stream=(log/(name+'.log')).open('ab');self.files.append(stream)
        process=subprocess.Popen([sys.executable,*args],cwd=ROOT,env={**self.env,**(extra or {})},stdout=stream,stderr=stream,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.children.append(process);return process

    def wait_port(self,port):
        for _ in range(150):
            try:
                httpx.get(f'http://127.0.0.1:{port}/',timeout=.2,trust_env=False);return
            except httpx.HTTPError:time.sleep(.1)
        raise TimeoutError('测试服务没有启动')

    def setup(self,name,mode=None):
        self.name=name;self.sid=create_session()['session_id'];self.prefix='test-'+uid()[:8]
        self.inputs=[];self.before=None
        config=settings().model_dump()
        if config['models'][config['active_model']]['provider']!='ollama':
            raise ValueError('本验收脚本只调用已授权的本地Ollama模型。')
        config['mcp']['business_url']=f'http://127.0.0.1:{self.business_port}/mcp'
        config['mcp']['tavily_key_env']='BACKEND_TEST_TAVILY_KEY'
        config['fault_injection']={'once_tool':None,'pause_after_failure':False}
        if mode:
            port=free_port()
            self.launch(name+'-search-fixture',[str(ROOT/'tests/fixtures/protocol_mcp.py'),'--port',str(port),'--mode',mode])
            self.wait_port(port);config['mcp']['tavily_url']=f'http://127.0.0.1:{port}/mcp'
        else:
            # No web preference in ordinary cases. A key reference is disabled so
            # discovery cannot accidentally reach the public Tavily endpoint.
            config['mcp']['tavily_key_env']='UNCONFIGURED_BACKEND_TEST_KEY'
        config['mcp']['external_timeout_seconds']=.2 if mode=='timeout' else 10
        config['limits']['max_tool_attempts']=3
        self.path=OUTPUT/'configs'/(name+'.yaml');self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False),encoding='utf8')
        self.config=config
        progress=OUTPUT/name;progress.mkdir(exist_ok=True)
        (progress/'progress.json').write_text(json.dumps({'session_id':self.sid,'queue_prefix':self.prefix},indent=2),encoding='utf8')

    def worker(self,role,extra=None):
        return self.launch(self.name+'-'+role,['-m','app.cli','worker',role,'--queue',self.prefix+':'+role,'--once'],
            {'CONTENT_STUDIO_CONFIG':str(self.path),**(extra or {})})

    def drain(self,role):
        for _ in range(10):
            with db_factory() as db:
                pending=db.scalar(select(JobRow.id).where(JobRow.kind==self.prefix+':'+role,JobRow.status.in_(['queued','running'])).limit(1))
            if not pending:return
            process=self.worker(role)
            try:process.wait(timeout=300)
            except subprocess.TimeoutExpired:stop(process);raise TimeoutError('受控worker执行超时')
            with db_factory() as db:
                failed=db.scalar(select(JobRow).where(JobRow.id==pending,JobRow.status=='failed'))
                if failed:raise RuntimeError(failed.error)
        raise RuntimeError('测试任务未在有限轮数内结束')

    def say(self,text):
        state=read_state(self.sid)
        view={'plan_version_id':state['plans'][-1]['plan_version_id']} if state['plans'] else {}
        value=receive_message(self.sid,text,uid(),view,queue_kind=self.prefix+':message')
        self.inputs.append({'content':text,'receipt':value,'queue':self.prefix+':message','view_context':view})
        self.drain('message')

    def start(self,brief=BRIEF):
        self.say(brief);self.say('按当前计划开始执行。')

    def record(self,checks,error=None):
        state=read_state(self.sid)
        with db_factory() as db:
            ops=[tool_view(o) for o in db.scalars(select(OperationRow).where(OperationRow.session_id==self.sid).order_by(OperationRow.started_at))]
            costs=[entry_view(o) for o in db.scalars(select(CostEntryRow).where(CostEntryRow.session_id==self.sid).order_by(CostEntryRow.created_at))]
            events=[event_envelope(o) for o in db.scalars(select(EventRow).where(EventRow.session_id==self.sid).order_by(EventRow.seq))]
            jobs=[{'id':j.id,'kind':j.kind,'status':j.status,'lease_epoch':j.lease_epoch,'error':j.error}
                  for j in db.scalars(select(JobRow).where(JobRow.session_id==self.sid))]
        checks['no_harness_error']=error is None
        result={'name':self.name,'session_id':self.sid,'checks':checks,'passed':all(checks.values())}
        folder=OUTPUT/self.name;folder.mkdir(exist_ok=True)
        workspace=self.http.get(f'/sessions/{self.sid}').json()
        data={'actual_model':bool(state['model_calls']),'actual_business_mcp':True,'external_service_fixture':True,'real_external_tavily':False,
              **result,'inputs':self.inputs,'before':self.before,'workspace':workspace,'state':state,
              'tool_calls':ops,'cost_entries':costs,'events':events,'jobs':jobs,'harness_error':error}
        (folder/'evidence.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
        self.index=[item for item in self.index if item['name']!=self.name]+[result]
        (OUTPUT/'index.json').write_text(json.dumps(self.index,ensure_ascii=False,indent=2),encoding='utf8')
        print(self.name,'PASS' if result['passed'] else 'FAIL',[k for k,v in checks.items() if not v],flush=True)

    def ordinary(self,mode):
        self.setup('search-'+mode,mode)
        must_real=mode=='empty-real'
        if must_real:
            # Fixture mode names are explicit; this flag selects an empty server.
            raise ValueError('use search_case for strict-real')
        self.start(BRIEF.replace('允许合成素材。','优先联网检索，失败允许改用合成素材。'))
        self.drain('run');state=read_state(self.sid);run=state['active_run']
        with db_factory() as db:ops=db.scalars(select(OperationRow).where(OperationRow.session_id==self.sid)).all()
        web=[o for o in ops if o.tool_name=='search_web_materials']
        checks={'web_called':bool(web),'no_fake_save':run['status']!='completed' or len(state['proposals'])==1}
        if mode=='normal':checks['completed']=run['status']=='completed'
        if mode=='partial':checks['partial_recorded']=any(o.status=='partial_success' for o in web)
        if mode=='empty':checks['empty_recorded']=any((o.output or {}).get('empty') for o in web)
        if mode in {'failure','timeout'}:checks['failed_recorded']=any(o.status=='failed' for o in web)
        checks['truthful_terminal']=run['status'] in {'completed','incomplete'}
        if run['status']=='completed' and mode!='normal':checks['source_warning_retained']=bool(run.get('source_warnings'))
        self.record(checks)

    def strict_empty(self):
        self.setup('strict-real-empty','empty')
        self.start(BRIEF.replace('允许合成素材。','必须真实来源，优先联网，禁止合成素材。'))
        self.drain('run');s=read_state(self.sid)
        self.record({'incomplete':s['active_run']['status']=='incomplete','not_saved':not s['proposals'],
                     'no_synthetic':all(m['source_type']=='web' for m in s['materials'])})

    def reuse(self):
        self.setup('cross-run-reuse');self.start();self.drain('run');before=read_state(self.sid);self.before=before
        self.say('预算改为10个模拟单位，请先给新计划，不要执行。');self.say('按当前计划开始执行。');self.drain('run')
        after=read_state(self.sid)
        with db_factory() as db:ops=db.scalars(select(OperationRow).where(OperationRow.session_id==self.sid)).all()
        self.record({'both_saved':len(after['proposals'])==2,'new_run':before['active_run']['run_id']!=after['active_run']['run_id'],
            'script_reused':before['active_run']['script_id']==after['active_run']['script_id'],
            'materials_once':sum(o.tool_name=='search_synthetic_materials' for o in ops)==1,
            'script_once':sum(o.tool_name=='generate_script' for o in ops)==1,
            'estimate_recalculated':sum(o.tool_name=='estimate_proposal' for o in ops)>=2,
            'new_fee_015':after['cost_summary']['tool_simulated']=='0.15'})

    def crash(self,tool):
        self.setup('crash-after-'+tool);self.start()
        marker=OUTPUT/self.name/'checkpoint-gap.json';marker.parent.mkdir(exist_ok=True)
        if marker.exists():marker.unlink()
        process=self.worker('run',{'CONTENT_STUDIO_WORKER_LEASE_SECONDS':'3',
            'CONTENT_STUDIO_TEST_PAUSE_AFTER_TOOL':tool,'CONTENT_STUDIO_TEST_MARKER':str(marker)})
        end=time.monotonic()+250
        while not marker.exists() and process.poll() is None and time.monotonic()<end:time.sleep(.1)
        if not marker.exists():raise RuntimeError('没有观察到真实工具提交后的崩溃窗口')
        self.before=read_state(self.sid);stop(process);time.sleep(4)
        self.drain('run');s=read_state(self.sid)
        with db_factory() as db:
            ops=db.scalars(select(OperationRow).where(OperationRow.session_id==self.sid)).all()
            jobs=db.scalars(select(JobRow).where(JobRow.session_id==self.sid,JobRow.kind==self.prefix+':run')).all()
        self.record({'completed':s['active_run']['status']=='completed','saved_once':len(s['proposals'])==1,
            'lease_reclaimed':any(j.lease_epoch>=2 for j in jobs),
            'tool_once':sum(o.tool_name==tool for o in ops)==1,
            'materials_once':sum(o.tool_name=='search_synthetic_materials' for o in ops)==1})

    def failure_and_config(self):
        self.setup('failure-resume-config')
        self.config['fault_injection']={'once_tool':'generate_script','pause_after_failure':True}
        self.path.write_text(yaml.safe_dump(self.config,allow_unicode=True,sort_keys=False),encoding='utf8')
        self.start();self.drain('run');self.before=read_state(self.sid)
        old_snapshot=copy.deepcopy(self.before['active_run']['snapshot'])
        self.config['models']['local_new_mode']=copy.deepcopy(self.config['models'][self.config['active_model']])
        self.config['models']['local_new_mode']['max_tokens']=1800
        self.config['active_model']='local_new_mode'
        self.config['fault_injection']={'once_tool':None,'pause_after_failure':False}
        self.path.write_text(yaml.safe_dump(self.config,allow_unicode=True,sort_keys=False),encoding='utf8')
        self.say('从失败处继续，保留已成功结果。');self.drain('run');resumed=read_state(self.sid)
        self.say('预算改为10个模拟单位，请先给新计划，不要执行。');self.say('按当前计划开始执行。');self.drain('run')
        current=read_state(self.sid)
        with db_factory() as db:ops=db.scalars(select(OperationRow).where(OperationRow.session_id==self.sid)).all()
        failed=[op for op in ops if op.tool_name=='generate_script' and op.status=='failed']
        self.record({'first_failed':self.before['active_run']['status']=='incomplete',
            'resumed_saved':resumed['active_run']['status']=='completed',
            'same_run_on_resume':self.before['active_run']['run_id']==resumed['active_run']['run_id'],
            'old_snapshot_unchanged':old_snapshot==resumed['active_run']['snapshot'],
            'old_failed_attempt_preserved':len(failed)==1,
            'new_run_new_mode':current['active_run']['snapshot']['active_model']=='local_new_mode',
            'new_mode_parameters':current['active_run']['snapshot']['selected_model']['max_tokens']==1800,
            'both_saved':len(current['proposals'])==2})

    def cache_protocol(self):
        self.setup('search-cache-protocol','normal')
        import asyncio
        from unittest.mock import patch
        from app.config import runtime_snapshot
        from app.domain import create_plan
        from app.gateway import Gateway
        from app.store import mutate, claim_job, finish_job
        with patch.dict(os.environ,{'CONTENT_STUDIO_CONFIG':str(self.path),'BACKEND_TEST_TAVILY_KEY':'fixture-not-real-key'}):
            snapshot=runtime_snapshot();run_id=uid();queue=self.prefix+':probe'
            brief={'topic':'协议缓存验证（不交付活动）','budget':'20','deadline':'2030-12-03T18:00:00+08:00',
                   'start_at':None,'real_sources_required':True}
            def setup(state,db):
                plan=create_plan(brief,1,'simple','仅验证真实MCP缓存协议，不执行活动')
                state['plans']=[plan];state['requirements']=brief;state['session']['title']=brief['topic']
                state['active_run']={'run_id':run_id,'status':'running','phase':'research','plan_version_id':plan['plan_version_id'],
                    'snapshot':snapshot,'requirements':brief,'loaded_skills':[],'control_status':None,'business_calls':0,'context_calls':0}
                db.add(JobRow(id=uid(),session_id=self.sid,kind=queue,status='queued',payload={}))
            mutate(self.sid,setup);job=claim_job(queue,'cache-probe')
            async def probe():
                async with Gateway(self.sid,job,snapshot,run_id) as gateway:
                    first=await gateway.call('search_web_materials',{'query':'fixed-cache-fixture'}, {'search_web_materials'})
                    second=await gateway.call('search_web_materials',{'query':'fixed-cache-fixture'}, {'search_web_materials'})
                    return first,second
            first,second=asyncio.run(probe())
            def end(state,_):
                state['active_run'].update(status='incomplete',reason='协议缓存验证结束，不代表内容方案完成。')
                state['plans'][0]['status']='stopped'
            mutate(self.sid,end);finish_job(job)
        state=read_state(self.sid)
        with db_factory() as db:
            external=db.scalars(select(CostEntryRow).where(CostEntryRow.session_id==self.sid,CostEntryRow.kind=='external_search')).all()
        self.record({'first_actual_result':first['status']=='success','second_cached':second['data']['reused'] is True,
            'external_once':len(external)==1,'cache_not_recharged':state['cost_summary']['tool_simulated']=='0.10'})

    def delivery_repair(self):
        self.setup('delivery-invalid-repair')
        self.config['fault_injection']['invalid_draft_once']=True
        self.path.write_text(yaml.safe_dump(self.config,allow_unicode=True,sort_keys=False),encoding='utf8')
        self.start();self.drain('run');s=read_state(self.sid)
        with db_factory() as db:ops=db.scalars(select(OperationRow).where(OperationRow.session_id==self.sid).order_by(OperationRow.started_at)).all()
        validations=[op for op in ops if op.tool_name=='validate_proposal']
        self.record({'completed':s['active_run']['status']=='completed','saved_once':len(s['proposals'])==1,
            'check_call_success_but_invalid':bool(validations and validations[0].status=='success' and not validations[0].output['is_valid']),
            'new_draft_checked':len(validations)>=2 and validations[-1].output['is_valid'] and validations[0].output['draft_id']!=validations[-1].output['draft_id'],
            'no_repeat_script':sum(op.tool_name=='generate_script' for op in ops)==1})

    def crash_model_intent(self):
        self.setup('crash-pending-model');self.start();process=self.worker('run',{'CONTENT_STUDIO_WORKER_LEASE_SECONDS':'3'})
        end=time.monotonic()+150;pending=None
        while time.monotonic()<end and process.poll() is None:
            s=read_state(self.sid);pending=next((c for c in reversed(s['model_calls']) if c.get('run_id')==s['active_run']['run_id'] and c['response'].get('status')=='running'),None)
            if pending:break
            time.sleep(.02)
        if not pending:raise RuntimeError('未观察到模型请求前持久化意图')
        self.before=read_state(self.sid);stop(process);time.sleep(4);self.drain('run');s=read_state(self.sid)
        call=next(c for c in s['model_calls'] if c['request_id']==pending['request_id'])
        self.record({'completed':s['active_run']['status']=='completed','saved_once':len(s['proposals'])==1,
            'unknown_request_preserved':call['response'].get('result_unknown') is True,
            'unknown_usage_not_zero':call['usage']['input_tokens'] is None and call['usage']['output_tokens'] is None})

    def stop_before_save(self):
        self.setup('stop-before-save');self.start()
        marker=OUTPUT/self.name/'pause.json';marker.parent.mkdir(exist_ok=True)
        for path in (marker,marker.with_suffix('.continue')):
            if path.exists():path.unlink()
        process=self.worker('run',{'CONTENT_STUDIO_TEST_PAUSE_AFTER_TOOL':'validate_proposal','CONTENT_STUDIO_TEST_MARKER':str(marker)})
        end=time.monotonic()+250
        while not marker.exists() and process.poll() is None and time.monotonic()<end:time.sleep(.1)
        if not marker.exists():raise RuntimeError('未进入保存前的真实检查提交窗口')
        self.say('停止当前任务。')
        self.before=read_state(self.sid)
        marker.with_suffix('.continue').write_text('release',encoding='utf8');process.wait(timeout=60)
        stopped=read_state(self.sid)
        self.say('从停止处继续，保留已完成结果。');self.drain('run');s=read_state(self.sid)
        with db_factory() as db:ops=db.scalars(select(OperationRow).where(OperationRow.session_id==self.sid)).all()
        self.record({'stop_received_before_save':self.before['active_run']['status']=='stopping',
            'stopped_without_save':stopped['active_run']['status']=='stopped' and not stopped['proposals'],
            'resumed_same_run':s['active_run']['run_id']==stopped['active_run']['run_id'],
            'completed':s['active_run']['status']=='completed','saved_once':len(s['proposals'])==1,
            'check_once':sum(op.tool_name=='validate_proposal' for op in ops)==1})

    def close(self):
        for child in reversed(self.children):stop(child)
        for stream in self.files:stream.close()
        self.http.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--only',nargs='*');args=parser.parse_args()
    harness=Harness()
    cases={**{'search-'+mode:(lambda m=mode:harness.ordinary(m)) for mode in ['normal','empty','partial','failure','timeout']},
           'strict-real-empty':harness.strict_empty,'cross-run-reuse':harness.reuse,
           'failure-resume-config':harness.failure_and_config,
           'search-cache-protocol':harness.cache_protocol,
           'delivery-invalid-repair':harness.delivery_repair,
           'crash-pending-model':harness.crash_model_intent,
           'stop-before-save':harness.stop_before_save,
           'crash-materials':lambda:harness.crash('search_synthetic_materials'),
           'crash-save':lambda:harness.crash('save_proposal')}
    try:
        for name,action in cases.items():
            if args.only and name not in args.only:continue
            try:action()
            except Exception as exc:
                print(name,'HARNESS_ERROR',str(exc),flush=True)
                harness.record({},str(exc))
        if any(not item['passed'] for item in harness.index):raise SystemExit(1)
    finally:harness.close()


if __name__=='__main__':main()
