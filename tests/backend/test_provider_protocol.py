"""Provider HTTP contract fixtures; no real DeepSeek service is contacted."""
import json
import httpx
import pytest
from app import llm


@pytest.fixture
def calls(monkeypatch):
    result=[]
    def record(sid,rid,kind,profile,response,usage,tool_names=None,request_id=None):
        key=request_id or f'fixture-{len(result)}'
        result.append({'request_id':key,'response':response,'usage':usage})
        return key
    monkeypatch.setattr(llm,'record_model_call',record)
    monkeypatch.setenv('TEST_DEEPSEEK_KEY','protocol-fixture-not-a-real-key')
    return result


def client_with(monkeypatch,handler):
    original=httpx.AsyncClient
    monkeypatch.setattr(llm.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))


@pytest.mark.asyncio
async def test_provider_empty_choices_records_error_and_keeps_usage(monkeypatch,calls):
    client_with(monkeypatch,lambda _:httpx.Response(200,json={'choices':[],'usage':{'prompt_tokens':7,'completion_tokens':2}}))
    with pytest.raises(ValueError):
        await llm.invoke('s','r',{'provider':'deepseek','model':'fixture','base_url':'https://fixture.invalid','api_key_env':'TEST_DEEPSEEK_KEY'},'fixture',[])
    assert calls[-1]['usage']=={'input_tokens':7,'output_tokens':2} and calls[-1]['response'].get('error')


@pytest.mark.asyncio
@pytest.mark.parametrize('provider',['deepseek','ollama'])
async def test_stream_length_termination_is_not_a_successful_reply(monkeypatch,calls,provider):
    packet={'message':{'content':'截断的测试草稿'},'done':True,'done_reason':'length','prompt_eval_count':5,'eval_count':2} if provider=='ollama' else {
        'choices':[{'delta':{'content':'截断的测试草稿'},'finish_reason':'length'}],'usage':{'prompt_tokens':5,'completion_tokens':2}}
    payload=json.dumps(packet,ensure_ascii=False)+'\n' if provider=='ollama' else 'data: '+json.dumps(packet,ensure_ascii=False)+'\n\ndata: [DONE]\n\n'
    client_with(monkeypatch,lambda _:httpx.Response(200,text=payload))
    with pytest.raises(ValueError):
        await llm.stream_reply('s',{'provider':provider,'model':'fixture','base_url':'https://fixture.invalid','api_key_env':'TEST_DEEPSEEK_KEY'},[],lambda _:None)
    assert calls[-1]['usage']=={'input_tokens':5,'output_tokens':2} and calls[-1]['response'].get('error')
    assert '截断的测试草稿' in calls[-1]['response']['content']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,count',[('timeout',3),(500,3),(401,1),(429,1)])
async def test_provider_failures_have_bounded_attempts_and_no_implicit_provider_switch(monkeypatch,calls,failure,count):
    attempts=[]
    def handler(request):
        attempts.append(str(request.url))
        if failure=='timeout':raise httpx.ReadTimeout('明确标注的HTTP协议超时夹具',request=request)
        return httpx.Response(failure,json={'error':{'message':'HTTP协议错误夹具'}})
    async def immediate(_):pass
    monkeypatch.setattr(llm.asyncio,'sleep',immediate)
    client_with(monkeypatch,handler)
    with pytest.raises(httpx.HTTPError):
        await llm.invoke('s','r',{'provider':'deepseek','model':'fixture','base_url':'https://fixture.invalid','api_key_env':'TEST_DEEPSEEK_KEY'},'fixture',[])
    assert len(attempts)==count and set(attempts)=={'https://fixture.invalid/chat/completions'}
    assert len({c['request_id'] for c in calls})==count
    assert all(c['usage']['input_tokens'] is None for c in calls)


