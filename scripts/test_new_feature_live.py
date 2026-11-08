"""Adversarial real Qwen runs, own queues/services; labeled local external MCP."""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
import yaml
from sqlalchemy import select
import verify_backend_completion as base
from app.store import read_state, db_factory, OperationRow, ProposalRow

ROOT=base.ROOT


class Boundaries(base.Harness):
    def configure(self):self.path.write_text(yaml.safe_dump(self.config,allow_unicode=True,sort_keys=False),encoding='utf8')
    def operations(self):
        with db_factory() as db:return db.scalars(select(OperationRow).where(OperationRow.session_id==self.sid).order_by(OperationRow.started_at)).all()
    def record(self,checks,error=None):
        super().record(checks,error)
        with db_factory() as db:proposals=[p.body for p in db.scalars(select(ProposalRow).where(ProposalRow.session_id==self.sid).order_by(ProposalRow.version))]
        (base.OUTPUT/self.name/'saved-proposals.json').write_text(json.dumps(proposals,ensure_ascii=False,indent=2),encoding='utf8')

    def source_case(self,mode,strict=False,minimum=1):
        self.setup('strict-minimum' if strict and mode=='normal' else mode,mode);self.config['search']['min_results']=minimum;self.configure()
        policy='必须使用真实素材，禁止合成，资料不齐先重检一次，仍不足就停止。' if strict else '优先联网，失败或不足允许合成。'
        self.start(base.BRIEF.replace('允许合成素材。',policy));self.drain('run')
        state=read_state(self.sid);ops=self.operations();run=state['active_run'];web=[o for o in ops if o.tool_name=='search_web_materials']
        checks={'actual_web_call':bool(web),'bounded_attempts':len(web)<=3,'truthful_terminal':run['status'] in {'completed','incomplete'}}
        if mode in {'mixed-shapes','duplicates','instruction-in-source'}:checks['completed']=run['status']=='completed'
        if mode=='mixed-shapes':checks['partial_keeps_good_row']=any(o.status=='partial_success' and o.output['available_count']==1 for o in web)
        if mode=='duplicates':checks['deduplicated_two_sources']=any(o.output and o.output.get('available_count')==2 for o in web)
        if strict:
            checks.update(insufficient_not_saved=run['status']=='incomplete' and not state['proposals'],
                no_synthetic=all(m['source_type']=='web' for m in state['materials']))
        if mode=='partial-then-failure':
            checks.update(valid_result_retained=bool(state['materials']),later_failure_recorded=any(o.status=='failed' for o in web))
            checks['explicit_single_retry_respected']=len(web)==2
            with db_factory() as db:charges=db.scalars(select(base.CostEntryRow).where(base.CostEntryRow.session_id==self.sid,base.CostEntryRow.kind=='external_search')).all()
            checks['terminal_search_costs_not_pending']=bool(charges) and all(c.status=='unknown' and c.amount is None for c in charges)
        if mode=='instruction-in-source':
            estimates=[o for o in ops if o.tool_name=='estimate_proposal' and o.status=='success']
            validations=[o for o in ops if o.tool_name=='validate_proposal' and o.status=='success']
            saves=[o for o in ops if o.tool_name=='save_proposal' and o.status=='success']
            checks.update(actual_rule_cost=state['cost_summary']['production']=='8',
                checked_before_save=bool(estimates and validations and saves and estimates[-1].started_at<validations[-1].started_at<saves[-1].started_at))
        self.record(checks)

    def changed_copy(self):
        self.setup('changed-copy');self.start();self.drain('run');self.before=read_state(self.sid)
        self.say('主题和预算不变，不要沿用旧文案，换成更口语的写法，标题只用一句话。先给新计划，不要执行。')
        preview=read_state(self.sid)
        self.say('按当前计划开始执行。');self.drain('run');state=read_state(self.sid);ops=self.operations()
        self.record({'preview_waits':preview['active_run']['status']=='completed' and preview['plans'][-1]['status']=='ready',
            'new_run':state['active_run']['run_id']!=self.before['active_run']['run_id'],
            'script_regenerated':state['active_run']['script_id']!=self.before['active_run']['script_id'],
            'two_actual_generations':sum(o.tool_name=='generate_script' for o in ops)==2,'two_saved':len(state['proposals'])==2})

    def stricter_sources(self):
        self.setup('stricter-sources','normal')
        self.config['mcp']['tavily_key_env']='UNCONFIGURED_BOUNDARY_KEY';self.configure()
        self.start();self.drain('run');self.before=read_state(self.sid)
        self.config['mcp']['tavily_key_env']='BACKEND_TEST_TAVILY_KEY';self.configure()
        self.say('这次必须真实来源，禁止合成素材，其他主题预算日期不变，请先给新计划，不要执行。')
        self.say('按当前计划开始执行。');self.drain('run');state=read_state(self.sid)
        self.record({'old_synthetic_preserved':bool(self.before['materials']) and all(m['source_type']=='synthetic' for m in self.before['materials']),
            'new_only_web':bool(state['materials']) and all(m['source_type']=='web' for m in state['materials']),
            'new_saved':len(state['proposals'])==2,'script_not_reused':state['active_run']['script_id']!=self.before['active_run']['script_id']})

    def preview_config_change(self):
        self.setup('preview-config-change');self.say(base.BRIEF);self.before=read_state(self.sid)
        active=self.config['active_model'];self.config['models'][active]['max_tokens']=1750;self.configure()
        self.say('按当前计划开始执行。');intermediate=read_state(self.sid)
        count=len([o for o in self.operations() if o.run_id])
        self.say('按当前最新计划开始执行。');self.drain('run');state=read_state(self.sid)
        self.record({'config_change_requires_new_preview':intermediate['active_run'] is None and len(intermediate['plans'])>=2,
            'no_implicit_business_dispatch':count==0,'new_confirmation_saves':len(state['proposals'])==1,
            'new_parameters':state['active_run']['snapshot']['selected_model']['max_tokens']==1750})

    def change_before_save(self):
        self.setup('change-before-save');self.start();marker=base.OUTPUT/self.name/'pause.json'
        for path in (marker,marker.with_suffix('.continue')):
            if path.exists():path.unlink()
        process=self.worker('run',{'CONTENT_STUDIO_TEST_PAUSE_AFTER_TOOL':'validate_proposal','CONTENT_STUDIO_TEST_MARKER':str(marker)})
        end=time.monotonic()+240
        while not marker.exists() and process.poll() is None and time.monotonic()<end:time.sleep(.1)
        if not marker.exists():raise RuntimeError('没有进入真实保存前窗口')
        self.say('预算改为10个模拟单位，请先给新计划，不要执行。');self.before=read_state(self.sid)
        marker.with_suffix('.continue').write_text('release',encoding='utf8');process.wait(timeout=60)
        self.drain('message');preview=read_state(self.sid)
        self.say('按当前计划开始执行。');self.drain('run');state=read_state(self.sid);ops=self.operations()
        old_id=self.before['active_run']['run_id']
        self.record({'change_recorded_before_save':self.before['active_run']['control_status']=='replace',
            'no_old_save':not preview['proposals'] and not any(o.tool_name=='save_proposal' and o.run_id==old_id for o in ops),
            'new_preview_waits':preview['plans'][-1]['status']=='ready',
            'new_run_saves_once':state['active_run']['run_id']!=old_id and len(state['proposals'])==1,
            'valid_script_reused':sum(o.tool_name=='generate_script' for o in ops)==1,
            'new_estimate_and_check':sum(o.tool_name=='estimate_proposal' for o in ops)==2 and sum(o.tool_name=='validate_proposal' for o in ops)==2})

    def exhausted_resume(self):
        self.setup('exhausted-resume','empty');self.config['limits']['max_tool_attempts']=1;self.configure()
        self.start(base.BRIEF.replace('允许合成素材。','必须真实来源，禁止合成。无结果先重试，次数不足再停止。'))
        self.drain('run');self.before=read_state(self.sid)
        before_models=len(self.before['model_calls']);self.say('那就接着做吧，从失败处继续，别重做已经成功的。')
        queued=read_state(self.sid);self.drain('run');state=read_state(self.sid)
        with db_factory() as db:
            jobs=db.scalars(select(base.JobRow).where(base.JobRow.session_id==self.sid,base.JobRow.kind==self.prefix+':run')).all()
        self.record({'incomplete_preserved':state['active_run']['status']=='incomplete' and not state['proposals'],
            'no_new_run_job':len(jobs)==1,'did_not_queue_exhausted_run':queued['active_run']['status']=='incomplete',
            'only_one_search_attempt':sum(o.tool_name=='search_web_materials' for o in self.operations())==1,
            'resume_did_not_execute_model_graph':not any(c.get('run_id') for c in state['model_calls'][before_models:])})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',default='after');parser.add_argument('--only',nargs='*');args=parser.parse_args()
    if args.stage not in {'before','after'}:raise ValueError('stage must be before/after')
    base.OUTPUT=ROOT/'examples/new-feature-boundaries'/args.stage
    harness=Boundaries()
    cases={'mixed-shapes':lambda:harness.source_case('mixed-shapes'),
        'duplicates':lambda:harness.source_case('duplicates',False,2),
        'strict-minimum':lambda:harness.source_case('normal',True,2),
        'partial-then-failure':lambda:harness.source_case('partial-then-failure',True,2),
        'instruction-in-source':lambda:harness.source_case('instruction-in-source'),
        'changed-copy':harness.changed_copy,'stricter-sources':harness.stricter_sources,
        'preview-config-change':harness.preview_config_change,'change-before-save':harness.change_before_save,
        'exhausted-resume':harness.exhausted_resume,
        'crash-materials':lambda:harness.crash('search_synthetic_materials'),
        'crash-save':lambda:harness.crash('save_proposal'),
        'crash-model':harness.crash_model_intent}
    try:
        for name,action in cases.items():
            if args.only and name not in args.only:continue
            try:action()
            except Exception as exc:
                print(name,'HARNESS_ERROR',str(exc),flush=True);harness.record({},str(exc))
        if any(not item['passed'] for item in harness.index):raise SystemExit(1)
    finally:harness.close()


if __name__=='__main__':main()
