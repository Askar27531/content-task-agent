"""Conservative dependency signatures for cross-run reuse in one session."""
from __future__ import annotations
import copy
import re
from datetime import datetime, timezone
from .config import canonical_hash
from .contracts import material_is_fresh


def resource_only_update(text: str, patch: dict) -> bool:
    if not patch or not set(patch)<= {'budget','deadline','start_at'}:
        return False
    rest=re.sub(r'\d{4}-\d{1,2}-\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2})?)?','',text)
    for phrase in ['请先给新计划','只生成新预览','等我确认再执行','不要执行','先给计划','请重新生成计划',
                   '预算','制作','截止时间','截止','开始时间','开始','改为','改成','调整为','降低到','提高到','北京时间','个模拟单位','模拟单位','请','把']:
        rest=rest.replace(phrase,'')
    return not re.sub(r'[\d.零一二两三四五六七八九十百千万\s，,。；;：:]','',rest)


def writing_hash(state):
    texts=[item['content'] for item in state.get('timeline',[]) if item.get('role')=='user' and
           item.get('is_requirement_update') and not item.get('resource_only_update')]
    return canonical_hash(texts)


def script_signature(run, snapshot, materials):
    skill=next(s for s in snapshot['skills'] if s['skill_id']=='script-writing')
    return canonical_hash({'topic':run['requirements']['topic'],'writing_hash':run.get('writing_hash'),
        'model':snapshot['selected_model'],'skill':skill['version'],
        'materials':sorted(materials,key=lambda m:m['material_id'])})


def reusable(state, new_run):
    snapshot=new_run['snapshot'];now=datetime.now(timezone.utc);brief=new_run['requirements']
    for archive in reversed(list(state.get('runs',{}).values())):
        old=archive['run'];old_snapshot=old['snapshot'];materials=archive.get('materials',[])
        if old['requirements']['topic']!=brief['topic'] or not materials:
            continue
        old_plan=next((p for p in state['plans'] if p['plan_version_id']==old['plan_version_id']),None)
        if not old_plan or old_plan['steps'][0]['status'] not in {'success','partial_success'}:
            continue
        if old.get('writing_hash')!=new_run.get('writing_hash'):
            continue
        if old_snapshot.get('mcp',{}).get('tavily_url')!=snapshot.get('mcp',{}).get('tavily_url'):
            continue
        if not old_snapshot.get('material_catalog_hash') or old_snapshot['material_catalog_hash']!=snapshot['material_catalog_hash']:
            continue
        if brief.get('real_sources_required') and any(m['source_type']!='web' for m in materials):
            continue
        if len(materials)<snapshot.get('search',{}).get('min_results',1):
            continue
        ttl=snapshot.get('search',{}).get('material_ttl_seconds',86400)
        if any(m['source_type']=='web' and not material_is_fresh(m,ttl,now) for m in materials):
            continue
        script=state['scripts'].get(old.get('script_id'))
        script_step=next((s for s in old_plan['steps'] if s['key']=='script'),{})
        reusable_script=bool(script and script_step.get('status')=='success' and old.get('script_signature')==script_signature(new_run,snapshot,materials))
        return {'materials':copy.deepcopy(materials),'script':copy.deepcopy(script) if reusable_script else None,
                'source_run_id':old['run_id'],'plan_version_id':old['plan_version_id'],
                'source_warnings':copy.deepcopy(old.get('source_warnings',[]))}
    return None


def apply_reuse(state, run, plan):
    found=reusable(state,run)
    if not found:return None
    old=next(p for p in state['plans'] if p['plan_version_id']==found['plan_version_id'])
    state['materials']=found['materials']
    run['source_warnings']=found['source_warnings']
    for key in ['materials']+(['script'] if found['script'] else []):
        target=next(s for s in plan['steps'] if s['key']==key)
        source=next(s for s in old['steps'] if s['key']==key)
        if source['status'] not in {'success','partial_success'}:continue
        target.update(status=source['status'],tool_call_ids=list(source['tool_call_ids']),reused_from_run=found['source_run_id'])
    if found['script']:
        run['script_id']=found['script']['script_id']
        run['script_signature']=script_signature(run,run['snapshot'],found['materials'])
    return found['source_run_id']
