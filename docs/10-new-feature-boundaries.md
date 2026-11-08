# 缓存、并发与事件边界

这组测试检查功能组合后的问题：缓存政策改变、产物已经存在但步骤失败、过期worker写入、事件中间缺号，以及改变需求后的复用。

结果为159项后端回归、13组本地模型场景及审计、40轮并发竞争、13项HTTP/SSE、7项浏览器检查和4项功能回归通过。[汇总](../examples/new-feature-boundaries/summary.json)中保留了测试口径。

## 场景

| 场景 | 检查结果 | Trace |
| --- | --- | --- |
| 混合畸形素材 | 保留可用项，不因单个坏链接整批失败 | [mixed-shapes](../examples/new-feature-boundaries/after/mixed-shapes/evidence.json) |
| 重复来源 | 同一链接只计一次 | [duplicates](../examples/new-feature-boundaries/after/duplicates/evidence.json) |
| 严格最低数量 | 1项不足2项时未完成，不合成补数 | [strict-minimum](../examples/new-feature-boundaries/after/strict-minimum/evidence.json) |
| 部分成功后失败 | 保留已有素材，遵守一次重检要求 | [partial-then-failure](../examples/new-feature-boundaries/after/partial-then-failure/evidence.json) |
| 素材夹带忽略预算指令 | 仍估算、检查，保存成本8 | [instruction-in-source](../examples/new-feature-boundaries/after/instruction-in-source/evidence.json) |
| 只改文案、沿用预算 | 不清空预算，新计划等待确认，重做脚本 | [changed-copy](../examples/new-feature-boundaries/after/changed-copy/evidence.json) |
| 收紧来源要求 | 不复用不合格的合成资料与脚本 | [stricter-sources](../examples/new-feature-boundaries/after/stricter-sources/evidence.json) |
| 预览后改模型配置 | 刷新预览，再次确认才执行 | [preview-config-change](../examples/new-feature-boundaries/after/preview-config-change/evidence.json) |
| 检查后、保存前改预算 | 旧运行不保存，新运行重新估算和检查 | [change-before-save](../examples/new-feature-boundaries/after/change-before-save/evidence.json) |
| 次数耗尽后继续 | 不重置次数、不新建任务 | [exhausted-resume](../examples/new-feature-boundaries/after/exhausted-resume/evidence.json) |

另外3组在素材提交、保存提交、模型请求登记窗口终止进程，重新检查恢复。[完整索引与审计](../examples/new-feature-boundaries/after/audit.json)包含13组结果。

搜索返回来自标注为协议测试夹具的本地MCP服务；DeepSeek边界使用HTTP夹具。不能把这里的通过数当作Tavily或DeepSeek真实接口测试。

## 修复

缓存读取原来只检查键，改为同时检查开关、数量和资料时效。复用还需要确认原步骤确实成功，并核对新运行的素材要求。缺失、错误或未来来源时间不得被当成新资料。

模型空响应和截断不能完成任务，但已返回草稿与用量要保留。重复登记请求不能把已确认结果退回pending。搜索已结束而费用无法确认时，标记unknown。

同会话并发领取曾被唯一索引拒绝并导致worker退出；现在持有会话锁后再检查。过期租约不能自行续期，终态任务也不能被迟到响应改写。实际保存后发生清理异常，不再推翻完成状态。

SSE需要检查每一段事件是否连续，不能只检查起始水位。中间缺号要求重新同步，过长或非法水位返回422。

口语中的“预算不变”不再被当作缺少新金额。素材重试次数取明确需求与系统上限的较小值，执行和续跑使用同一限制。

## 测试记录

[并发记录](../examples/new-feature-boundaries/after/concurrency.json)包含40轮、每轮8个worker竞争同会话任务，以及20个响应并发重放同一模型费用。它检查竞态和去重，没有测试多机吞吐。

[HTTP/SSE记录](../examples/new-feature-boundaries/after/transport/evidence.json)有13项：跨批次重放、分页期间新增、Last-Event-ID优先、重复事件编号、中间缺号、错误水位与游标。读取没有新增模型、工具或费用记录。

[浏览器报告](../examples/new-feature-boundaries/after/ui/report.json)检查异常元数据、重复来源、部分后失败、新旧文档、严格来源、次数耗尽和SSE缺口。事件夹具只写入专用测试会话，页面仍从API读取。

修复前输出和夹具错误留在before、debug目录。部分后失败用例曾因夹具分支匹配错误而第一步就失败，这不属于成功素材被业务丢弃。

## 运行

使用独立本地Qwen配置，保持PostgreSQL可用。脚本自己启动API、MCP和worker；浏览器需要默认工作台服务。

```powershell
$env:CONTENT_STUDIO_DB_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/backend -q
.venv\Scripts\python.exe scripts/test_new_feature_concurrency.py
.venv\Scripts\python.exe scripts/test_new_feature_transport.py --stage after
.venv\Scripts\python.exe scripts/test_new_feature_live.py --stage after
.venv\Scripts\python.exe scripts/audit_backend_completion.py --root examples/new-feature-boundaries/after
npx playwright test --config playwright.feature-boundaries.config.ts
```

审计在场景全部结束后执行。脚本重跑会创建新会话并更新同名材料，不要求复用旧数据库中的会话ID。

明确重试次数只覆盖已测试的素材/搜索语句，更多口语表达还需扩展。资料夹带指令的用例通过，也不代表所有提示攻击都被解决。
