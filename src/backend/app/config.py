from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv, dotenv_values
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env", override=False)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://studio:studio_dev@127.0.0.1:5433/content_studio")


def credential(name: str) -> str | None:
    return os.getenv(name) or dotenv_values(ROOT / '.env').get(name) or None


def config_file() -> Path:
    requested=os.getenv('CONTENT_STUDIO_CONFIG','config/app.yaml')
    path=Path(requested)
    path=path if path.is_absolute() else ROOT/path
    if not path.exists():
        if requested!='config/app.yaml':raise ValueError('指定的YAML配置文件不存在。')
        path=ROOT/'config/app.example.yaml'
    return path


def worker_options() -> dict:
    defaults={'poll_seconds':.5,'lease_seconds':60,'heartbeat_seconds':10}
    value=yaml.safe_load(config_file().read_text(encoding='utf8'))
    result={**defaults,**value.get('worker',{})}
    if any(float(result[key])<=0 for key in defaults):raise ValueError('worker时间配置必须为正数。')
    return result


class ModelConfig(BaseModel):
    provider: str
    base_url: str
    model: str
    api_key_env: str | None = None
    timeout_seconds: int = Field(default=180,gt=0)
    temperature: float = Field(default=0,ge=0,le=2)
    max_tokens: int = Field(default=1600,gt=0)
    input_cost_per_million: str | None = None
    output_cost_per_million: str | None = None
    currency: str = 'USD'

    @field_validator('input_cost_per_million', 'output_cost_per_million')
    @classmethod
    def valid_rate(cls,value):
        if value is not None:
            try:
                rate=Decimal(value)
            except InvalidOperation as exc:
                raise ValueError('模型单价必须是非负有限值或不配置。') from exc
            if not rate.is_finite() or rate<0:
                raise ValueError('模型单价必须是非负有限值或不配置。')
        return value

    @field_validator('provider')
    @classmethod
    def valid_provider(cls, value):
        if value not in {'ollama', 'deepseek'}:
            raise ValueError('模型provider仅支持ollama或deepseek。')
        return value


class Settings(BaseModel):
    active_model: str
    models: dict[str, ModelConfig]
    mcp: dict
    limits: dict[str, int]
    fault_injection: dict = Field(default_factory=dict)
    worker: dict = Field(default_factory=lambda: {'poll_seconds': 0.5, 'lease_seconds': 60, 'heartbeat_seconds': 10})
    search: dict = Field(default_factory=lambda: {'max_results': 4, 'cache_ttl_seconds': 3600, 'material_ttl_seconds': 86400, 'min_results': 1})

    @field_validator('limits')
    @classmethod
    def positive_limits(cls, value):
        if any(number <= 0 for number in value.values()):
            raise ValueError('运行限额必须为正数。')
        return {'max_tool_attempts':3,'max_business_calls':30,'max_context_calls':12,
                'max_replans':3,'run_timeout_seconds':900,'max_model_rounds':24,**value}

    @field_validator('search')
    @classmethod
    def valid_search(cls,value):
        result={'max_results':4,'min_results':1,'cache_ttl_seconds':3600,'material_ttl_seconds':86400,**value}
        if not 1<=result['min_results']<=result['max_results'] or min(result['cache_ttl_seconds'],result['material_ttl_seconds'])<0:
            raise ValueError('搜索数量和缓存/素材有效期不合法。')
        return result


def settings() -> Settings:
    path = config_file()
    result = Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if result.active_model not in result.models:
        raise ValueError("active_model 未指向已定义模型。")
    selected = result.models[result.active_model]
    if selected.provider == "deepseek" and not credential(selected.api_key_env or "DEEPSEEK_API_KEY"):
        raise ValueError("DeepSeek 模式缺少对应环境变量密钥，未自动切换模型。")
    return result


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def skill_catalog() -> list[dict]:
    result = []
    for directory in sorted((ROOT / "skills").iterdir()):
        entry = directory / "SKILL.md"
        if not entry.is_file():
            continue
        text = entry.read_text(encoding="utf-8")
        metadata = yaml.safe_load(text.split("---", 2)[1])
        resources = {}
        for folder in ("references", "assets"):
            for path in sorted((directory / folder).glob("*.md")):
                resources[f"{folder}/{path.name}"] = path.read_text(encoding="utf-8")
        scripts = {f"scripts/{path.name}": hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted((directory / "scripts").glob("*.py"))}
        result.append({"skill_id": metadata["name"], "description": metadata["description"], "body": text,
                       "resources": resources, "script_hashes": scripts,
                       "version": canonical_hash({"body": text, "resources": resources, "scripts": scripts})})
    return result


def runtime_snapshot() -> dict:
    value = settings().model_dump()
    value["selected_model"] = copy.deepcopy(value["models"][value["active_model"]])
    value["skills"] = skill_catalog()
    value["production_rules"] = json.loads((ROOT / "data/production-rules.json").read_text(encoding="utf-8"))
    value["tavily_enabled"] = bool(credential(value["mcp"].get("tavily_key_env", "TAVILY_API_KEY")))
    value['material_catalog_hash']=canonical_hash(json.loads((ROOT/'data/materials.json').read_text(encoding='utf8')))
    value["config_hash"] = canonical_hash(value)
    return value
