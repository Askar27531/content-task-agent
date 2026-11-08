from __future__ import annotations

import asyncio
import base64
import json
import binascii

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, text

from .store import (EventRow, OperationRow, ProposalRow, SessionRow, create_session, db_factory,
                    event_envelope, read_state, receive_message, tool_view, workspace, CostEntryRow)
from .ledger import entry_view

app = FastAPI(title="内容工坊 · API", version="0.1.0")


class ViewContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    proposal_id: str | None = Field(default=None, min_length=1, max_length=64)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    client_message_id: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=20000)
    view_context: ViewContext = Field(default_factory=ViewContext)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, exc):
    names = {'content':'消息内容', 'client_message_id':'消息标识', 'plan_version_id':'计划版本编号', 'proposal_id':'方案编号', 'view_context':'查看上下文'}
    details = []
    for error in exc.errors():
        key = str(error['loc'][-1]); name = names.get(key, key)
        kind = error['type']; ctx = error.get('ctx', {})
        message = f'{name}不能为空。' if kind in {'missing','string_too_short'} else f'{name}不能超过{ctx.get("max_length")}个字符。' if kind=='string_too_long' else f'{name}必须为字符串。' if kind=='string_type' else f'{name}不符合接口格式。'
        details.append({'field':key, 'message':message})
    return JSONResponse({'error': {'code':'VALIDATION_ERROR','message':' '.join(d['message'] for d in details), 'details':details}}, status_code=422)


@app.exception_handler(KeyError)
async def not_found(_request, _exc):
    return JSONResponse({"error": {"code": "NOT_FOUND", "message": "请求的会话或记录不存在。"}}, status_code=404)


@app.exception_handler(HTTPException)
async def http_error(_request,exc):
    return JSONResponse({'error':{'code':'HTTP_'+str(exc.status_code),'message':str(exc.detail)}},status_code=exc.status_code)


def page(items: list, cursor: str | None = None, limit: int = 30) -> dict:
    try:
        end = int(base64.urlsafe_b64decode(cursor).decode()) if cursor else len(items)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(422, "无效分页标识。")
    if end < 0 or end > len(items):
        raise HTTPException(422, '分页标识超出当前记录范围。')
    start = max(0, end - limit)
    return {"items": items[start:end], "next_cursor": base64.urlsafe_b64encode(str(start).encode()).decode() if start else None, "has_more": start > 0}


@app.get("/api/health")
def health():
    with db_factory() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "storage": "postgresql", "mode": "live", "model": "configured-per-run"}


@app.post("/api/sessions", status_code=201)
def new_session():
    return create_session()


@app.get("/api/sessions")
def sessions(limit: int = Query(30, ge=1, le=100), cursor: int = Query(0, ge=0)):
    with db_factory() as db:
        rows = db.scalars(select(SessionRow).order_by(SessionRow.updated_at.desc(), SessionRow.id).offset(cursor).limit(limit + 1)).all()
        more = len(rows) > limit
        return {"items": [row.state["session"] for row in rows[:limit]], "next_cursor": str(cursor + limit) if more else None, "has_more": more}


@app.get("/api/sessions/{session_id}")
def session_snapshot(session_id: str):
    return workspace(session_id)


