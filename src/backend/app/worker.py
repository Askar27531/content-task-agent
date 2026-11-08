from __future__ import annotations

import asyncio
import logging
import os
import time

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from .config import DATABASE_URL, runtime_snapshot, worker_options
from .gateway import Gateway
from .graphs import ACTIVE, DialogueRunner, ExecutionRunner
from .store import (JobRow, add_message, assert_lease, claim_job, db_factory, finish_job, heartbeat,
                    mutate, read_state, uid)

log=logging.getLogger(__name__)


def failure_reason(exc):
    if isinstance(exc,BaseExceptionGroup):
        return '; '.join(dict.fromkeys(failure_reason(item) for item in exc.exceptions))[:1000]
    return str(exc)[:1000] or type(exc).__name__


def checkpoint_url():
    from urllib.parse import urlencode
    return DATABASE_URL.replace('postgresql+psycopg://','postgresql://')+('&' if '?' in DATABASE_URL else '?')+urlencode({'options':'-csearch_path=studio_checkpoints'})


def record_failure(job, role, error):
    def failed(value,db):
        assert_lease(db,job)
        active=value.get('active_run')
        if role=='run' and active and active['run_id']==job['payload']['run_id'] and active['status']=='completed':
            return  # Cleanup failure cannot negate a committed proposal.
        if role=='run' and active and active['run_id']==job['payload']['run_id'] and active['status']!='completed':
            active['status']='incomplete';active['reason']=error
            active['reason_code']='AGENT_TIMEOUT' if '超时' in error else 'JOB_FAILURE'
            plan=next(p for p in value['plans'] if p['plan_version_id']==active['plan_version_id'])
            plan['status']='incomplete'
            for step in plan['steps']:
                if step['status']=='pending':step.update(status='blocked',blocked_reason=error)
        add_message(value,'本次处理未完成：'+error+'。已取得结果保留，请修正条件或明确续跑。')
    mutate(job['session_id'],failed,'job.failed')


def mark_interrupted_model_requests(job, role):
    if job['lease_epoch']<2:return
    from .ledger import record_charge
    from .store import CostEntryRow
    from sqlalchemy import select
    def update(state,db):
        assert_lease(db,job)
        for call in state['model_calls']:
            target=call.get('run_id')==job['payload'].get('run_id') if role=='run' else call.get('run_id') is None
            if target and call['response'].get('status')=='running':
                call['response']={'error':'WORKER_INTERRUPTED','result_unknown':True}
                entry=db.scalar(select(CostEntryRow).where(CostEntryRow.charge_key=='model:'+call['request_id']))
                record_charge(db,charge_key='model:'+call['request_id'],session_id=job['session_id'],
                    run_id=call.get('run_id'),kind=call['kind'],unit=entry.unit if entry else 'USD',status='unknown',usage=call['usage'])
    mutate(job['session_id'],update,'recovery.reconciled')


async def execute_job(job, role, checkpointer):
    sid=job['session_id'];state=read_state(sid);current=state.get('active_run')
    if role=='run':
        if not current or current['run_id']!=job['payload']['run_id']:
            raise ValueError('后台任务对应的旧运行已经不可用。')
        snapshot=current['snapshot']
    else:
        snapshot=job['payload'].get('snapshot')
        if not snapshot:
            snapshot=current['snapshot'] if current and current['status'] in ACTIVE|{'incomplete','stopped'} else runtime_snapshot()
            with db_factory.begin() as db:
                assert_lease(db,job)
                row=db.get(JobRow,job['job_id']);row.payload={**row.payload,'snapshot':snapshot}
            job['payload']['snapshot']=snapshot
    async with Gateway(sid,job,snapshot,current['run_id'] if role=='run' else None) as gateway:
        runner=ExecutionRunner(job,snapshot,gateway) if role=='run' else DialogueRunner(job,snapshot,gateway)
        graph=runner.compile(checkpointer)
        await graph.ainvoke({'session_id':sid},config={'configurable':{'thread_id':('run:'+current['run_id']) if role=='run' else 'dialogue:'+sid},'recursion_limit':400})


async def process_job(job, role, checkpointer, lease_seconds=60, heartbeat_seconds=10):
    sid=job['session_id'];started=time.monotonic();state=read_state(sid)
    base_elapsed=(state.get('active_run') or {}).get('elapsed_seconds',0)
    lost=asyncio.Event()
    def save_elapsed():
        def update(value,db):
            assert_lease(db,job)
            if (value.get('active_run') or {}).get('run_id')==job['payload'].get('run_id'):
                value['active_run']['elapsed_seconds']=base_elapsed+time.monotonic()-started
        mutate(sid,update,'run.elapsed')
    async def beat():
        while True:
            await asyncio.sleep(min(heartbeat_seconds,lease_seconds/3))
            try:
                if not heartbeat(job,lease_seconds):lost.set();return
                if role=='run':save_elapsed()
            except Exception:
                log.exception('Heartbeat failed; dispatch stopped')
                lost.set();return
    mark_interrupted_model_requests(job,role)
    beat_task=asyncio.create_task(beat());lost_task=asyncio.create_task(lost.wait())
    remaining=(state.get('active_run') or {}).get('snapshot',{}).get('limits',{}).get('run_timeout_seconds',900)-base_elapsed
    timeout=max(.01,remaining) if role=='run' else 900
    execution=asyncio.create_task(asyncio.wait_for(execute_job(job,role,checkpointer),timeout=timeout))
    error=None;owns=True
    try:
        done,_=await asyncio.wait({execution,lost_task},return_when=asyncio.FIRST_COMPLETED)
        if lost_task in done:
            owns=False;execution.cancel()
            await asyncio.gather(execution,return_exceptions=True)
            log.warning('Lease lost: %s; writes fenced',job['job_id'])
            return
        await execution
    except Exception as exc:
        log.exception('Job failed: %s',job['job_id'])
        error='Agent累计执行超时，实际已成功产物保留。' if isinstance(exc,TimeoutError) else failure_reason(exc)
        try:record_failure(job,role,error)
        except Exception:owns=False;log.exception('Failure write fenced')
    finally:
        beat_task.cancel();lost_task.cancel()
        await asyncio.gather(beat_task,lost_task,return_exceptions=True)
        if owns:
            try:
                if role=='run':save_elapsed()
                finish_job(job,error)
            except Exception:log.exception('Final write fenced; worker remains available')


async def work(kind, *, queue=None, once=False):
    if kind not in {'message','run'}:raise ValueError('worker角色必须为message或run。')
    queue=queue or kind;owner=kind+'-'+uid()[:8]
    try:options=worker_options()
    except Exception:
        log.exception('Invalid infrastructure configuration; safe polling defaults preserve old-run recovery')
        options={'lease_seconds':60,'heartbeat_seconds':10,'poll_seconds':.5}
    lease=float(os.getenv('CONTENT_STUDIO_WORKER_LEASE_SECONDS',str(options['lease_seconds'])))
    if lease<1:raise ValueError('任务租约至少为1秒。')
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url()) as saver:
        while True:
            job=claim_job(queue,owner,lease)
            if job:await process_job(job,kind,saver,lease,float(options['heartbeat_seconds']))
            elif once:return
            else:await asyncio.sleep(float(options['poll_seconds']))
            if once:return

