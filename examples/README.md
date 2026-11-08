# 案例与测试结果

先看下面五组。它们使用DeepSeek、PostgreSQL、LangGraph和业务MCP，需要真实来源的用例连接Tavily。

| 案例 | 内容 | 材料 |
| --- | --- | --- |
| 正常完成与重复执行 | 预算10，12→8后保存，再次开始不重复调用 | [输入/输出/Trace](external-live/after/combined-replan-repeat/evidence.json)、[方案](external-live/after/combined-replan-repeat/proposal-v1.md) |
| 缺信息 | 模糊预算与截止时间，追问且不执行 | [输入/输出/Trace](external-live/after/deepseek-vague/evidence.json) |
| 工具失败后续跑 | 素材保留，同run重试脚本并保存 | [输入/输出/Trace](external-live/after/combined-failure-resume/evidence.json)、[方案](external-live/after/combined-failure-resume/proposal-v1.md) |
| 自动恢复与重规划 | 脚本失败一次后自动新尝试，12→8，复用成功产物 | [输入/输出/Trace](external-live/after/audit-auto-failure-replan/evidence.json)、[方案](external-live/after/audit-auto-failure-replan/proposal-v1.md)、[审计](external-live/after/review-audit.json) |
| 没有可行方案 | 预算5、成本8，未完成并说明缺口3，不保存 | [输入/输出/Trace](external-live/after/deepseek-low-budget/evidence.json) |

## 读取记录

evidence.json中的inputs是输入及受理信息，workspace/state是返回与持久化状态。tool_calls包含工具输入输出，state.model_calls、events、jobs记录模型和任务过程。checks是用例断言。保存方案另外提供Markdown和JSON。

机器可读入口是[manifest.json](submission/manifest.json)。全部8组外部场景见[index.json](external-live/after/index.json)，[后端JUnit](external-live/backend.xml)和[浏览器报告](external-live/after/ui/report.json)也已保存。

## 其他目录

- `live/`：本地Qwen的缺项、预算调整、故障续跑和不可行案例。
- `backend-boundaries/`：自然语言、API、工具权限、控制与交付恢复。
- `backend-completion/`：费用、配置、复用、进程退出与事件重放。
- `new-feature-boundaries/`：缓存、并发、口语修改和SSE组合边界。
- `external-live/`：DeepSeek、Tavily实际接口与场景。
- `submission/`：交付索引和静态检查结果。

before、debug目录保留失败或诊断结果；协议夹具、/demo和真实服务调用分别标注。不要把失败文件改成最终成功输出。

静态文件可以离线阅读。打开其中的会话ID或重跑浏览器测试，需要对应数据库；新环境先运行相关场景脚本生成会话。命令见[测试说明](../docs/03-evaluation.md)和各专项报告。
