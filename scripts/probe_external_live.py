"""Small real-provider probes. Credentials never enter saved artifacts."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import sys
import argparse

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src/backend'))
import httpx2
from contextlib import AsyncExitStack
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from app.config import settings,credential
from app.llm import invoke,stream_reply
from app.search import parse_remote
from app.store import create_session,read_state

OUTPUT=ROOT/'examples/external-live/probe'


def errors(exc,depth=0):
    if depth>=8:return []
    if isinstance(exc,BaseExceptionGroup):return [detail for item in exc.exceptions for detail in errors(item)]
    detail={'type':type(exc).__name__,'message':str(exc)[:1000] or repr(exc)[:500]}
    if getattr(exc,'response',None) is not None:detail['http_status']=exc.response.status_code
    return [detail]+(errors(exc.__cause__,depth+1) if exc.__cause__ else [])


def safe(value):
    text=json.dumps(value,ensure_ascii=False,indent=2,default=str)
    for name in ['DEEPSEEK_API_KEY','TAVILY_API_KEY']:
        secret=credential(name)
        if secret:text=text.replace(secret,'[REDACTED]')
    return text


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--only',choices=['deepseek','tavily']);args=parser.parse_args()
    OUTPUT.mkdir(parents=True,exist_ok=True);config=settings();sid=create_session()['session_id'];results={}
    profile=config.models['deepseek'].model_dump();profile['max_tokens']=96
    try:
        if args.only=='tavily':raise StopAsyncIteration
        reply=await invoke(sid,None,profile,'external_connection_probe',[
            {'role':'system','content':'这是API连通测试，只需简短回答。'},
            {'role':'user','content':'请只回复“连接成功”。'}])
        results['deepseek']={'passed':bool(reply['content']),'configured_model':profile['model'],'response':reply}
    except StopAsyncIteration:pass
    except Exception as exc:results['deepseek']={'passed':False,'configured_model':profile['model'],'errors':errors(exc)}
    try:
        if args.only=='deepseek':raise StopAsyncIteration
        key=credential(config.mcp.get('tavily_key_env','TAVILY_API_KEY'))
        async with AsyncExitStack() as stack:
            http=await stack.enter_async_context(httpx2.AsyncClient(headers={'Authorization':'Bearer '+key},timeout=30,trust_env=False,proxy=config.mcp.get('proxy_url')))
            client=await stack.enter_async_context(Client(streamable_http_client(config.mcp['tavily_url'],http_client=http),mode=config.mcp.get('tavily_protocol_mode','legacy')))
            tools=await client.list_tools();definitions=[{'name':t.name,'input_schema':t.input_schema} for t in tools.tools]
            tool=next(t for t in tools.tools if t.name in {'tavily_search','tavily-search','search'})
            response=await asyncio.wait_for(client.call_tool(tool.name,{'query':'site:unesco.org World Book and Copyright Day','max_results':2}),timeout=50)
            result=parse_remote(response,'site:unesco.org World Book and Copyright Day',{'search':{'max_results':2}})
            results['tavily']={'passed':bool(result['materials']),'endpoint':config.mcp['tavily_url'],'tools':definitions,
                'response':result,'structured_content_type':type(response.structured_content).__name__}
    except StopAsyncIteration:pass
    except Exception as exc:results['tavily']={'passed':False,'errors':errors(exc)}
    result={'session_id':sid,'real_external_services':True,'results':results,'model_calls':read_state(sid)['model_calls']}
    (OUTPUT/('evidence-'+(args.only or 'both')+'.json')).write_text(safe(result),encoding='utf8')
    print(safe({'session_id':sid,'results':{k:{a:b for a,b in v.items() if a in ['passed','configured_model','errors','structured_content_type']} for k,v in results.items()}}))


if __name__=='__main__':asyncio.run(main())
