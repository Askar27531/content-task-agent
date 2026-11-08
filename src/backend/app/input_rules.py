"""Ground conversational updates and enforce the product's capability/consent boundary."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from . import domain
from .config import canonical_hash

FIELDS = {"topic", "budget", "deadline", "start_at", "real_sources_required", "prefer_web"}
CAPABILITIES = {
    "video_generation": "当前没有真实视频或成片生成工具，只能提供文字策划与脚本。",
    "image_generation": "当前没有图片文件生成工具，只能给出视觉制作建议。",
    "publishing": "当前没有外部账号登录或发布工具，不能代为发布活动。",
    "payment": "当前没有支付或广告投放工具，不能代付款或投放广告。",
}
VAGUE_TOPICS = {"搞一搞", "搞一下", "活动", "方案", "内容", "差不多", "热闹点", "你看着办", "随便"}


def request_messages(state: dict) -> list[str]:
    result=[];size=0
    for item in reversed(state.get('timeline', [])):
        if item.get('role')!='user' or not item.get('is_requirement_update'):continue
        text=item['content']
        if size+len(text)>6000:break
        result.append(text);size+=len(text)
    return list(reversed(result))


def chinese_number(value: str) -> int:
    digits = dict(zip('零〇一二两三四五六七八九', [0,0,1,2,2,3,4,5,6,7,8,9]))
    if value.isdigit(): return int(value)
    if not any(ch in value for ch in '十百千万'):
        return int(''.join(str(digits[ch]) for ch in value))
    total=section=number=0
    for ch in value:
        if ch in digits: number=digits[ch]
        elif ch=='万': total+=(section+number)*10000;section=number=0
        else: section+=(number or 1)*{'十':10,'百':100,'千':1000}[ch];number=0
    return total+section+number


def chinese_clocks(text: str) -> list[tuple[int,int]]:
    values=[]
    text=re.sub(r'[\s，,]', '', text)
    for match in re.finditer(r'(上午|早上|早晨|下午|晚上|傍晚|中午|凌晨)?([零〇一二两三四五六七八九十\d]{1,3})[点时](?:(半|一刻|三刻|[零〇一二两三四五六七八九十\d]{1,3})分?)?',text):
        period,hour,minute=match.groups();h=chinese_number(hour)
        m={'半':30,'一刻':15,'三刻':45}.get(minute) if minute in {'半','一刻','三刻'} else chinese_number(minute) if minute else 0
        if period in {'下午','晚上','傍晚','中午'} and h<12: h+=12
        if period=='凌晨' and h==12:h=0
        if 0<=h<24 and 0<=m<60: values.append((h,m))
    return values


def evidence_matches(fragment: str, message: str) -> bool:
    def canonical(value):
        return re.sub(r"[\s，,。；;、‘’“”\"']", "", value).casefold()
    return bool(fragment and canonical(fragment) in canonical(message))


def explicit_execution(message: str) -> bool:
    """Fail closed on quotations, conditions, questions and negative imperatives."""
    if re.search(r"[?？]|如果|假如|下一条|下次|还没决定|没决定|还没确认|只(?:讨论|看|聊|预览)|别动|do not|don't|\bif\b", message, re.I):
        return False
    if re.search(r"(?:不要|不必|暂不|先不|先别|还不|别|不)(?:真正|马上|现在)?(?:开始|执行|启动|动手|做|继续)", message):
        return False
    for quote in re.findall(r'[“「\"`][^”」\"`]*[”」\"`]', message):
        if re.search(r"开始|执行|启动|start|execute|run", quote, re.I):
            return False
    if re.search(r"save_proposal|validation_id|estimate_id|(?:跳过|绕过|忽略).{0,8}(?:预算|校验|检查)", message, re.I):
        return False
    return bool(re.search(r"开始|执行|启动|开工|动手|照做|做吧|跑起来|\bstart\b|\bexecute\b|\bgo ahead\b", message, re.I))


def _clock_proven(value: str, evidence: str, message: str) -> bool:
    match = re.search(r"[T ](\d{1,2}):(\d{2})", value)
    if not match:
        return False
    hour, minute = match.groups()
    clock = rf"(?<!\d)0?{int(hour)}\s*[:：]\s*{minute}(?!\d)"
    # Include the exact raw date/time even if the model's evidence quote omitted a separator.
    return bool(re.search(clock, evidence) or
                re.search(rf"{re.escape(value[:10])}[T ]+0?{int(hour)}[:：]{minute}", message) or
                (int(hour),int(minute)) in chinese_clocks(evidence))


def inspect_input(intent: dict, message: str, current: dict | None = None) -> dict:
    patch = {}; issues = {}; evidence = dict(intent.get("evidence", {})); updates = dict(intent.get('updates', {}))
    # Explicit absolute fields outrank an extractor that omits or rewrites them.
    labels = {'start_at':r'开始(?:时间)?|start(?: time)?', 'deadline':r'截止(?:时间)?|最晚|必须|deadline|finish by'}
    for key, label in labels.items():
        match = re.search(rf'(?:{label})\s*[：:]?\s*(\d{{4}}(?:-\d{{1,2}}-\d{{1,2}}|年\d{{1,2}}月\d{{1,2}}日))([^。；;\n]{{0,45}})', message, re.I)
        if match:
            fragment=match.group(0)
            # Stop at the next field: a start clock cannot fill an unknown deadline clock.
            fragment=re.split(r'[,，](?=\s*(?:截止|开始|预算|制作预算))',fragment)[0]
            updates[key]=fragment; evidence[key]=fragment
    amounts=list(re.finditer(r'(?:制作预算|预算|budget)\s*(?:改为|改成|改到|调整为|降到|提高到|现在是|应为)?\s*[：:]?\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|[零〇一二两三四五六七八九十百千万]+)',message,re.I))
    if amounts:
        amount=amounts[-1]
        updates['budget']=amount.group(1); evidence['budget']=amount.group(0)
        # An unqualified correction with multiple amounts is not a single confirmed cap.
        tail=message[amount.end():]
        if re.search(r'不对|错了|改为|改成|更正为',tail) and re.search(r'\d',tail) and not re.search(r'截止|开始|海报|视频|人数',tail):
            updates['budget']=None;issues['budget']='预算有多个更正值，请明确当前单一制作预算上限。'
    if not (current or {}).get('topic') and not updates.get('topic'):
        heading=re.split(r'[，,。；;\n]',message,1)[0].strip()
        candidate=re.sub(r'^(?:请|帮我|请帮我|为|做个|做一份|做个|策划一下)+','',heading)
        candidate=re.sub(r'(?:写|做)?(?:文字内容方案|内容活动方案|活动方案|内容方案|推广方案)$','',candidate).strip()
        if 2<=len(candidate)<=60 and candidate not in VAGUE_TOPICS and not re.search(r'搞一搞|搞一下|不知道|你定|看着办|^只要|^只需要|^提供|^请问|^怎么|^能否|^我不要|^预算|^制作预算|^截止|^开始|^先别|^先不|^不要|^别|^如果|当前计划|\b(?:only|use|start|budget)\b',candidate,re.I):
            updates['topic']=candidate; evidence['topic']=heading
    for key, value in updates.items():
        if key not in FIELDS or not evidence_matches(evidence.get(key, ""), message):
            continue
        if ((current or {}).get(key) is not None and
                re.search(r'不变|沿用原|保持原|维持原|照旧',evidence.get(key,''))):
            continue  # Explicit references retain existing facts; they are not new numbers or clocks.
        if key == "budget" and value is not None:
            try:
                if re.fullmatch(r'[零〇一二两三四五六七八九十百千万]+', str(value)):
                    value = str(chinese_number(str(value)))
                number = Decimal(str(value))
                if not number.is_finite() or number < 0:
                    raise InvalidOperation
                if not re.search(r"[\d零一二两三四五六七八九十百千万]", evidence[key]):
                    raise InvalidOperation
                numbers = re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", evidence[key])
                if not numbers:
                    numbers=[str(chinese_number(item)) for item in re.findall(r'[零〇一二两三四五六七八九十百千万]+',evidence[key])]
                if numbers and not any(Decimal(item) == number for item in numbers):
                    raise InvalidOperation
                clause=re.search(r'(?:预算|budget|经费)([^，,。；;\n]*)',message,re.I)
                ambiguous = clause and re.search(r"或|到|~|左右|大概|可能|未定|没决定|看着办|你定",clause.group(1))
                if ambiguous and not re.search(r"不超过|最多|上限|以.{0,10}为准|确定为|就按", message):
                    raise InvalidOperation
                patch[key] = str(number)
            except (InvalidOperation, ValueError):
                patch[key] = None; issues[key] = "制作预算尚未明确，请提供单一、非负且有限的模拟单位上限。"
        elif key in {"deadline", "start_at"} and value is not None:
            value = str(value)
            absolute = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?", value)
            candidate = absolute.group() if absolute else value
            chinese_date=re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日',evidence[key])
            clocks=chinese_clocks(evidence[key])
            if not absolute and chinese_date and len(clocks)==1:
                y,m,d=map(int,chinese_date.groups());h,minute=clocks[0]
                candidate=f'{y:04d}-{m:02d}-{d:02d}T{h:02d}:{minute:02d}:00+08:00'
            candidate = re.sub(r"([T ])(\d):", r"\g<1>0\2:", candidate)
            try:
                normalized = domain.normalize_requirement_dates({key: candidate})[key]
                parsed = datetime.fromisoformat(normalized)
                if parsed.tzinfo is None or not _clock_proven(candidate, evidence[key], message):
                    raise ValueError
                patch[key] = normalized
            except (ValueError, TypeError):
                patch[key] = None; issues[key] = "请明确活动截止日期及具体时刻，不会自动把‘尽快’或仅有日期补成某个时间。" if key == "deadline" else "请明确制作开始日期和具体时刻，或明确采用默认开始时间。"
        elif key == "topic" and value in VAGUE_TOPICS:
            patch[key] = None; issues[key] = "请说明具体活动主题或推广对象，暂不能把泛泛的‘搞一搞’视为主题。"
        else:
            patch[key] = value
    # Explicit withdrawal must remove previous facts, not silently reuse them.
    for clause in re.split(r'[，,。；;\n]',message):
        if not re.search(r"作废|删掉|删除|撤回|清空|取消原|先别算数",clause) or re.search(r"(?:不要|别|不是|没有).{0,12}(?:作废|删掉|删除|撤回|清空)",clause):
            continue
        for key, pattern in {"budget": r"预算|budget|经费", "deadline": r"截止|期限|deadline", "start_at": r"开始时间"}.items():
            if re.search(pattern, clause, re.I):
                patch[key] = None; issues[key] = {"budget": "原制作预算已撤回，请重新确认预算。", "deadline": "原活动截止时间已撤回，请重新确认截止时间。", "start_at": "原制作开始时间已撤回，请重新确认开始时间。"}[key]
    for key in intent.get("uncertain_fields", []):
        if (current or {}).get(key) is not None and re.search(r'不变|沿用原|保持原|维持原|照旧',evidence.get(key,'')):
            continue
        if key in FIELDS and evidence_matches(evidence.get(key, ""), message) and (key not in patch or patch[key] is None or re.search(r'未定|不确定|作废|撤回|没决定|没想好|还没|可能|大概|左右|范围|灵活|别替我|你定|看着办|或',evidence[key])):
            patch[key] = None; issues.setdefault(key, f"{dict(topic='活动主题', budget='制作预算', deadline='活动截止时间', start_at='制作开始时间', real_sources_required='来源要求')[key]}尚待确认。")
    gaps = []
    if evidence_matches(intent.get("capability_evidence", ""), message):
        gaps = [value for value in intent.get("capability_gaps", []) if value in CAPABILITIES]
    text_only = bool(re.search(r"(?:只要|只需要|仅要|仅需要|仅生成|改为|改成).{0,15}(?:方案|文案|脚本)", message))
    if text_only: gaps=[]
    def positive_action(pattern):
        for match in re.finditer(pattern,message,re.I):
            prefix=re.split(r'[，,。；;！？?!\n]',message[max(0,match.start()-12):match.start()])[-1]
            suffix=message[match.end():match.end()+5]
            if not re.search(r'不要|不用|无需|不需要|禁止|do not',prefix,re.I) and not re.match(r'不需要|不用',suffix):return True
        return False
    if not text_only:
        if positive_action(r"(?:直接|真正|实际|要你|必须).{0,16}(?:生成|制作|产出).{0,14}(?:4K|视频|成片|mp4)"): gaps.append('video_generation')
        if positive_action(r"(?:登录|上传|自动).{0,12}(?:抖音|小红书|发布)"): gaps.append('publishing')
        if positive_action(r"付款|买广告|代支付|付费充值"): gaps.append('payment')
    if re.search(r'(?:只能|只用|仅用|必须).{0,18}(?:真实|真的|可核验|可信).{0,10}(?:资料|素材|来源|出处)|(?:only|exclusively).{0,20}(?:verified|real|trusted).{0,15}(?:sources|materials)|synthetic.{0,25}(?:not acceptable|not allowed)',message,re.I):
        patch['real_sources_required']=True
    if re.search(r'优先.{0,6}(?:联网|真实)|先.{0,4}(?:联网|搜真实)|先通过Tavily',message,re.I):
        patch['prefer_web']=True
    return {"patch": patch, "field_issues": issues, "capability_gaps": list(dict.fromkeys(gaps)),
            "resolve_capabilities": text_only or bool(intent.get('resolve_capabilities') and evidence_matches(intent.get('capability_evidence', ''), message))}


def apply_input(state: dict, analysis: dict) -> list[str]:
    patch = analysis['patch']; old = dict(state['requirements'])
    state['requirements'].update(patch)
    fields = state.setdefault('input_field_issues', {})
    for key in patch:
        fields.pop(key, None)
    fields.update(analysis['field_issues'])
    if analysis['resolve_capabilities']:
        state['capability_gaps'] = []
    if analysis['capability_gaps']:
        state['capability_gaps'] = analysis['capability_gaps']
    if (old != state['requirements'] or analysis['capability_gaps']) and state['plans'] and state['plans'][-1]['status']=='ready':
        state['plans'][-1]['status'] = 'superseded'
    return requirements_issues(state)


def requirements_issues(state: dict) -> list[str]:
    run=state.get('active_run') or {};latest=state.get('plans',[])[-1] if state.get('plans') else {}
    saved_unchanged=(run.get('status')=='completed' and latest.get('status')=='completed' and
                     latest.get('plan_version_id')==run.get('plan_version_id') and run.get('requirements')==state['requirements'] and
                     latest.get('brief_hash',canonical_hash([]))==canonical_hash(request_messages(state)))
    return list(dict.fromkeys(domain.missing_requirements(state['requirements'], require_future=not saved_unchanged) +
                             list(state.get('input_field_issues', {}).values()) +
                             [CAPABILITIES[key] for key in state.get('capability_gaps', []) if key in CAPABILITIES]))
