# 测试

测试分为规则与数据库测试、MCP/HTTP协议测试、模型场景测试和浏览器测试。它们检查的问题不同：协议夹具能复现错误，真实模型场景才能说明模型是否按工具结果作出选择。

已有完整后端回归为170项，全部通过，无跳过。[控制台输出](../examples/external-live/backend.txt)和[JUnit](../examples/external-live/backend.xml)保存在仓库中。上传前重新运行的[170项回归](../examples/submission/backend.xml)与[4项页面检查](../examples/submission/playwright.json)另存于submission目录。本地完整业务场景在Windows验证；Linux规则与浏览器检查由[GitHub Actions](https://github.com/Askar27531/content-task-agent/actions/workflows/check.yml)运行，完整模型场景仍需单独验证。

## 主要用例

| 输入或操作 | 预期 | 实际结果与Trace |
| --- | --- | --- |
| 信息齐全，预算10，初始premium | 估算超限后选替代，复算通过再保存 | 12→8，保存一次；[记录](../examples/external-live/after/combined-replan-repeat/evidence.json) |
| “预算你看着办，最近做完” | 追问预算和截止时间，不执行 | 两项保持未确认，无业务调用；[记录](../examples/external-live/after/deepseek-vague/evidence.json) |
| 已完成后再次开始 | 不新增工具执行或保存 | 沿用同一运行与方案；[记录](../examples/external-live/after/combined-replan-repeat/evidence.json) |
| 素材成功后注入脚本失败，再续跑 | 保留素材和失败记录，同run恢复 | 搜索一次，新脚本尝试成功后保存；[记录](../examples/external-live/after/combined-failure-resume/evidence.json) |
| 一次脚本故障，同时存在预算超限 | 自动重试、重规划，保留成功结果 | 无人工续跑，attempt=2成功，12→8后保存；[记录](../examples/external-live/after/audit-auto-failure-replan/evidence.json) |
| 预算5 | 无可行候选时说明缺口，不保存 | 成本8、缺口3，保留素材与脚本；[记录](../examples/external-live/after/deepseek-low-budget/evidence.json) |
| 严格真实来源，搜索主动超时 | 不使用合成资料冒充完成 | 未完成、无保存；[记录](../examples/external-live/after/tavily-timeout-strict/evidence.json) |
| 搜索主动超时，允许降级 | 模型可选择合成，保留失败及来源说明 | 改用合成资料并保存；[记录](../examples/external-live/after/tavily-timeout-fallback/evidence.json) |
| 相同真实搜索再次调用 | 有效缓存命中，不重复收费 | 一次远程请求，第二次命中缓存；[记录](../examples/external-live/after/real-tavily-cache-guards/evidence.json) |

8组场景都符合各自预期，部分用例的正确结果是“未完成”。[索引](../examples/external-live/after/index.json)和[事实审计](../examples/external-live/after/audit.json)没有将它们计为方案生成成功。

每份 evidence.json 中，inputs 是输入，workspace/state 是输出，tool_calls、state.model_calls、events、jobs 是运行记录。保存方案另外导出了Markdown和JSON，集中入口见[examples](../examples/README.md)。

## 预算、依赖和实际状态

`scripts/audit_external_review.py` 从已有Trace重算27项断言，包括成本明细、截止时间、步骤依赖、失败尝试、重规划、内容指纹和保存记录。[结果](../examples/external-live/after/review-audit.json)全部通过。

核心场景的制作成本依次是12、8，两次都等于规则明细的Decimal加总。预算始终为10，最终方案成本8。工具模拟费0.55另列，不参与制作预算判断。

脚本第一次失败记录仍为failed，新操作attempt=2才是success。检索只执行一次，成功脚本在预算重规划后仍使用同一产物。旧计划为superseded，未执行的旧版检查与保存不记成功；新版保存前有有效估算和校验。

## 外部接口与前端

DeepSeek提供方测试包含原生工具选择、结果回传、流式、错误模型、错误密钥、截断与用量保留，8项检查通过；另有3项真实流截断检查。Tavily测试覆盖中文/emoji、非法域名业务错误、合法域名无结果和错误密钥，4项通过。

浏览器中检查了6个外部场景，并回归4个既有功能。界面状态、历史成本、失败记录、来源和文档与API结果一致。材料见[外部测试报告](11-external-live-and-review.md)。

## 问题记录

开发中发现过模型补造日期、条件句误触发执行、交付阶段空工具返回、缓存政策失效、旧worker迟到写入、SSE事件缺口、预算不足却丢失最终缺口等问题。各报告保留触发输入和修复前结果：

- [输入与控制边界](07-backend-boundaries.md)
- [交付工具调用](08-delivery-tool-fix.md)
- [任务恢复与费用](09-backend-completion.md)
- [缓存、并发与事件](10-new-feature-boundaries.md)
- [DeepSeek与Tavily](11-external-live-and-review.md)

测试记录中也有夹具匹配错误和测试前置条件缺失，已单独说明，没有都归为产品缺陷。

## 运行测试

先按[README](../README.md)安装依赖、初始化数据库。

```powershell
$env:PYTHONPATH='src/backend'
$env:CONTENT_STUDIO_DB_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/backend -q
.venv\Scripts\ruff.exe check src/backend tests/backend scripts --select E9,F63,F7,F82
npm run typecheck
npm run build
```

浏览器和真实模型测试需要对应服务、模型及数据库会话。具体命令在各报告中。重新打开材料中的会话链接，需要保留原数据库，或先运行脚本生成新会话；静态JSON不会自动导入数据库。

只核对已有文件可以运行：

```powershell
python scripts/check_delivery.py
python scripts/audit_external_review.py
```

这两条不调用模型或数据库。

## 尚未覆盖

模拟制作规则不代表商业报价。文案结构和引用通过检查，也可能有未经输入证实的泛化卖点，需要人工审稿。供应商价格未配置时费用未知，尚未做账单对账。

并发和崩溃测试覆盖了报告中的窗口，没有验证多机负载、长期容量或所有竞态。模型与网络变化也可能使重跑得到不同结果。前端主JS约989KB，仍需拆分。
