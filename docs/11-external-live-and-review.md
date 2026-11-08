# DeepSeek与Tavily测试

使用DeepSeek `deepseek-chat`、`https://api.deepseek.com` 和Tavily官方远程MCP。Tavily经项目HTTP代理 `http://127.0.0.1:7890` 连接，TLS校验开启。

8组场景及事实审计通过，另外保存了提供方边界与浏览器测试。模型ID按实际请求记录，未推断服务端映射。

## 单次故障与预算调整

[核心用例](../examples/external-live/after/audit-auto-failure-replan/evidence.json)输入预算10、初始premium、必须真实来源。只发送需求和开始两条消息，没有人工续跑：

```text
检索成功
  → 脚本失败一次
  → 新尝试成功
  → 估算12，预算缺口2
  → 模型选择simple，建立新计划
  → 重新估算8
  → 检查通过
  → 保存一次
```

成本按明细计算：premium为策划1＋文案2＋视觉9＝12；simple为1＋2＋5＝8。预算始终为10。工具模拟费0.55另列，失败尝试模拟费为0。

素材只检索一次。脚本成功后，预算重规划继续使用同一script_id和正文。第一次脚本操作仍为failed，第二次是新的操作ID、attempt=2。旧计划为superseded，未执行的检查与保存没有变成成功。

[27项审计](../examples/external-live/after/review-audit.json)检查Decimal加总、依赖、操作顺序、版本、内容指纹、排期和实际保存。[最终方案](../examples/external-live/after/audit-auto-failure-replan/proposal-v1.md)与[页面截图](../examples/external-live/after/ui/screenshots/auto-failure-replan.png)可直接查看。保存方案v1来自计划v2，两种版本分别计数。

## 其他场景

| 场景 | 结果 | 记录 |
| --- | --- | --- |
| 模糊需求 | 追问预算和截止时间，无业务调用 | [vague](../examples/external-live/after/deepseek-vague/evidence.json) |
| 重规划后再次开始 | 12→8保存，重复指令不新增业务调用 | [repeat](../examples/external-live/after/combined-replan-repeat/evidence.json) |
| 故障后聊天续跑 | 同run恢复，保留真实素材和失败尝试 | [resume](../examples/external-live/after/combined-failure-resume/evidence.json) |
| 严格来源，客户端超时 | 不合成、不保存，说明未完成 | [strict](../examples/external-live/after/tavily-timeout-strict/evidence.json) |
| 允许来源降级，客户端超时 | 改用合成并说明来源，失败记录保留 | [fallback](../examples/external-live/after/tavily-timeout-fallback/evidence.json) |
| 搜索缓存与参数 | 一次真实请求，第二次缓存；非法参数派发前拒绝 | [cache](../examples/external-live/after/real-tavily-cache-guards/evidence.json) |
| 预算5 | 最低已核验成本8，说明缺口3，不保存 | [low-budget](../examples/external-live/after/deepseek-low-budget/evidence.json) |

超时由测试将真实端点RPC客户端时限设为1ms触发，不是Tavily实际发生服务中断。请求是否已被远程服务接收或计费可能未知。

## 提供方与页面

| 检查 | 结果 | 材料 |
| --- | --- | --- |
| DeepSeek原生工具、结果回传、流式、错误模型/密钥、截断与用量 | 8项通过 | [记录](../examples/external-live/after/provider/evidence.json) |
| DeepSeek真实流截断 | 3项通过，保留部分草稿与用量，不报成功 | [记录](../examples/external-live/after/provider-stream-truncation.json) |
| Tavily中文/emoji、非法域名、无结果、错误密钥 | 4项通过 | [记录](../examples/external-live/after/tavily/evidence.json) |
| 场景事实审计 | 8组通过 | [审计](../examples/external-live/after/audit.json) |
| 外部场景页面 | 6项通过 | [报告](../examples/external-live/after/ui/report.json) |
| 既有功能页面回归 | 4项通过 | [报告](../examples/backend-completion/ui/report.json) |

缓存组不调用LLM；其余7组使用真实DeepSeek。提供方工具回传测试中的echo是本地探测工具，不是业务MCP。完整后端回归170项，见[JUnit](../examples/external-live/backend.xml)。

## 接入中发现的问题

DeepSeek设置JSON模式还不足以满足结构要求，最初返回缺字段或错误类型。适配器增加完整schema提示与本地校验，[原失败](../examples/external-live/before/initial-graph/combined-replan-repeat/evidence.json)保留。

Tavily可能以HTTP200、is_error=false携带业务400。适配器现在读取error/status/detail，和真正的空结果区分；HTTP鉴权状态通过响应钩子保留，不再被压成通用MCP错误。

[网络诊断](../examples/external-live/network-tls.json)中，直连三个地址有两个TLS重置。更换事件循环或握手方式没有解决；使用项目代理后，[三次工具发现](../examples/external-live/transport-proxy.json)通过。连接错误最多显式尝试三次，不隐式重试搜索RPC，401/403/429不会作为连接错误重试。

低预算用例还暴露了无意义升级和最终缺口丢失：[修复前记录](../examples/external-live/before/low-budget-review/evidence.json)。过滤不能改善约束的候选、移除估算上下文中的旧规格，并在终止时保留缺口后，4轮停止并报告缺口3。

页面曾把旧失败记录也标成复用结果。现在失败显示保留失败记录，只有成功产物显示复用，[原截图](../examples/external-live/before/failed-card-label.png)留存。

## 运行

需要PostgreSQL、实际API密钥及可用网络，会使用账户额度。代理地址按自己的环境配置。

```powershell
$env:PYTHONPATH='src/backend'
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe scripts/test_external_boundaries.py --only provider
.venv\Scripts\python.exe scripts/test_external_boundaries.py --only tavily
.venv\Scripts\python.exe scripts/test_external_boundaries.py --only stream-truncation
.venv\Scripts\python.exe scripts/test_external_boundaries.py
.venv\Scripts\python.exe scripts/audit_backend_completion.py --root examples/external-live/after
.venv\Scripts\python.exe scripts/audit_external_review.py
npx playwright test --config playwright.external.config.ts
```

场景脚本使用独立进程、队列和故障配置，不修改全局故障开关。支持 `--only auto-failure-replan` 定向执行。全部场景完成后再审计；浏览器需要默认8000/5173服务和对应会话。

模型/搜索供应商单价没有配置，实际账单仍未知。工具模拟费不是供应商账单，文案事实也没有因此完成全面核验。
