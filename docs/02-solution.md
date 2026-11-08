# 设计

浏览器只连接 FastAPI。API 接收消息后把任务写入 PostgreSQL，由独立 worker 处理；执行过程再通过 SSE 返回页面。

对话和执行拆成两个 worker、两张 LangGraph。这样生成脚本时，新的停止或修改消息仍能被处理，不必等整个活动完成。

## 技术栈

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| React / Vite / TypeScript | 19.3.1 / 8.3.6 / 7.0.2 | 页面、构建、类型检查 |
| Ant Design | 6.7.0 | UI组件与主题 |
| React Router / TanStack Query | 7.18.5 / 5.106.1 | 会话路由、查询与缓存 |
| FastAPI / Pydantic | 0.162.0 / 2.14.3 | HTTP、SSE、请求和配置校验 |
| SQLAlchemy / psycopg / Alembic | 2.1.8 / 3.3.8 / 1.20.1 | 事务、PostgreSQL连接与迁移 |
| LangGraph / checkpoint-postgres | 1.2.20 / 3.2.0 | 状态图与检查点 |
| MCP Python SDK | 2.3.0 | 工具发现、Streamable HTTP连接与调用 |
| pytest / Playwright | 9.2.0 / 1.65.0 | 后端和浏览器测试 |

依赖锁在根目录。模型适配使用 HTTPX，直接调用 Ollama `/api/chat`、DeepSeek `/chat/completions`，记录原生工具调用和 token 用量。没有额外使用 LangChain 模型封装，也不保存模型隐藏思考。

## 对话图

`load_dialogue_context → understand_message → validate_requirements` 读取会话并识别需求。缺项进入 `clarify_requirements`；需求可用时经过 `select_skills → build_plan_preview → validate_plan_preview`；执行、停止、续跑进入 `apply_chat_action`，最后由 `respond` 返回消息。

模型输出结构化意图，业务代码再检查原文证据、缺项、计划版本与重复提交。首次需求即使带有“开始”，也先生成预览。撤回预算或日期会清空相应字段并使旧计划失效。写作数量、渠道、格式等要求随原始需求固定到运行中，控制消息不混入写作要求。

## 执行图

入口为 `load_run_context → reconcile_state → select_execution_skills → assess_progress`。先从操作账本恢复结果，再决定下一步。

- `decide_next_action` 向模型提供当前状态和阶段内可用工具。
- `guard_action` 检查租约、控制请求、参数、依赖、白名单与调用上限。
- `call_mcp_tool` 发起调用，`record_tool_result` 保存结果，再返回进度判断。
- `review_materials` 处理空结果、部分结果和来源要求。
- `replan` 创建替代计划；`compose_proposal` 组装交付草稿。
- `validate_delivery → verify_delivery → save_proposal` 检查并保存。
- `handle_stop_or_pause` 处理停止或需求变更，`finalize_run` 给出完成或未完成说明。

估算不通过时，规则表先过滤无法改善已违反条件的候选。模型在剩余候选与停止之间决策；选了新规格也必须重新调用估算，不能把旧结果中的12改成8。素材和文案仍有效时复用，估算和交付检查重新执行。

同一运行累计计算尝试与执行时长，续跑不重置限额。连续3轮没有可派发的原生工具调用会停止并说明原因；已有结果保留。

## MCP 与 Skill

自建 MCP Server 是本地独立进程，使用 Streamable HTTP。工具包括：

| 工具 | 作用 |
| --- | --- |
| `search_synthetic_materials` | 检索合成素材 |
| `generate_script` | 使用运行固定模型与文案Skill生成脚本 |
| `estimate_proposal` | 返回成本、串行排期、可行性及缺口 |
| `validate_proposal` | 检查章节、字段与引用 |
| `save_proposal` | 调用业务服务保存方案 |
| `load_skill` / `read_skill_resource` | 读取注册Skill正文与资源 |

`search_web_materials` 由后端适配 Tavily 远程 MCP。模型只能使用公开的逻辑工具名，不能指定连接地址、凭证或 worker 租约。不同阶段提供不同白名单，加载交付 Skill 也不能使文案阶段获得保存权限。

