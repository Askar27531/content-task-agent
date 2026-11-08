from __future__ import annotations

import copy
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, create_engine, select, exists
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session as DbSession, mapped_column, sessionmaker

from .config import DATABASE_URL, canonical_hash


def uid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return utcnow().isoformat()


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "studio_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[dict] = mapped_column(JSONB)


class EventRow(Base):
    __tablename__ = "studio_events"
    session_id: Mapped[str] = mapped_column(ForeignKey("studio_sessions.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRow(Base):
    __tablename__ = "studio_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("studio_sessions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class OperationRow(Base):
    __tablename__ = "studio_operations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("studio_sessions.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    step_id: Mapped[str] = mapped_column(String(64))
    operation_key: Mapped[str] = mapped_column(String(180), unique=True)
    tool_name: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(120))
    server_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="running")
    public_input: Mapped[dict] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    server_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)


class ProposalRow(Base):
    __tablename__ = "studio_proposals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("studio_sessions.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("session_id", "version"),)


class CostEntryRow(Base):
    __tablename__ = 'studio_cost_entries'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    charge_key: Mapped[str] = mapped_column(String(160), unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('studio_sessions.id'), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    unit: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchCacheRow(Base):
    __tablename__ = 'studio_search_cache'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    result: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=6, max_overflow=8)
db_factory = sessionmaker(engine, expire_on_commit=False)


def add_message(state: dict, content: str, role: str = "assistant", **extra) -> str:
    item_id = uid()
    state["timeline"].append({"item_id": item_id, "item_seq": len(state["timeline"]) + 1,
                              "kind": "message", "role": role, "content": content, "created_at": stamp(), **extra})
    return item_id


def enqueue(db: DbSession, session_id: str, kind: str, payload: dict) -> str:
    job_id = uid()
    db.add(JobRow(id=job_id, session_id=session_id, kind=kind, payload=payload, status="queued"))
    return job_id


def event_envelope(row: EventRow) -> dict:
    return {"event_id": f"{row.session_id}:{row.seq}", "session_seq": row.seq, "session_id": row.session_id,
            "type": row.type, "occurred_at": row.created_at.isoformat(), **row.payload}


def mutate(session_id: str, fn: Callable, event_type: str = "workspace.updated", payload: dict | None = None):
    with db_factory.begin() as db:
        row = db.scalar(select(SessionRow).where(SessionRow.id == session_id).with_for_update())
        if row is None:
            raise KeyError("会话不存在。")
        state = copy.deepcopy(row.state)
        result = fn(state, db)
        active = state.get("active_run")
        if active:
            state.setdefault("runs", {})[active["run_id"]] = {
                "run": copy.deepcopy(active), "cost_summary": copy.deepcopy(state["cost_summary"]),
                "materials": copy.deepcopy(state["materials"])}
        row.revision += 1
        row.state = state
        row.title = state["session"]["title"]
        row.updated_at = datetime.fromisoformat(state["session"]["last_user_activity_at"])
        run = state.get("active_run")
        references={'run_id':run['run_id'] if run else None,'entity_version':row.revision}
        if run:references['plan_version_id']=run['plan_version_id']
        if payload and payload.get('tool_call_id'):
            operation=db.get(OperationRow,payload['tool_call_id'])
            if operation:
                references.update(tool_call_id=operation.id,step_id=operation.step_id,run_id=operation.run_id)
                matched=next((p for p in state['plans'] if any(s['step_id']==operation.step_id for s in p['steps'])),None)
                if matched:references['plan_version_id']=matched['plan_version_id']
        if payload and 'run_id' in payload:references['run_id']=payload['run_id']
        if payload and payload.get('message_id'):references['message_id']=payload['message_id']
        db.add(EventRow(session_id=session_id, seq=row.revision, type=event_type,
                        payload={**references,
                                 "payload": payload or {"changed": True}}, created_at=utcnow()))
        return copy.deepcopy(result)


def read_state(session_id: str) -> dict:
    with db_factory() as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            raise KeyError("会话不存在。")
        state = copy.deepcopy(row.state)
        state["last_event_seq"] = row.revision
        return state


