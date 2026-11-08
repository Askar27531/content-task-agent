from __future__ import annotations

import asyncio
import json
import os
from typing import Callable

import httpx
from jsonschema import validate, ValidationError

from .store import record_model_call, uid
from .config import credential


def provider_messages(messages: list[dict], provider: str) -> list[dict]:
    result = []
    for message in messages:
        item = {"role": message["role"], "content": message.get("content", "")}
        if message.get("tool_calls"):
            item["tool_calls"] = []
            for call in message["tool_calls"]:
                function = call["function"]
                if provider == "ollama":
                    item["tool_calls"].append({"function": {"name": function["name"], "arguments": function["arguments"]}})
                else:
                    item["tool_calls"].append({"id": call["id"], "type": "function", "function": {
                        "name": function["name"], "arguments": json.dumps(function["arguments"], ensure_ascii=False)}})
        if message["role"] == "tool":
            item["tool_name" if provider == "ollama" else "tool_call_id"] = message.get("tool_name" if provider == "ollama" else "tool_call_id", "")
        result.append(item)
    return result


async def invoke(session_id: str, run_id: str | None, profile: dict, kind: str, messages: list[dict],
                 tools: list[dict] | None = None, schema: dict | None = None) -> dict:
    provider = profile["provider"]
    native_messages = provider_messages(messages, provider)
    body = {"model": profile["model"], "messages": native_messages, "stream": False}
    headers = {}
    if provider == "ollama":
        url = profile["base_url"].rstrip("/") + "/api/chat"
        body.update({"think": False, "options": {"temperature": profile.get("temperature", 0), "num_ctx": 16384, "num_predict": profile.get("max_tokens", 1600)}})
        if schema:
            body["format"] = schema
    else:
        url = profile["base_url"].rstrip("/") + "/chat/completions"
        key = credential(profile.get("api_key_env") or "DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("当前运行的模型密钥未配置；不会自动切换服务。")
        headers["Authorization"] = f"Bearer {key}"
        body.update({"temperature": profile.get("temperature", 0), "max_tokens": profile.get("max_tokens", 1600)})
        body['thinking']={'type':'disabled'}
        if schema:
            body["response_format"] = {"type": "json_object"}
            instruction='\n严格按以下JSON Schema输出数据实例，包含所有required字段；不要输出schema、response_format或额外包装。字段类型必须匹配，未提供的可选字段可以省略。\n'+json.dumps(schema,ensure_ascii=False)
            if body['messages'] and body['messages'][0]['role']=='system':
                body['messages'][0]['content']+=instruction
            else:
                body['messages'].insert(0,{'role':'system','content':instruction})
    if tools:
        body["tools"] = tools
    tool_names = [tool["function"]["name"] for tool in tools or []]
    for attempt in range(3):
        request_id = record_model_call(session_id, run_id, kind, profile, {'status':'running'},
            {'input_tokens':None,'output_tokens':None}, tool_names)
        usage={'input_tokens':None,'output_tokens':None}
        try:
            async with httpx.AsyncClient(timeout=profile.get("timeout_seconds", 180), trust_env=False) as client:
                response = await client.post(url, json=body, headers=headers)
                response.raise_for_status()
            data = response.json()
            usage = {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count")}
            if provider != "ollama":
                usage = {"input_tokens": data.get("usage", {}).get("prompt_tokens"), "output_tokens": data.get("usage", {}).get("completion_tokens")}
                if not data.get('choices'):
                    raise ValueError('模型未返回可用choices，未把空响应当作成功。')
            native = data.get("message") if provider == "ollama" else data["choices"][0]["message"]
            finish=data.get('done_reason') if provider=='ollama' else data['choices'][0].get('finish_reason')
            if finish in {'length','max_tokens'}:
                raise ValueError('模型输出达到长度限制，未把截断内容当作有效交付。')
            calls = []
            for call in native.get("tool_calls") or []:
                function = call["function"]
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                calls.append({"id": call.get("id") or uid(), "type": "function", "function": {"name": function["name"], "arguments": arguments}})
            result = {"role": "assistant", "content": native.get("content", "") or "", "tool_calls": calls,'finish_reason':finish}
            record_model_call(session_id, run_id, kind, profile, result, usage, tool_names, request_id=request_id)
            return result
        except asyncio.CancelledError:
            record_model_call(session_id,run_id,kind,profile,{'error':'REQUEST_INTERRUPTED','result_unknown':True},usage,tool_names,request_id=request_id)
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            record_model_call(session_id, run_id, kind, profile, {"error": str(exc)[:300], "attempt": attempt + 1}, usage, tool_names, request_id=request_id)
            retryable = isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)) or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500)
            if not retryable or attempt == 2:
                raise
            await asyncio.sleep(attempt + 1)
    raise RuntimeError("模型请求未完成。")


