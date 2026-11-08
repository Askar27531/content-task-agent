"""Shared deterministic tool, dependency and error contracts."""
from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import urlparse
from .config import canonical_hash


def validate_dependencies(steps: list[dict]) -> None:
    by_key = {step['key']: step for step in steps}
    if len(by_key) != len(steps):
        raise ValueError('步骤标识重复。')
    visiting, done = set(), set()
    def visit(key):
        if key not in by_key:
            raise ValueError('存在未知步骤依赖：' + key)
        if key in visiting:
            raise ValueError('步骤依赖存在循环。')
        if key in done:
            return
        visiting.add(key)
        for parent in by_key[key].get('dependencies', []):
            visit(parent)
        visiting.remove(key); done.add(key)
    for key in by_key:
        visit(key)


def require_dependencies(plan: dict, step: dict) -> None:
    validate_dependencies(plan['steps'])
    lookup = {item['key']: item for item in plan['steps']}
    if any(lookup[key]['status'] not in {'success', 'partial_success'} for key in step.get('dependencies', [])):
        raise ValueError('步骤依赖尚未取得有效产物。')


def tool_error(exc: BaseException, *, result_unknown=False) -> dict:
    name = type(exc).__name__.lower(); message = str(exc)[:1000]
    timeout = 'timeout' in name or isinstance(exc, TimeoutError)
    injected=message.startswith('INJECTED_ONCE:')
    code = 'INJECTED_FAILURE' if injected else 'TIMEOUT' if timeout else 'CONFIGURATION_ERROR' if '未配置' in message else 'TOOL_ERROR'
    return {'code': code, 'message': message or '工具调用未完成。',
            'retryable': injected or timeout or 'connect' in name, 'result_unknown': result_unknown}


def queue_for(job: dict, role: str) -> str:
    # Dedicated integration-test queues cannot be selected by HTTP clients.
    kind=job.get('kind','')
    return kind.rsplit(':',1)[0]+':'+role if kind.startswith('test-') and ':' in kind else role


def normalize_search(data: dict, query: str, *, requested: int, now: str | None = None) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get('results'), list):
        raise ValueError('搜索MCP返回格式不符合results列表契约。')
    materials, missing, seen = [], [], set()
    for index, item in enumerate(data['results']):
        if not isinstance(item, dict):
            missing.append(f'第{index+1}项不是素材对象'); continue
        url = item.get('url'); summary = item.get('content') or item.get('snippet'); title=item.get('title')
        try:
            parsed=urlparse(url) if isinstance(url,str) else None
            valid_url=bool(parsed and parsed.scheme in {'http','https'} and parsed.hostname and parsed.port!=0)
        except ValueError:
            valid_url=False
        if (not valid_url or not isinstance(summary, str) or not summary.strip()
                or (title is not None and not isinstance(title,str))):
            missing.append(f'第{index+1}项缺少有效链接或正文'); continue
        if url in seen:
            continue
        seen.add(url)
        materials.append({'material_id': 'web-'+canonical_hash(url)[:10], 'title': (title or '').strip() or url,
                          'summary': summary[:1800], 'url': url, 'source_type': 'web',
                          'retrieved_at': now or datetime.now(timezone.utc).isoformat()})
    return {'materials': materials[:requested], 'source_type': 'web', 'empty': not materials,
            'partial': bool(materials and missing), 'missing': missing, 'query': query,
            'requested_count': requested, 'available_count': min(len(materials), requested)}


def material_is_fresh(material: dict, ttl: float, now: datetime | None = None) -> bool:
    try:
        retrieved=datetime.fromisoformat(material['retrieved_at'])
        age=((now or datetime.now(timezone.utc))-retrieved).total_seconds()
        return retrieved.tzinfo is not None and ttl>0 and 0<=age<ttl
    except (KeyError,ValueError,TypeError):
        return False