def workspace(session_id: str) -> dict:
    from .input_rules import requirements_issues
    state = read_state(session_id)
    keys = ("session", "requirements", "plans", "active_run", "cost_summary", "materials", "proposals",
            "model_display", "last_event_seq", "pending_changes")
    value = {key: state[key] for key in keys}
    value["mode"] = "live"
    value['requirements_issues'] = requirements_issues(state)
    if value["active_run"]:
        value["active_run"] = {k: v for k, v in value["active_run"].items()
                               if k not in {"snapshot", "model_messages", "draft", "pending_calls"}}
    return value


def create_session() -> dict:
    session = {"session_id": uid(), "title": "新的内容活动", "last_user_activity_at": stamp(), "status": "draft"}
    state = {"session": session, "requirements": {"topic": None, "budget": None, "deadline": None,
             "start_at": None, "real_sources_required": False}, "plans": [], "active_run": None,
             "cost_summary": {"production": None, "budget": None, "tool_simulated": "0.00", "model_input_tokens": None,
             "model_output_tokens": None, "model_unknown_usage_calls": 0, "model_cost": None, "estimated_finish": None, "estimate_stale": False},
             "materials": [], "proposals": [], "model_display": "qwen3:latest · 本地", "pending_changes": None,
             "pending_patch": None, "timeline": [], "client_messages": {}, "scripts": {}, "estimates": {},
             "validations": {}, "skill_loads": [], "model_calls": [], "draft": None, "fault_consumed": [], "runs": {}}
    with db_factory.begin() as db:
        db.add(SessionRow(id=session["session_id"], title=session["title"], updated_at=utcnow(), revision=0, state=state))
    return session


def receive_message(session_id: str, content: str, client_id: str, view_context: dict, *, queue_kind='message') -> dict:
    def update(state, db):
        existing = state["client_messages"].get(client_id)
        if existing:
            if existing["hash"] != canonical_hash(content):
                raise ValueError("同一消息标识不能用于不同内容。")
            return {"message_id": existing["message_id"], "processing_id": existing["job_id"], "duplicate": True}
        message_id = add_message(state, content, "user")
        state["session"]["last_user_activity_at"] = stamp()
        job_id = enqueue(db, session_id, queue_kind, {"message_id": message_id, "content": content, "view_context": view_context})
        state["client_messages"][client_id] = {"hash": canonical_hash(content), "message_id": message_id, "job_id": job_id}
        return {"message_id": message_id, "processing_id": job_id, "duplicate": False}
    return mutate(session_id, update, "message.accepted")


def record_model_call(session_id: str, run_id: str | None, kind: str, profile: dict, response: dict,
                      usage: dict, tool_names: list[str] | None = None, request_id: str | None = None) -> str:
    from .ledger import model_amount, record_charge
    request_id = request_id or uid()
    def update(state, db):
        previous = next((i for i, value in enumerate(state['model_calls']) if value['request_id'] == request_id), None)
        if previous is not None and state['model_calls'][previous]['response'].get('status')!='running' and response.get('status')=='running':
            return  # A replayed intent cannot erase an already received response.
        item = {"request_id": request_id, "run_id": run_id, "kind": kind, "model": profile["model"],
                                     "provider": profile["provider"], "response": response, "usage": usage,
                                     "available_tools": tool_names or [], "at": stamp()}
        if previous is None:
            state['model_calls'].append(item)
        else:
            state['model_calls'][previous] = item
        amount = model_amount(profile, usage)
        record_charge(db, charge_key='model:'+request_id, session_id=session_id, run_id=run_id, kind=kind,
                      amount=amount, unit=profile.get('currency', 'USD'), usage=usage,
                      status='pending' if response.get('status') == 'running' else 'unknown' if amount is None else 'estimated')
        inputs = [call["usage"].get("input_tokens") for call in state["model_calls"]]
        outputs = [call["usage"].get("output_tokens") for call in state["model_calls"]]
        state["cost_summary"]["model_input_tokens"] = sum(v for v in inputs if v is not None) if any(v is not None for v in inputs) else None
        state["cost_summary"]["model_output_tokens"] = sum(v for v in outputs if v is not None) if any(v is not None for v in outputs) else None
        state["cost_summary"]["model_unknown_usage_calls"] = sum(i is None or o is None for i, o in zip(inputs, outputs))
        priced=db.scalars(select(CostEntryRow).where(CostEntryRow.session_id==session_id,CostEntryRow.kind.notin_(['tool_simulated','external_search']))).all()
        known=len(priced)==len(state['model_calls']) and all(row.amount is not None for row in priced)
        units={row.unit for row in priced}
        state['cost_summary']['model_cost']=str(sum((row.amount for row in priced),Decimal(0))) if known and len(units)==1 else None
        state['cost_summary']['model_cost_currency']=next(iter(units)) if known and len(units)==1 else None
    mutate(session_id, update, "model.usage_recorded",{'run_id':run_id,'model_request_id':request_id})
    return request_id


