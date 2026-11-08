from __future__ import annotations
import json
from datetime import timedelta
from sqlalchemy.dialects.postgresql import insert
from .config import canonical_hash
from .contracts import normalize_search, material_is_fresh
from .store import SearchCacheRow, db_factory, utcnow


WEB_DEFINITION = {'type':'function','function':{'name':'search_web_materials',
    'description':'通过Tavily远程MCP检索真实文本来源。空结果、部分成功和连接失败会明确返回；不得冒充成功来源。',
    'parameters':{'type':'object','additionalProperties':False,'properties':{'query':{'type':'string','minLength':1,'maxLength':2000}},'required':['query']}}}


def cache_key(snapshot, query):
    return canonical_hash({'endpoint':snapshot['mcp']['tavily_url'], 'query':' '.join(query.split()).casefold(),
                           'max_results':snapshot.get('search',{}).get('max_results',4)})


def cached(snapshot, query):
    policy=snapshot.get('search',{})
    if policy.get('cache_ttl_seconds',3600)<=0:return None
    with db_factory() as db:
        row=db.get(SearchCacheRow,cache_key(snapshot,query))
        if row and row.expires_at > utcnow():
            materials=row.result.get('materials',[])
            if (row.result.get('partial') or len(materials)<policy.get('min_results',1)
                    or not all(material_is_fresh(m,policy.get('material_ttl_seconds',86400)) for m in materials)):
                return None
            return {**row.result,'reused':True}
    return None


def store_cache(snapshot, query, data):
    ttl=snapshot.get('search',{}).get('cache_ttl_seconds',3600)
    if ttl<=0 or not data.get('materials') or data.get('partial'):
        return
    with db_factory.begin() as db:
        statement=insert(SearchCacheRow).values(id=cache_key(snapshot,query),result=data,expires_at=utcnow()+timedelta(seconds=ttl))
        db.execute(statement.on_conflict_do_update(index_elements=['id'],set_={'result':statement.excluded.result,'expires_at':statement.excluded.expires_at}))


def parse_remote(response, query, snapshot):
    if response.is_error:
        raise ValueError('远程MCP业务失败：'+'; '.join(getattr(block,'text','') for block in response.content)[:700])
    data=response.structured_content
    if data is None:
        text=next((getattr(block,'text','') for block in response.content if getattr(block,'text',None)),None)
        if not text:
            raise ValueError('远程MCP没有返回可解析的结果。')
        data=json.loads(text)
    if isinstance(data,dict) and data.get('error'):
        detail=data.get('detail',{});message=detail.get('error') if isinstance(detail,dict) else detail
        raise ValueError('远程MCP业务失败'+(' HTTP '+str(data['status']) if data.get('status') else '')+'：'+str(message or data['error'])[:700])
    return normalize_search(data,query,requested=snapshot.get('search',{}).get('max_results',4))