@pytest.mark.asyncio
async def test_deepseek_native_arguments_usage_and_tool_result_replay(monkeypatch,calls):
    def handler(request):
        body=json.loads(request.content)
        assert request.url.path=='/chat/completions'
        assert request.headers['authorization'].startswith('Bearer protocol-fixture')
        assert body['messages'][-1]['tool_call_id']=='previous'
        assert body['thinking']=={'type':'disabled'}
        return httpx.Response(200,json={'choices':[{'message':{'content':None,'tool_calls':[{'id':'actual-fixture-id',
            'function':{'name':'estimate_proposal','arguments':'{"production_spec":"simple"}'}}]},'finish_reason':'tool_calls'}],
            'usage':{'prompt_tokens':12,'completion_tokens':8}})
    client_with(monkeypatch,handler)
    profile={'provider':'deepseek','model':'fixture','base_url':'https://fixture.invalid','api_key_env':'TEST_DEEPSEEK_KEY'}
    result=await llm.invoke('sid','run',profile,'fixture',[{'role':'tool','tool_call_id':'previous','content':'{}'}])
    assert result['tool_calls'][0]['function']['arguments']=={'production_spec':'simple'}
    assert result['tool_calls'][0]['id']=='actual-fixture-id'
    assert calls[0]['response']['status']=='running'
    assert calls[0]['request_id']==calls[1]['request_id']
    assert calls[-1]['usage']=={'input_tokens':12,'output_tokens':8}


@pytest.mark.asyncio
@pytest.mark.parametrize('malformed,truncated',[(True,False),(False,True)])
async def test_failed_provider_outputs_keep_known_usage(monkeypatch,calls,malformed,truncated):
    packet={'choices':[{'message':{'content':'partial','tool_calls':[{'function':{'name':'t','arguments':'{'}}] if malformed else []},
                       'finish_reason':'length' if truncated else 'tool_calls'}], 'usage':{'prompt_tokens':3,'completion_tokens':4}}
    client_with(monkeypatch,lambda _:httpx.Response(200,json=packet))
    with pytest.raises(ValueError):
        await llm.invoke('s','r',{'provider':'deepseek','model':'fixture','base_url':'https://fixture.invalid','api_key_env':'TEST_DEEPSEEK_KEY'},'fixture',[])
    assert calls[-1]['usage']=={'input_tokens':3,'output_tokens':4}
    assert 'error' in calls[-1]['response']


@pytest.mark.asyncio
async def test_deepseek_stream_actual_deltas_and_terminal_usage(monkeypatch,calls):
    packets=[{'choices':[{'delta':{'content':'方案'},'finish_reason':None}]},
             {'choices':[{'delta':{'content':'预览'},'finish_reason':'stop'}]},
             {'choices':[],'usage':{'prompt_tokens':5,'completion_tokens':2}}]
    text=''.join('data: '+json.dumps(packet,ensure_ascii=False)+'\n\n' for packet in packets)+'data: [DONE]\n\n'
    def handler(request):
        assert json.loads(request.content)['thinking']=={'type':'disabled'}
        return httpx.Response(200,text=text,headers={'content-type':'text/event-stream'})
    client_with(monkeypatch,handler)
    deltas=[]
    result=await llm.stream_reply('s',{'provider':'deepseek','model':'fixture','base_url':'https://fixture.invalid','api_key_env':'TEST_DEEPSEEK_KEY'},[],deltas.append)
    assert result=='方案预览' and deltas==['方案','方案预览']
    assert calls[-1]['usage']=={'input_tokens':5,'output_tokens':2}


@pytest.mark.asyncio
async def test_stream_without_finish_is_not_reported_as_completed(monkeypatch,calls):
    client_with(monkeypatch,lambda _:httpx.Response(200,text='data: {"choices":[{"delta":{"content":"半句"}}]}\n\n'))
    with pytest.raises(ValueError,match='完成标记'):
        await llm.stream_reply('s',{'provider':'deepseek','model':'fixture','base_url':'https://fixture.invalid','api_key_env':'TEST_DEEPSEEK_KEY'},[],lambda _:None)
    assert calls[-1]['response']['error'] and calls[-1]['usage']['input_tokens'] is None
