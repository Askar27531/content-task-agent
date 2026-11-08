"""Resume original failed runs via chat, then check real replanning and idempotence."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from decimal import Decimal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'src/backend'))
from test_backend_boundaries import Audit, READY


def main():
    audit=Audit('delivery-fix/recovery')
    for name in ['20-video-script-supported','23-stop-resume']:
        original=json.loads((ROOT/'examples/backend-boundaries/verification'/name/'evidence.json').read_text(encoding='utf8'))
        sid=original['session_id'];before=audit.get(f'/sessions/{sid}')
        if before['active_run']['status'] != 'incomplete':
            raise SystemExit('原失败会话已恢复或已变化；不覆盖首次恢复证据。请用完整场景脚本新建会话复验。')
        # Never rewrite the original evidence. The new directory records the
        # current state before resuming and all genuine new HTTP receipts.
        inputs=[audit.send(sid,'从失败处继续，保留已有素材和脚本，只完成剩余交付。')]
        audit.wait(sid,run=True);after=audit.get(f'/sessions/{sid}')
        inputs.append(audit.send(sid,'按当前计划开始执行。'));audit.wait(sid,run=True)
        duplicate=audit.get(f'/sessions/{sid}')
        from app.store import db_factory, OperationRow
        from sqlalchemy import select
        with db_factory() as db:
            ops=db.scalars(select(OperationRow).where(OperationRow.session_id==sid)).all()
        plan=after['plans'][-1]
        audit.record_control(name,sid,inputs,{
            'original_evidence_incomplete':original['workspace']['active_run']['status']=='incomplete',
            'same_run':before['active_run']['run_id']==after['active_run']['run_id'],
            'completed':after['active_run']['status']=='completed',
            'script_id_preserved':before['active_run']['script_id']==after['active_run']['script_id'],
            'estimate_id_preserved':before['active_run']['estimate_id']==after['active_run']['estimate_id'],
            'materials_preserved':before['materials']==after['materials'],
            'material_once':sum(op.tool_name=='search_synthetic_materials' for op in ops)==1,
            'script_once':sum(op.tool_name=='generate_script' for op in ops)==1,
            'all_steps_success':all(s['status']=='success' and not s.get('blocked_reason') for s in plan['steps']),
            'saved_once':len(after['proposals'])==len(duplicate['proposals'])==1,
            'repeat_execute_same_run':after['active_run']['run_id']==duplicate['active_run']['run_id'],
            'production_8':Decimal(after['cost_summary']['production'])==8,
        },{'before_resume':before,'original_failure_evidence':str(Path('examples/backend-boundaries/verification')/name/'evidence.json')})
    name='24-replan-delivery'
    audit.scenario(name,[READY.replace('精简视觉simple','高规格premium'),'按当前计划开始执行。'],{'completed':True})
    evidence=json.loads((audit.folder/name/'evidence.json').read_text(encoding='utf8'))
    calls=evidence['tool_calls'];w=evidence['workspace']
    checks={'completed':w['active_run']['status']=='completed',
            'real_12_then_8':[c['output']['production_cost'] for c in calls if c['tool_name']=='estimate_proposal' and c['status']=='success']==['12','8'],
            'same_material_once':sum(c['tool_name']=='search_synthetic_materials' for c in calls)==1,
            'script_once':sum(c['tool_name']=='generate_script' for c in calls)==1,
            'saved_once':len(w['proposals'])==1}
    evidence['checks'].update(checks);evidence['passed']=all(evidence['checks'].values())
    (audit.folder/name/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
    audit.results[-1].update(checks=evidence['checks'],passed=evidence['passed']);audit.save()
    print(name,checks,flush=True)
    if any(not r['passed'] for r in audit.results):raise SystemExit(1)


if __name__=='__main__':main()
