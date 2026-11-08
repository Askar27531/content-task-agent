# 任务、费用与恢复

后端除了执行Agent，还要处理重复提交、进程退出、搜索结果不完整和页面重连。这些功能共用PostgreSQL中的任务、操作、费用与事件记录。

## 实现

`0002_ledger_cache` 迁移增加费用账本、搜索缓存和同会话任务约束。模型请求先登记，再用同一request_id更新响应；用量未知时金额保持null。历史费用通过backfill-ledger幂等补录。

MCP网关冻结工具schema，校验参数、依赖和尝试上限。搜索结果归一化后交给模型评审：接受、重试、合成补充或停止。空结果不完成素材步骤，部分成功保留可用项。缓存按服务、查询、数量隔离。

新运行读取新配置，续跑保留原模型、规则和Skill。仅预算或时间变化时，可复用签名仍有效的产物；写作要求变化需要重写脚本，新的估算和检查不能省略。

worker通过租约领取任务，丢失租约后停止派发。恢复先核对操作和保存事实，再推进图。事件从数据库重放，分页和SSE重连不会重做业务。

## 恢复测试

测试在三个位置真实终止worker，而不是只抛异常：

| 退出位置 | 恢复要求 |
| --- | --- |
| 素材提交后、检查点更新前 | 读取已有检索结果，不重复检索或收费 |
| 保存提交后、响应交给图之前 | 找到已保存方案，不重复保存 |
| 模型请求意图登记后、响应确认前 | 原请求保留未知用量，恢复后另记新请求 |

14组集成场景包含搜索缓存、部分结果、严格来源、降级、产物复用、配置与Skill恢复、停止/保存竞争及上述退出窗口。13组调用本地Qwen，缓存组直接检查网关。外部搜索使用明确标注的本地MCP夹具，DeepSeek使用HTTP夹具。

[场景索引](../examples/backend-completion/index.json) · [费用与依赖审计](../examples/backend-completion/audit.json)

## 结果

这组功能的保存记录为105项后端回归、14组集成场景、9项HTTP/SSE检查和9项MCP恢复/拒绝检查通过。对应输入输出在上面的索引中；[传输记录](../examples/backend-completion/transport/evidence.json)和[页面报告](../examples/backend-completion/ui/report.json)单独保存。

浏览器核对复用定位、部分成功、严格空结果和断线恢复。断线用例还注入了传输事件和一次503，业务内容仍从API读取。

这组夹具不计为外部服务成功。DeepSeek、Tavily的实际调用见[外部测试](11-external-live-and-review.md)，完整回归结果见[测试总览](03-evaluation.md)。

## 验收覆盖

| 编号 | 检查内容 | 材料入口 |
| --- | --- | --- |
| E01—E07 | 正常、缺项、故障、预算调整、不可行与重复执行 | [基础案例](../examples/README.md) |
| E08/E10/E17 | 空/部分搜索、降级、MCP参数和错误 | [集成索引](../examples/backend-completion/index.json) |
| E09/E13 | 失败续跑、进程退出、保存去重 | [集成审计](../examples/backend-completion/audit.json) |
| E11/E14 | 工期、预算、字段与依赖 | [输入边界](07-backend-boundaries.md) |
| E12/E18 | 配置快照、Skill正文/资源与恢复 | [集成索引](../examples/backend-completion/index.json) |
| E15/E16 | 聊天授权、版本、停止和修改 | [控制边界](07-backend-boundaries.md) |
| E19/E22 | 检查失效、阶段白名单与保存条件 | [MCP与状态设计](02-solution.md) |
| E20/E21 | 事件重放、文案内部模型与费用 | [传输记录](../examples/backend-completion/transport/evidence.json)、[集成审计](../examples/backend-completion/audit.json) |

E01—E22的定义保留在[执行计划](../execution-plan.md)。范围仍是两种套餐，不包含任意制作清单优化。

## 运行

需要PostgreSQL和本地Qwen。Qwen专用脚本要求所用YAML选择local_qwen3；可复制配置模板到独立文件，再通过CONTENT_STUDIO_CONFIG指定，避免覆盖日常配置。脚本会启动独立服务和测试队列。

```powershell
$env:PYTHONPATH='src/backend'
$env:CONTENT_STUDIO_DB_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/backend -q
.venv\Scripts\python.exe scripts/verify_backend_completion.py
.venv\Scripts\python.exe scripts/audit_backend_completion.py
.venv\Scripts\python.exe scripts/verify_backend_transport.py
.venv\Scripts\python.exe scripts/verify_backend_guards.py
npx playwright test --config playwright.completion.config.ts
```

浏览器还需要默认8000/5173服务，以及当前数据库中的测试会话。GitHub Actions配置见[check.yml](../.github/workflows/check.yml)，远端结果见[工作流记录](https://github.com/Askar27531/content-task-agent/actions/workflows/check.yml)。

外部请求在超时或退出后可能已被服务端接收，无法保证恰好调用一次；业务保存与收费去重由本地事务和唯一约束保证。