@app.post("/api/sessions/{session_id}/messages")
def send(session_id: str, body: MessageInput):
    if not body.content.strip():
        raise HTTPException(422, "消息不能为空。")
    try:
        result = receive_message(session_id, body.content.strip(), body.client_message_id, body.view_context.model_dump(exclude_none=True))
    except ValueError as exc:
        return JSONResponse({"error": {"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)}}, status_code=409)
    return JSONResponse(result, status_code=200 if result["duplicate"] else 202)


@app.get("/api/sessions/{session_id}/messages")
def messages(session_id: str, cursor: str | None = None, limit: int = Query(30, ge=1, le=100)):
    return page(read_state(session_id)["timeline"], cursor, limit)


@app.get("/api/sessions/{session_id}/plans")
def plans(session_id: str,cursor: str|None=None,limit: int=Query(30,ge=1,le=100)):
    return page(read_state(session_id)['plans'],cursor,limit)


@app.get("/api/plans/{version_id}")
def plan(version_id: str):
    with db_factory() as db:
        for row in db.scalars(select(SessionRow)):
            result = next((value for value in row.state["plans"] if value["plan_version_id"] == version_id), None)
            if result:
                return result
    raise KeyError(version_id)


@app.get("/api/tool-calls/{operation_id}")
def tool(operation_id: str):
    with db_factory() as db:
        row = db.get(OperationRow, operation_id)
        if row is None:
            raise KeyError(operation_id)
        value=tool_view(row)
        state=db.get(SessionRow,row.session_id).state
        value['reused_in_step_ids']=[s['step_id'] for p in state['plans'] for s in p['steps']
            if operation_id in s['tool_call_ids'] and s['step_id']!=row.step_id]
        return value


@app.get("/api/artifacts/{artifact_id}")
def artifact(artifact_id: str):
    with db_factory() as db:
        for row in db.scalars(select(SessionRow)):
            if artifact_id in row.state["scripts"]:
                return row.state["scripts"][artifact_id]
            if (row.state.get("draft") or {}).get("draft_id") == artifact_id:
                return row.state["draft"]
    raise KeyError(artifact_id)


def run_state(run_id: str):
    with db_factory() as db:
        for row in db.scalars(select(SessionRow)):
            archive = row.state.get("runs", {}).get(run_id)
            if archive:
                return {**row.state, "cost_summary": archive["cost_summary"], "materials": archive["materials"]}, archive["run"]
    raise KeyError(run_id)


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    state, value = run_state(run_id)
    snapshot=value['snapshot']
    config={'config_hash':snapshot['config_hash'],'provider':snapshot['selected_model']['provider'],
            'model':snapshot['selected_model']['model'],'skill_versions':{s['skill_id']:s['version'] for s in snapshot['skills']}}
    return {**{key: item for key, item in value.items() if key != "snapshot"}, "cost_summary": state["cost_summary"],'config_summary':config}


@app.get("/api/runs/{run_id}/steps")
def steps(run_id: str):
    state, value = run_state(run_id)
    result = next(plan for plan in state["plans"] if plan["plan_version_id"] == value["plan_version_id"])
    return {"items": result["steps"], "next_cursor": None, "has_more": False}


@app.get("/api/runs/{run_id}/costs")
def costs(run_id: str):
    from sqlalchemy import or_
    from decimal import Decimal
    state, _run = run_state(run_id)
    session_costs=read_state(state['session']['session_id'])['cost_summary']
    model_summary={key:value for key,value in session_costs.items() if key.startswith('model_')}
    with db_factory() as db:
        rows=db.scalars(select(CostEntryRow).where(CostEntryRow.session_id==state['session']['session_id'],
            or_(CostEntryRow.run_id==run_id,CostEntryRow.kind!='tool_simulated')).order_by(CostEntryRow.created_at)).all()
        totals={}
        for unit in {row.unit for row in rows}:
            known=[row.amount for row in rows if row.unit==unit and row.amount is not None]
            totals[unit]=str(sum(known,Decimal(0))) if known else None
        entries=[entry_view(row) for row in rows]
    return {**state["cost_summary"], **model_summary, "model_usage_scope": "session",'tool_usage_scope':'run','external_usage_scope':'session',
        'entries':entries,'known_totals_by_unit':totals,'unknown_entry_count':sum(row['amount'] is None for row in entries),
        'note': "模型用量含本会话澄清、决策及文案调用；模拟工具费按当前运行，制作成本独立；未知价格不按零计。历史明细需运行backfill-ledger迁移。"}


@app.get("/api/runs/{run_id}/materials")
def materials(run_id: str):
    state, _run = run_state(run_id)
    return {"items": state["materials"], "next_cursor": None, "has_more": False}


@app.get("/api/sessions/{session_id}/proposals")
def proposals(session_id: str,cursor: str|None=None,limit: int=Query(30,ge=1,le=100)):
    return page(read_state(session_id)['proposals'],cursor,limit)


@app.get("/api/proposals/{proposal_id}")
def proposal(proposal_id: str):
    with db_factory() as db:
        value = db.get(ProposalRow, proposal_id)
        if not value:
            raise KeyError(proposal_id)
        return value.body


@app.get("/api/proposals/{proposal_id}/export")
def export(proposal_id: str, format: str = Query("markdown", pattern="^(markdown|json)$")):
    value = proposal(proposal_id)
    content = value["markdown"] if format == "markdown" else json.dumps(value, ensure_ascii=False, indent=2)
    return Response(content, media_type="text/markdown;charset=utf-8" if format == "markdown" else "application/json", headers={"Content-Disposition": f'attachment; filename="proposal-v{value["version"]}.{ "md" if format == "markdown" else "json"}"'})


@app.get("/api/sessions/{session_id}/events")
async def events(session_id: str, request: Request, after: int = Query(0, ge=0, le=9223372036854775807)):
    initial = await asyncio.to_thread(read_state, session_id)
    last_header = request.headers.get("last-event-id")
    cursor = after
    if last_header:
        prefix, _, sequence = last_header.rpartition(":")
        if prefix != session_id or not sequence.isascii() or not sequence.isdigit():
            raise HTTPException(409, "事件标识不属于当前会话。")
        if len(sequence)>19 or int(sequence)>9223372036854775807:
            raise HTTPException(422,'事件水位超出有效范围。')
        cursor = int(sequence)
    async def stream():
        nonlocal cursor
        from sqlalchemy import func
        with db_factory() as db:
            oldest=db.scalar(select(func.min(EventRow.seq)).where(EventRow.session_id==session_id))
        if (cursor > initial["last_event_seq"] or (oldest is not None and cursor < oldest-1)
                or (oldest is None and cursor < initial["last_event_seq"])):
            yield "data: " + json.dumps({"type": "resync_required", "session_seq": initial["last_event_seq"], "session_id": session_id, "payload": {}}, ensure_ascii=False) + "\n\n"
            return
        idle = 0
        while not await request.is_disconnected():
            def read():
                with db_factory() as db:
                    revision=db.get(SessionRow,session_id).revision
                    rows=[event_envelope(row) for row in db.scalars(select(EventRow).where(EventRow.session_id == session_id, EventRow.seq > cursor).order_by(EventRow.seq).limit(100))]
                    return rows,revision
            rows,revision = await asyncio.to_thread(read)
            if (rows and rows[0]['session_seq']!=cursor+1) or (not rows and revision>cursor):
                yield 'data: '+json.dumps({'type':'resync_required','session_seq':revision,'session_id':session_id,'payload':{}},ensure_ascii=False)+'\n\n'
                return
            for value in rows:
                if value['session_seq']!=cursor+1:
                    yield 'data: '+json.dumps({'type':'resync_required','session_seq':revision,'session_id':session_id,'payload':{}},ensure_ascii=False)+'\n\n'
                    return
                cursor = value["session_seq"]
                yield f"id: {value['event_id']}\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n"
            idle = 0 if rows else idle + 1
            if idle >= 30:
                yield ": keepalive\n\n"; idle = 0
            await asyncio.sleep(0.35)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
