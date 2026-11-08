"""Actual local Streamable HTTP, not a successful external Tavily claim."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest
from mcp import Client
from app.search import parse_remote


@pytest.fixture(scope='module')
def fixture_url():
    root=Path(__file__).resolve().parents[2]
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    process=subprocess.Popen([sys.executable,str(root/'tests/fixtures/protocol_mcp.py'),'--port',str(port)],
        cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    url=f'http://127.0.0.1:{port}/mcp'
    try:
        for _ in range(100):
            try:
                if httpx.get(url,timeout=.2,trust_env=False).status_code in {400,405,406}:break
            except httpx.HTTPError:pass
            time.sleep(.1)
        else:raise RuntimeError('协议夹具未启动')
        yield url
    finally:
        process.terminate();process.wait(timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize('query,count,partial',[('normal',1,False),('empty',0,False),('partial',1,True)])
async def test_actual_mcp_results_are_normalized_without_fabricated_sources(fixture_url,query,count,partial):
    async with Client(fixture_url) as client:
        tools=await client.list_tools();assert tools.tools[0].name=='tavily_search'
        response=await client.call_tool('tavily_search',{'query':query,'max_results':4})
        result=parse_remote(response,query,{'search':{'max_results':4}})
        assert len(result['materials'])==count and result['partial']==partial
        assert all('协议测试夹具' in item['title'] for item in result['materials'])


@pytest.mark.asyncio
@pytest.mark.parametrize('query',['failure','malformed'])
async def test_actual_mcp_business_and_schema_errors_are_not_empty_successes(fixture_url,query):
    async with Client(fixture_url) as client:
        response=await client.call_tool('tavily_search',{'query':query})
        with pytest.raises(ValueError):parse_remote(response,query,{})


@pytest.mark.asyncio
async def test_real_mcp_timeout_does_not_return_a_success_payload(fixture_url):
    async with Client(fixture_url) as client:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.call_tool('tavily_search',{'query':'timeout'}),timeout=.05)


@pytest.mark.asyncio
async def test_lazy_remote_client_context_can_outlive_graph_node_tasks(fixture_url):
    from app.gateway import Gateway
    gateway=Gateway('protocol-fixture',{}, {'mcp':{'tavily_url':fixture_url}})
    ready=asyncio.get_running_loop().create_future();stop=asyncio.Event()
    owner=asyncio.create_task(gateway._remote_lifetime('fixture-not-real-key',ready,stop))
    try:
        client=await ready
        async def node_one():return await client.list_tools()
        async def node_two():return await client.call_tool('tavily_search',{'query':'normal'})
        assert (await asyncio.create_task(node_one())).tools
        response=await asyncio.create_task(node_two())
        assert not response.is_error
    finally:
        stop.set();await owner
