# 交付阶段的工具调用

脚本已经生成、估算也可行，模型却没有继续校验或保存。这是输入边界测试中两条完成断言失败的原因。模型没有越权，也没有假保存，但任务仍然没有交付。

## 原因

交付阶段的上下文仍包含完整素材、写作要求和早先“先给计划，不要执行”等语句。当前草稿、估算和检查标识埋在历史信息里。Trace中出现反复读取写作Skill，随后连续空工具返回。

调整后，`ExecutionRunner.decision_facts` 在交付阶段只提供当前目标、draft_id、估算摘要和有效检查标识。写作阶段仍保留原需求，完整历史也没有删除。模型仍需自行发起原生工具调用，网关和保存服务仍会验证权限、预算、版本与内容指纹。

续跑时还发现旧 blocked_reason 残留。现在步骤实际开始后清除当前受阻说明，历史失败留在原尝试中。

UI复验补充了能力缺口持久化和分句否定检查：明确要求成片、发布或付款的消息不能只回一句做不到而不更新右侧；纯能力询问则不改活动需求。

## 验证结果

| 验证 | 结果 | 材料 |
| --- | --- | --- |
| 完整边界矩阵 | 34/34通过 | [索引](../examples/backend-boundaries/delivery-fix/final/index.json)、[API](../examples/backend-boundaries/delivery-fix/final/api-boundaries.json) |
| 规则与PostgreSQL回归 | 65项通过，无跳过 | [JUnit](../examples/backend-boundaries/delivery-fix/unit-tests.xml) |
| 原失败路径的新会话 | 两项均校验并保存 | [定向测试](../examples/backend-boundaries/delivery-fix/targeted/index.json) |
| 原失败运行直接续跑 | 同run恢复，复用素材、脚本和估算，只保存一次 | [恢复索引](../examples/backend-boundaries/delivery-fix/recovery/index.json) |
| 12→8预算调整 | 两次估算，检索和脚本各一次，最终保存一次 | [记录](../examples/backend-boundaries/delivery-fix/recovery/24-replan-delivery/evidence.json) |
| Trace审计 | 23项语义/控制场景记录正确 | [审计](../examples/backend-boundaries/delivery-fix/final/trace-audit.json) |

模型保持Ollama Qwen3，没有放宽完成断言。连续3次空工具返回仍会终止，避免无限循环。

[上下文对照](../examples/backend-boundaries/delivery-fix/diagnostic/context-probe.json)只请求模型并发现工具，没有派发业务调用。它用于比较工具选择；完成结论来自上表的完整运行。两次原运行的恢复分别记录在[脚本交付](../examples/backend-boundaries/delivery-fix/recovery/20-video-script-supported/evidence.json)和[停止续跑](../examples/backend-boundaries/delivery-fix/recovery/23-stop-resume/evidence.json)。

## 复现

```powershell
.venv\Scripts\python.exe scripts/test_backend_boundaries.py --stage delivery-fix/final
.venv\Scripts\python.exe scripts/audit_backend_boundaries.py --stage delivery-fix/final
$env:BOUNDARY_UI_STAGE='delivery-fix/final'
npm run test:ui:boundaries
```

浏览器检查状态、步骤、受阻说明、工具次数和导出正文。BOUNDARY_UI_STAGE指定场景目录，报告写入其ui目录；旧恢复会话必须存在于当前数据库。

`scripts/verify_delivery_recovery.py` 依赖原verification会话，已完成时会拒绝覆盖首次恢复材料。新环境应先生成新的失败运行，再准备恢复检查。`scripts/probe_delivery_context.py --rounds 2` 可以重做上下文探测。

这项修复针对已复现的空返回问题，不能保证任意模型与提示都正确选工具。未增加媒体生成、发布或付款能力。
