"""One durable ledger row per actual attempt; unknown prices remain null."""
from decimal import Decimal
from sqlalchemy import select


def record_charge(db, *, charge_key, session_id, run_id, kind, amount=None, unit, usage=None, status='known'):
    from .store import CostEntryRow
    row = db.scalar(select(CostEntryRow).where(CostEntryRow.charge_key == charge_key).with_for_update())
    values = {'session_id': session_id, 'run_id': run_id, 'kind': kind,
              'amount': Decimal(str(amount)) if amount is not None else None, 'unit': unit, 'status': status, 'usage': usage or {}}
    if row:
        for key, value in values.items():
            setattr(row, key, value)
    else:
        db.add(CostEntryRow(charge_key=charge_key, **values))


def model_amount(profile, usage):
    rates = [profile.get('input_cost_per_million'), profile.get('output_cost_per_million')]
    tokens = [usage.get('input_tokens'), usage.get('output_tokens')]
    if any(value is None for value in rates+tokens):
        return None
    return sum((Decimal(str(rate))*Decimal(str(count))/Decimal(1000000) for rate, count in zip(rates, tokens)), Decimal(0))


def entry_view(row):
    return {'entry_id': row.id, 'charge_key': row.charge_key, 'run_id': row.run_id, 'kind': row.kind,
            'amount': str(row.amount) if row.amount is not None else None, 'unit': row.unit, 'status': row.status, 'usage': row.usage,
            'created_at': row.created_at.isoformat()}


def backfill():
    from datetime import datetime
    from sqlalchemy.dialects.postgresql import insert
    from .store import CostEntryRow, SessionRow, OperationRow, db_factory, uid
    count=0
    with db_factory.begin() as db:
        for session in db.scalars(select(SessionRow)):
            state=session.state;rows=[]
            for call in state.get('model_calls',[]):
                profile=state.get('runs',{}).get(call.get('run_id'),{}).get('run',{}).get('snapshot',{}).get('selected_model',{})
                amount=model_amount(profile,call['usage'])
                rows.append({'id':uid(),'charge_key':'model:'+call['request_id'],'session_id':session.id,
                    'run_id':call.get('run_id'),'kind':call['kind'],'amount':amount,'unit':profile.get('currency','USD'),
                    'status':'unknown' if amount is None else 'estimated','usage':call['usage'],
                    'created_at':datetime.fromisoformat(call['at'])})
            for op in db.scalars(select(OperationRow).where(OperationRow.session_id==session.id)):
                snapshot=state.get('runs',{}).get(op.run_id,{}).get('run',{}).get('snapshot',{})
                fee=Decimal(snapshot.get('production_rules',{}).get('tool_simulated_fees',{}).get(op.tool_name,'0'))
                if op.status not in {'success','partial_success'} or (op.output or {}).get('reused'):fee=Decimal(0)
                if op.status=='running':continue
                rows.append({'id':uid(),'charge_key':'tool:'+op.id,'session_id':session.id,'run_id':op.run_id,
                    'kind':'tool_simulated','amount':fee,'unit':'模拟单位','status':'known',
                    'usage':{'tool':op.tool_name,'attempt':op.attempt,'legacy_backfill':True},'created_at':op.started_at})
            if rows:
                statement=insert(CostEntryRow).values(rows).on_conflict_do_nothing(index_elements=['charge_key']).returning(CostEntryRow.id)
                count+=len(db.execute(statement).scalars().all())
    return count
