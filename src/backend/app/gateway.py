from __future__ import annotations

import copy
import json
import os
import asyncio
from contextlib import AsyncExitStack
from decimal import Decimal

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from .config import canonical_hash, credential
from .domain import BUSINESS_TOOLS, CONTEXT_TOOLS, TOOL_LABELS
from .contracts import require_dependencies, tool_error
from .ledger import record_charge
from . import search
from jsonschema import validate as validate_schema, ValidationError
from .mcp_server import ExecutionContext, apply_success
from .store import (OperationRow, assert_lease, db_factory, mutate, read_state, stamp, tool_view, uid, utcnow)


class Gateway:
    """One real MCP connection per job. Browser requests never invoke this layer directly."""

    def __init__(self, session_id: str, job: dict, snapshot: dict, run_id: str | None = None):
        self.session_id = session_id; self.job = job; self.snapshot = snapshot; self.run_id = run_id
        self.stack = AsyncExitStack(); self.local = None; self.remote = None
        self.definitions: dict[str, dict] = {}; self.remote_name: str | None = None
        self.context_reads = 0
        self.remote_stop=None;self.remote_owner=None

    async def __aenter__(self):
        try:
            return await self._open()
        except BaseException:
            await self.stack.aclose()
            raise

    async def _open(self):
        http = await self.stack.enter_async_context(httpx2.AsyncClient(timeout=httpx2.Timeout(20, read=240), trust_env=False))
        transport = streamable_http_client(self.snapshot["mcp"]["business_url"], http_client=http)
        self.local = await self.stack.enter_async_context(Client(transport))
        cursor = None
        while True:
            result = await self.local.list_tools(cursor=cursor)
            for tool in result.tools:
                if tool.name not in BUSINESS_TOOLS | CONTEXT_TOOLS:
                    continue
                schema = copy.deepcopy(tool.input_schema)
                schema.get("properties", {}).pop("execution", None)
                schema["required"] = [key for key in schema.get("required", []) if key != "execution"]
                schema.pop("$defs", None)
                self.definitions[tool.name] = {"type": "function", "function": {"name": tool.name, "description": tool.description or "", "parameters": schema}}
            cursor = result.next_cursor
            if cursor is None:
                break
        if self.snapshot.get('tavily_enabled'):
            self.definitions['search_web_materials'] = copy.deepcopy(search.WEB_DEFINITION)
        if self.run_id:
            def freeze(state, db):
                assert_lease(db,self.job)
                run=state['active_run']; frozen=run['snapshot'].get('tool_catalog')
                local={key:value for key,value in self.definitions.items() if key!='search_web_materials'}
                if frozen and canonical_hash(frozen)!=canonical_hash(local):
                    raise ValueError('MCP工具schema与运行固定版本不兼容，恢复受阻；已有产物保留。')
                if not frozen:
                    run['snapshot']['tool_catalog']=copy.deepcopy(local)
            mutate(self.session_id,freeze,'capabilities.frozen')
        return self

    async def __aexit__(self, *args):
        if self.remote_stop:
            self.remote_stop.set()
            try:await asyncio.wait_for(self.remote_owner,timeout=5)
            except TimeoutError:pass
        await self.stack.__aexit__(*args)

    async def _remote_lifetime(self,key,ready,stop):
        # LangGraph nodes are different asyncio tasks. AnyIO cancellation scopes
        # must be entered and exited by the same task, so remote contexts have a
        # dedicated owner while node tasks only perform RPC calls.
        try:
            attempts=max(1,min(3,int(self.snapshot['mcp'].get('connect_attempts',3))))
            for attempt in range(attempts):
                try:
                    async with AsyncExitStack() as stack:
                        async def check_status(response):
                            if response.status_code in {401,403,429} or response.status_code>=500:
                                response.raise_for_status()
                        http=await stack.enter_async_context(httpx2.AsyncClient(headers={'Authorization':f'Bearer {key}'},
                            timeout=httpx2.Timeout(20,read=45),trust_env=False,proxy=self.snapshot['mcp'].get('proxy_url'),
                            event_hooks={'response':[check_status]}))
                        client=await stack.enter_async_context(Client(streamable_http_client(self.snapshot['mcp']['tavily_url'],http_client=http),
                            mode=self.snapshot['mcp'].get('tavily_protocol_mode','legacy')))
                        ready.set_result(client)
                        await stop.wait()
                    return
                except Exception as exc:
                    def leaves(error):
                        return [leaf for child in error.exceptions for leaf in leaves(child)] if isinstance(error,BaseExceptionGroup) else [error]
                    nested=leaves(exc);retryable=all(isinstance(e,(httpx2.ConnectError,httpx2.ConnectTimeout)) for e in nested)
                    details='; '.join(type(e).__name__+':'+(str(e) or '连接在TLS握手阶段关闭') for e in nested)[:700]
                    if self.job.get('job_id'):
                        mutate(self.session_id,lambda _s,db:assert_lease(db,self.job),'mcp.connection_attempt',
                            {'server':'tavily','attempt':attempt+1,'retryable':retryable,'error':details})
                    if ready.done():raise
                    if not retryable or attempt+1>=attempts:
                        if len(nested)==1 and isinstance(nested[0],httpx2.HTTPStatusError):raise nested[0]
                        if retryable:raise ConnectionError('外部MCP连接失败：'+details) from exc
                        raise ValueError('外部MCP连接失败：'+details) from exc
                    await asyncio.sleep(.2*(attempt+1))
        except BaseException as exc:
            if not ready.done():ready.set_exception(exc)
        finally:
            if not stop.is_set():self.remote=None

    async def connect_tavily(self):
        if self.remote:
            return
        key = credential(self.snapshot["mcp"].get("tavily_key_env", "TAVILY_API_KEY"))
        if not key:
            raise ValueError("Tavily 未配置密钥，不能声称联网检索成功。")
        ready=asyncio.get_running_loop().create_future();self.remote_stop=asyncio.Event()
        self.remote_owner=asyncio.create_task(self._remote_lifetime(key,ready,self.remote_stop))
        self.remote=await ready
        available=[];cursor=None
        while True:
            result=await self.remote.list_tools(cursor=cursor);available.extend(result.tools);cursor=result.next_cursor
            if cursor is None:break
        tool = next((tool for tool in available if tool.name in {"tavily-search", "tavily_search", "search"}), None)
        if not tool:
            raise ValueError("远程 MCP 未提供已允许的 Tavily 搜索工具。")
        self.remote_name = tool.name
        self.remote_schema=copy.deepcopy(tool.input_schema)
        if self.run_id:
            def freeze_remote(state,db):
                assert_lease(db,self.job)
                value={'name':self.remote_name,'schema':self.remote_schema}
                previous=state['active_run']['snapshot'].get('remote_tool_catalog')
                if previous and previous!=value:
                    raise ValueError('外部MCP工具schema与本运行固定版本不兼容。')
                state['active_run']['snapshot']['remote_tool_catalog']=value
            mutate(self.session_id,freeze_remote,'capabilities.frozen')

    async def tools(self, names: set[str]) -> list[dict]:
        return [definition for name, definition in self.definitions.items() if name in names]

    async def call(self, name: str, arguments: dict, allowed: set[str]) -> dict:
        if name not in allowed or name not in self.definitions:
            raise ValueError(f"当前阶段未开放工具 {name}，未实际派发 MCP 调用。")
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须为结构化对象。")
        if "execution" in arguments:
            raise ValueError("执行上下文只能由后端注入。")
        try:
            validate_schema(arguments,self.definitions[name]['function']['parameters'])
        except ValidationError as exc:
            raise ValueError('工具参数未通过schema校验：'+exc.message[:200]) from exc
        if not self.run_id and name not in CONTEXT_TOOLS:
            raise ValueError("尚未收到明确执行指令，不可调用业务工具。")
        self.context_reads += name in CONTEXT_TOOLS
        if self.context_reads > self.snapshot["limits"]["max_context_calls"]:
            raise ValueError("本次上下文读取达到上限。")
        operation_id = uid()
        def begin(state, db):
            assert_lease(db, self.job)
            run = state.get("active_run") if self.run_id else None
            if run:
                if run["run_id"] != self.run_id or run["status"] not in {"running", "queued"} or run.get("control_status"):
                    raise ValueError("运行已停止或待处理控制请求，未派发新工具。")
                key = "context_calls" if name in CONTEXT_TOOLS else "business_calls"
                limit = self.snapshot["limits"]["max_context_calls" if name in CONTEXT_TOOLS else "max_business_calls"]
                if run.get(key, 0) >= limit:
                    raise ValueError("运行工具调用次数已达到上限。")
                run[key] = run.get(key, 0) + 1
            plan = next((item for item in state["plans"] if run and item["plan_version_id"] == run["plan_version_id"]), None)
            target = None
            if plan:
                target = next((item for item in plan["steps"] if item["tool"] == name or (item["key"] == "materials" and name in {"search_synthetic_materials", "search_web_materials"})), None)
                if name in CONTEXT_TOOLS:
                    target = next((item for item in plan["steps"] if item["status"] not in {"success", "partial_success"}), plan["steps"][0])
            step_id = target["step_id"] if target else "context-" + self.job["job_id"][:8]
            attempt = 1 + sum(1 for previous in (target or {}).get("tool_call_ids", [])
                              if (db.get(OperationRow, previous) is not None and db.get(OperationRow, previous).tool_name == name))
            if target:
                if name in BUSINESS_TOOLS:
                    require_dependencies(plan,target)
                    previous=[db.get(OperationRow,key) for key in target['tool_call_ids']]
                    attempts=sum(op is not None and op.run_id==self.run_id and op.tool_name==name for op in previous)
                    if attempts>=self.snapshot['limits'].get('max_tool_attempts',3):
                        raise ValueError('该步骤工具已达到有限尝试上限，成功产物保留。')
                target["tool_call_ids"].append(operation_id)
                if name in BUSINESS_TOOLS:
                    target["status"] = "running"
                    target.pop('blocked_reason', None)
            db.add(OperationRow(id=operation_id, session_id=self.session_id, run_id=self.run_id, step_id=step_id,
                                operation_key=operation_id, tool_name=name, display_name=TOOL_LABELS.get(name, name),
                                server_name="tavily-remote" if name == "search_web_materials" else "content-studio-business",
                                public_input=copy.deepcopy(arguments), status="running", attempt=attempt))
            state["timeline"].append({"item_id": uid(), "item_seq": len(state["timeline"]) + 1, "kind": "tool",
                                      "tool_call_id": operation_id, "created_at": stamp()})
        mutate(self.session_id, begin, "tool_call.started", {"tool_call_id": operation_id})
        execution = ExecutionContext(operation_id=operation_id, session_id=self.session_id, run_id=self.run_id,
                                     job_id=self.job["job_id"], owner=self.job["owner"], lease_epoch=self.job["lease_epoch"])
        transport_error = None
        transport_details = None
        try:
            if name == "search_web_materials":
                data=search.cached(self.snapshot,arguments['query'])
                if data is None:
                    await self.connect_tavily()
                    remote_args={'query':arguments['query'],'max_results':self.snapshot.get('search',{}).get('max_results',4)}
                    validate_schema(remote_args,self.remote_schema)
                    def started(state,db):
                        assert_lease(db,self.job)
                        db.get(OperationRow,operation_id).server_started_at=utcnow()
                        record_charge(db,charge_key='external:'+operation_id,session_id=self.session_id,run_id=self.run_id,
                            kind='external_search',unit='Tavily credits',status='pending',usage={'result_unknown':True})
                    mutate(self.session_id,started,'external_call.started',{'tool_call_id':operation_id})
                    response=await asyncio.wait_for(self.remote.call_tool(self.remote_name,remote_args),
                        timeout=float(self.snapshot['mcp'].get('external_timeout_seconds',180)))
                    data=search.parse_remote(response,arguments['query'],self.snapshot)
                    minimum=self.snapshot.get('search',{}).get('min_results',1)
                    if data['materials'] and len(data['materials'])<minimum:
                        data['partial']=True;data['missing'].append(f'可用素材{len(data["materials"])}项，低于最低{minimum}项。')
                    search.store_cache(self.snapshot,arguments['query'],data)
                minimum=self.snapshot.get('search',{}).get('min_results',1)
                if len(data['materials'])<minimum:
                    data['partial']=bool(data['materials'])
                def remote_commit(state, db):
                    assert_lease(db, self.job)
                    op = db.get(OperationRow, operation_id)
                    if op.status!='running':raise ValueError('已终止的搜索尝试不能被迟到结果覆盖。')
                    output = apply_success(state, db, name, arguments, data, execution)
                    op.status = 'partial_success' if data.get('partial') else 'success'
                    if data.get('reused'):op.server_name='tavily-result-cache'
                    op.output = output; op.duration_ms = int((utcnow() - op.started_at).total_seconds() * 1000)
                    fee=Decimal('0') if data.get('reused') else Decimal(self.snapshot['production_rules']['tool_simulated_fees'].get(name,'0.10'))
                    state["cost_summary"]["tool_simulated"] = str(Decimal(state["cost_summary"]["tool_simulated"]) + fee)
                    record_charge(db,charge_key='tool:'+operation_id,session_id=self.session_id,run_id=self.run_id,
                        kind='tool_simulated',amount=fee,unit='模拟单位',usage={'tool':name,'reused':bool(data.get('reused'))})
                    if not data.get('reused'):
                        record_charge(db,charge_key='external:'+operation_id,session_id=self.session_id,run_id=self.run_id,
                            kind='external_search',unit='Tavily credits',status='unknown',usage={'result_received':True})
                mutate(self.session_id, remote_commit, "tool_call.completed", {"tool_call_id": operation_id})
            else:
                response = await asyncio.wait_for(self.local.call_tool(name, {**arguments, "execution": execution.model_dump()}),
                    timeout=float(self.snapshot['mcp'].get('call_timeout_seconds',240)))
                if response.is_error:
                    transport_error = "; ".join(getattr(block, "text", "") for block in response.content)
        except Exception as exc:
            transport_error = str(exc)[:1000] or '工具调用超时。'
            transport_details=tool_error(exc)
        def finalize(state, db):
            assert_lease(db, self.job)
            op = db.get(OperationRow, operation_id)
            if op.status == "running":
                op.status = "failed"; op.error = transport_error or "工具未返回可确认结果。"
                op.output = {"error": op.error, "result_unknown": bool(op.server_started_at)}
                if transport_details:op.output['error_details']={**transport_details,'result_unknown':bool(op.server_started_at)}
                op.duration_ms = int((utcnow() - op.started_at).total_seconds() * 1000)
                record_charge(db,charge_key='tool:'+operation_id,session_id=self.session_id,run_id=self.run_id,
                              kind='tool_simulated',amount=Decimal(0),unit='模拟单位',usage={'tool':name,'failed':True})
            if self.run_id:
                run = state.get("active_run")
                plan = next((p for p in state["plans"] if run and p["plan_version_id"] == run["plan_version_id"]), None)
                step = next((item for item in (plan or {}).get("steps", []) if operation_id in item["tool_call_ids"]), None)
                if step and name in BUSINESS_TOOLS:
                    step["status"] = op.status if op.status in {'success','partial_success'} else "failed"
                    if name=='search_web_materials' and (op.output or {}).get('empty'):
                        step.update(status='blocked',blocked_reason='检索已完成，但未取得可用素材。')
                    if name=='validate_proposal' and not (op.output or {}).get('is_valid'):
                        step.update(status='blocked',blocked_reason='检查工具已完成，但当前草稿未通过完整性检查。')
                if run and name in BUSINESS_TOOLS:
                    run["last_tool_failed"] = op.status == "failed"
                    run["last_error"] = op.error
                    if name=='search_web_materials' and (op.status in {'failed','partial_success'} or (op.output or {}).get('empty')):
                        run['research_review_pending']=True
                        run['research_last_result']={'status':op.status,'error':op.error,'missing':(op.output or {}).get('missing',[]),'empty':(op.output or {}).get('empty',False)}
            if name=='search_web_materials' and op.status=='failed' and op.server_started_at:
                record_charge(db,charge_key='external:'+operation_id,session_id=self.session_id,run_id=self.run_id,
                    kind='external_search',unit='Tavily credits',status='unknown',usage={'result_unknown':True,'attempt_finished':True})
            return {"status": op.status, "data": op.output, "error": op.error, "tool_call_id": op.id}
        result=mutate(self.session_id, finalize, "step.updated", {"tool_call_id": operation_id})
        # A scoped fault-injection hook for the process recovery harness. HTTP
        # requests cannot choose this queue or marker. Normal workers never pause.
        if self.job.get('kind','').startswith('test-') and os.getenv('CONTENT_STUDIO_TEST_PAUSE_AFTER_TOOL')==name:
            from pathlib import Path
            from .config import ROOT
            marker=Path(os.environ['CONTENT_STUDIO_TEST_MARKER']).resolve()
            if not any(marker.is_relative_to((ROOT/folder).resolve()) for folder in ('examples/backend-completion','examples/new-feature-boundaries')):
                raise ValueError('测试标记只能写入本项目验证目录。')
            marker.parent.mkdir(parents=True,exist_ok=True)
            marker.write_text(json.dumps({'operation_id':operation_id,'tool':name,'status':result['status'],'checkpoint_gap':True}),encoding='utf8')
            while not marker.with_suffix('.continue').exists():
                await asyncio.sleep(.05)
        return result