async def stream_reply(session_id: str, profile: dict, messages: list[dict], on_delta: Callable) -> str:
    """Stream actual provider output, not a character-by-character replay of a cached answer."""
    provider = profile["provider"]
    body = {"model": profile["model"], "messages": provider_messages(messages, provider), "stream": True}
    headers = {}
    if provider == "ollama":
        url = profile["base_url"].rstrip("/") + "/api/chat"
        body.update({"think": False, "options": {"temperature": 0, "num_predict": 350, "num_ctx": 8192}})
    else:
        url = profile["base_url"].rstrip("/") + "/chat/completions"
        key=credential(profile.get("api_key_env") or "DEEPSEEK_API_KEY")
        if not key:raise ValueError('当前运行模型密钥未配置，不自动切换。')
        headers["Authorization"] = "Bearer " + key
        body.update({"max_tokens": 350, "stream_options": {"include_usage": True}})
        body['thinking']={'type':'disabled'}
    combined = ""; usage = {"input_tokens": None, "output_tokens": None}
    request_id=record_model_call(session_id,None,'dialogue_response_stream',profile,{'status':'running'},usage)
    error=None;complete=False
    try:
        async with httpx.AsyncClient(timeout=profile.get("timeout_seconds", 180), trust_env=False) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        complete=True
                        break
                    packet = json.loads(line)
                    truncated=False
                    if provider == "ollama":
                        text = packet.get("message", {}).get("content", "")
                        if packet.get("done"):
                            complete=True
                            usage = {"input_tokens": packet.get("prompt_eval_count"), "output_tokens": packet.get("eval_count")}
                            if packet.get('done_reason') in {'length','max_tokens'}:
                                truncated=True
                    else:
                        choices = packet.get("choices", [])
                        if choices and choices[0].get('finish_reason')=='stop':complete=True
                        text = choices[0].get("delta", {}).get("content", "") if choices else ""
                        if packet.get("usage"):
                            usage = {"input_tokens": packet["usage"].get("prompt_tokens"), "output_tokens": packet["usage"].get("completion_tokens")}
                        if choices and choices[0].get('finish_reason') in {'length','max_tokens','content_filter'}:
                            truncated=True
                    if text:
                        combined += text
                        on_delta(combined)
                    if truncated:raise ValueError('模型流未正常完成，截断或被过滤的草稿不作为完整回复。')
        if not complete:raise ValueError('模型流在完成标记前中断；已收到的草稿和未知用量保留。')
    except BaseException as exc:
        error=type(exc).__name__
        raise
    finally:
        result={'content':combined,**({'error':error} if error else {})}
        record_model_call(session_id,None,'dialogue_response_stream',profile,result,usage,request_id=request_id)
    return combined


def json_content(result: dict, schema: dict | None = None) -> dict:
    text = result["content"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    value = json.loads(text)
    if schema:
        try:
            validate(value, schema)
        except ValidationError as exc:
            raise ValueError("模型结构化输出未通过校验：" + exc.message[:200]) from exc
    return value
