# 输入与控制边界

这组测试通过HTTP提交自然语言，再检查数据库状态、模型调用和MCP结果。重点是含糊输入、错误授权、需求撤回与执行中的控制。

## 输入测试

| 输入类型 | 预期及结果 | 材料 |
| --- | --- | --- |
| 预算未定、尽快完成 | 不补造金额和时刻，继续追问 | [模糊需求](../examples/backend-boundaries/verification/01-vague/evidence.json) |
| 只有日期、预算10或20、负数、Infinity | 不任选范围、不补截止时刻、不保存非法预算 | [日期](../examples/backend-boundaries/verification/03-date-only/evidence.json)、[区间](../examples/backend-boundaries/verification/04-range/evidence.json)、[负数](../examples/backend-boundaries/verification/05-negative-budget/evidence.json)、[无限值](../examples/backend-boundaries/verification/06-infinite-budget/evidence.json) |
| 开始晚于截止，或撤回旧预算与日期 | 拒绝错误排期，清空撤回字段，旧计划失效 | [反向排期](../examples/backend-boundaries/verification/07-reversed-dates/evidence.json)、[撤回](../examples/backend-boundaries/verification/08-withdraw-brief/evidence.json) |
| 差不多、引用开始、条件句 | 不触发执行 | [模糊确认](../examples/backend-boundaries/verification/09-ambiguous-ack/evidence.json)、[引用](../examples/backend-boundaries/verification/10-quoted-command/evidence.json)、[条件](../examples/backend-boundaries/verification/11-conditional/evidence.json) |
| 伪造保存参数、要求生成成片/发布/付款 | 拒绝越权，说明能力缺口 | [绕过保存](../examples/backend-boundaries/verification/12-bypass-save/evidence.json)、[能力边界](../examples/backend-boundaries/verification/13-unsupported/evidence.json) |
| 强制真实来源但无搜索配置 | 不合成冒充完成 | [中文要求](../examples/backend-boundaries/verification/14-real-only-implicit/evidence.json)、[英文要求](../examples/backend-boundaries/verification/15-real-only-english/evidence.json) |
| 预算0、7.999，工期仅1小时 | 0不是缺项；成本8的缺口为0.001；工期不足不保存 | [零预算](../examples/backend-boundaries/verification/16-zero-budget/evidence.json)、[小数](../examples/backend-boundaries/verification/17-decimal-budget/evidence.json)、[短工期](../examples/backend-boundaries/verification/18-short-schedule/evidence.json) |
| 中文数字、上午/下午时间 | 解析金额和北京时间 | [中文输入](../examples/backend-boundaries/verification/19-chinese-times/evidence.json) |
| 修改后携带旧版授权 | 新版仍等待确认 | [旧版授权](../examples/backend-boundaries/verification/21-stale-plan-approval/evidence.json) |
| 工具执行中修改、停止再续跑 | 工具结束后处理，保留产物与运行标识 | [执行中修改](../examples/backend-boundaries/verification/22-change-during-tool/evidence.json)、[停止续跑](../examples/backend-boundaries/verification/23-stop-resume/evidence.json) |

金额使用Decimal；模型提取字段还要与原文核对。写作数量、渠道和格式随需求原文固定到运行中，不能只保留主题、预算和日期。撤回字段后，旧预览不能继续使用。

## API与工具

[API记录](../examples/backend-boundaries/verification/api-boundaries.json)包含空白、超长、缺标识、错误类型、伪造状态和无效游标。非法载荷返回422，未知对象404；首次提交202，同ID同内容200，同ID不同内容409。前端保留发送失败的草稿，不把错误对象直接显示为字符串。

[审计脚本](../scripts/audit_backend_boundaries.py)核对模型实际开放工具、原生调用、派发参数、成功结果、依赖、费用及保存前置条件。守卫拒绝但尚未派发时，不生成假的MCP失败记录。

## 发现的问题

需求提取曾补造日期、沿用撤回字段，也曾把条件句当成执行。修复增加原文证据校验、字段失效和保守授权检查。能力缺口单独进入待确认项，不能把真实成片需求自动改成文字并称已完成。

未完成回复原来会笼统声称素材与脚本已保留，改为读取实际库存逐项说明。后续未执行步骤标受阻，已有失败和成功分别保留。

交付阶段还有两条路径没有完成：三条文字脚本与停止续跑。模型读取Skill后连续空返回，已有素材、脚本和估算仍在，但没有完成校验或保存。这两条失败记录保留在[三条脚本](../examples/backend-boundaries/verification/20-video-script-supported/evidence.json)和[停止续跑](../examples/backend-boundaries/verification/23-stop-resume/evidence.json)。修复与同运行恢复见[交付工具调用](08-delivery-tool-fix.md)。

## 运行

使用本地Qwen配置并按README启动服务：

```powershell
.venv\Scripts\python.exe scripts/test_backend_boundaries.py --stage delivery-fix/final
.venv\Scripts\python.exe scripts/audit_backend_boundaries.py --stage delivery-fix/final
.venv\Scripts\python.exe scripts/test_mcp_boundaries.py
$env:BOUNDARY_UI_STAGE='delivery-fix/final'
npm run test:ui:boundaries
```

最终完整矩阵34/34通过，见[索引](../examples/backend-boundaries/delivery-fix/final/index.json)。只运行不依赖原恢复会话的9项UI检查，可指定 `tests/backend-ui/boundaries.spec.ts`；另2项恢复检查需要对应数据库会话。

本组测试使用Ollama，真实Tavily/DeepSeek另见[外部测试](11-external-live-and-review.md)。它没有承担多机压力或全部网络故障验证。