四个 Skill 位于 `skills/`：活动策划、文案脚本、受限重规划、方案交付。模型先看到元数据，再请求加载正文或资源。`generate_script` 的文案 Skill 是工具固定依赖，与模型主动加载分别记录。生成器内部不再运行工具循环。

只有交付 Skill 带确定性检查脚本，通过 `validate_proposal` 调用。成本计算和保存事务仍在业务代码中，避免两套实现。

## 状态与恢复

业务数据保存在七张表中：

- `studio_sessions`：会话、需求、计划、步骤及产物的JSONB聚合。
- `studio_jobs`：任务队列和租约。
- `studio_operations`：每次工具尝试及实际结果。
- `studio_proposals`：已提交方案。
- `studio_events`：会话内递增事件。
- `studio_cost_entries`：请求与工具费用账本。
- `studio_search_cache`：联网搜索缓存。

LangGraph 检查点使用独立的 `studio_checkpoints` schema。检查点恢复图上下文，业务表确认操作和保存是否已经完成。遇到保存响应丢失，恢复时读取已有方案，不重复保存。

任务通过 `FOR UPDATE SKIP LOCKED` 领取。会话锁与唯一索引限制同类任务并发，message/run 两类仍可并行。心跳续租，epoch 阻止过期 worker 写入；丢失租约后停止派发。

每次尝试单独记录。脚本重试成功后逻辑步骤可完成，原失败操作仍是 failed。估算调用 success 只表示算出了结果，`is_feasible=false` 仍然不能保存；`job.done` 也不等于 `run.completed`。

运行快照固定模型、规则、工具schema、Skill资源与限制。预算或时间单独变化时，可以复用签名仍有效的素材和脚本；写作要求、模型、素材或Skill变化会使复用失效。脚本不兼容或旧资源缺失时，恢复会明确受阻。

JSONB 聚合便于本地项目同步状态，但长会话会使对象变大。拆分消息、事件和产物表是后续工作。

## 费用与外部调用

成本使用 Decimal 计算。制作预算、工具模拟费、供应商货币和 Tavily credits 分开汇总。

模型请求先登记 pending，取得响应后更新同一 request_id。截断和解析失败仍保留返回的用量；无法确认响应时保持未知。失败工具不收项目模拟费，但其内部模型请求仍按实际用量记录。缓存命中与产物复用不重复收费。

DeepSeek JSON模式除了设置 response_format，还在提示中携带schema，再做本地校验。Tavily 可能在HTTP200中返回业务错误，需要读取 error/status/detail。远程连接由独立异步任务持有，避免跨任务关闭AnyIO上下文。代理只影响远程MCP连接，证书校验开启；最多三次连接尝试，不隐式重试搜索RPC。

搜索结果逐项检查，保留可用项。空结果不等于素材步骤成功，部分结果由模型决定接受、补充、有限重试或停止。来源降级说明会随方案保存。

## API

| 接口 | 内容 |
| --- | --- |
| `GET /api/health` | 数据库健康状态 |
| `GET/POST /api/sessions` | 会话列表、新建 |
| `GET /api/sessions/{id}` | 概览、计划、运行、费用摘要及事件水位 |
| `GET/POST /api/sessions/{id}/messages` | 分页时间线、幂等提交消息 |
| `GET /api/sessions/{id}/events` | 会话SSE，支持Last-Event-ID |
| `GET /api/sessions/{id}/plans`、`GET /api/plans/{id}` | 计划版本 |
| `GET /api/tool-calls/{id}`、`GET /api/artifacts/{id}` | 工具输入输出、产物正文 |
| `GET /api/runs/{id}`及`/steps`、`/costs`、`/materials` | 运行详情 |
| `GET /api/sessions/{id}/proposals`、`GET /api/proposals/{id}` | 保存方案及版本 |
| `GET /api/proposals/{id}/export?format=markdown`或`json` | 导出选中版本 |

同一 client_message_id、相同内容返回原受理结果；不同内容返回409。消息长度为1—20000字符，view_context 仅接收可选 plan_version_id、proposal_id，最长64字符。格式或游标错误返回422，错误正文使用 `error.code/message/details`。

快照先给首屏数据，大段消息和工具结果按需读取。每个会话只订阅一条SSE，事件编号用于去重和补齐；水位过期或出现缺口时重新同步快照。读取、分页和重连都不触发新的业务调用。