def tool_view(row: OperationRow) -> dict:
    return {"tool_call_id": row.id, "run_id": row.run_id, "step_id": row.step_id, "tool_name": row.tool_name,
            "display_name": row.display_name, "server_name": row.server_name, "status": row.status,
            "input": row.public_input, "output": row.output, "started_at": row.started_at.isoformat(),
            "duration_ms": row.duration_ms, "error": row.error, "attempt": row.attempt}


def claim_job(kind: str, owner: str, lease_seconds: float = 60) -> dict | None:
    with db_factory.begin() as db:
        expired = db.scalars(select(JobRow).where(JobRow.kind == kind, JobRow.status == "running",
                                                  JobRow.lease_until < utcnow()).with_for_update(skip_locked=True)).all()
        for job in expired:
            job.status = "queued"
        active = aliased(JobRow)
        row = db.scalar(select(JobRow).join(SessionRow, SessionRow.id == JobRow.session_id).where(JobRow.kind == kind, JobRow.status == "queued",
                        ~exists(select(active.id).where(active.session_id == JobRow.session_id,
                            active.kind == kind, active.status == 'running', active.lease_until > utcnow())))
                        .order_by(JobRow.created_at).limit(1).with_for_update(skip_locked=True, of=(SessionRow, JobRow)))
        if row is None:
            return None
        # The locking SELECT may have started before another claimant committed.
        # Recheck with a fresh READ COMMITTED snapshot while holding the session
        # lock; the unique index remains the final invariant, not worker control.
        if db.scalar(select(active.id).where(active.session_id==row.session_id,active.kind==kind,
                active.status=='running',active.lease_until>utcnow()).limit(1)):
            return None
        row.status = "running"; row.owner = owner; row.lease_epoch += 1
        row.lease_until = utcnow() + timedelta(seconds=lease_seconds)
        return {"job_id": row.id, "session_id": row.session_id, "kind": row.kind, "payload": row.payload,
                "lease_epoch": row.lease_epoch, "owner": owner}


def heartbeat(job: dict, lease_seconds: float = 60) -> bool:
    with db_factory.begin() as db:
        row = db.get(JobRow, job["job_id"], with_for_update=True)
        if row and row.status == "running" and row.owner == job["owner"] and row.lease_epoch == job["lease_epoch"] and row.lease_until and row.lease_until>utcnow():
            row.lease_until = utcnow() + timedelta(seconds=lease_seconds)
            return True
        return False


def finish_job(job: dict, error: str | None = None) -> None:
    with db_factory.begin() as db:
        row = db.get(JobRow, job["job_id"], with_for_update=True)
        if row and row.status=='running' and row.owner == job["owner"] and row.lease_epoch == job["lease_epoch"] and row.lease_until and row.lease_until>utcnow():
            row.status = "failed" if error else "done"; row.error = error; row.lease_until = None


def assert_lease(db: DbSession, job: dict) -> None:
    row = db.get(JobRow, job["job_id"], with_for_update=True)
    if not row or row.status != "running" or row.owner != job["owner"] or row.lease_epoch != job["lease_epoch"] or row.lease_until <= utcnow():
        raise RuntimeError("任务租约已失效，旧 worker 已停止写入。")
