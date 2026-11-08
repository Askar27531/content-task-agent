# 交付清单

## 文件

- [README](../README.md)：安装、启动、模型配置与评审入口。
- [src](../src/backend/app/graphs.py)：前后端、模型适配和MCP工具。
- [tests](../tests/backend/test_external_configuration.py)：可执行后端与浏览器测试。
- [data](../data/production-rules.json)：合成素材、成本和排期规则。
- [examples](../examples/README.md)：输入、实际输出、Trace与测试结果。
- [需求](01-requirements.md)、[设计](02-solution.md)、[测试](03-evaluation.md)、[AI使用](04-ai-usage.md)、[复盘](05-retrospective.md)。
- [Python依赖锁](../requirements.lock.txt)、[npm锁](../package-lock.json)、[环境变量模板](../.env.example)、[YAML模板](../config/app.example.yaml)。

框架版本和职责在设计文档中。AI使用说明区分个人主导的修改与AI辅助工作。

## 验证材料

[五组案例](../examples/README.md)覆盖正常完成、缺预算/截止时间、工具失败、预算超限、重复执行，以及失败续跑和不可行缺口。每组包含原始输入、输出和运行记录。

[核心审计](../examples/external-live/after/review-audit.json)有27项检查：实际12→8、一次故障、新尝试、依赖、成功产物保留、费用和保存记录。后端170项回归及8组外部场景有独立报告。

## 提交前检查

```powershell
python scripts/check_delivery.py
python scripts/audit_external_review.py
```

交付检查21项通过，[结果](../examples/submission/delivery-audit.json)保存在examples中。脚本核对文件、案例字段、文档链接、版本锁、忽略规则、单文件大小，以及当前配置密钥是否出现在候选文件或ZIP成员中。它只使用Python标准库和Git，不调用模型或数据库；密钥检查范围限于当前已配置值。

提交时排除 `.env`、本地YAML、`.runtime`、依赖目录和构建产物，保留示例模板和验证材料。若手工打包目录，需要自行按忽略规则筛选。

题目为“内容任务规划 Agent”，公开仓库为[Askar27531/content-task-agent](https://github.com/Askar27531/content-task-agent)，默认分支main。最终commit SHA可通过`git rev-parse HEAD`或[提交列表](https://github.com/Askar27531/content-task-agent/commits/main)核查，远端CI见[Actions](https://github.com/Askar27531/content-task-agent/actions/workflows/check.yml)。

离线交付检查不访问GitHub，报告中的远端验证字段只表示该脚本未执行网络核验。仓库可见性、提交与CI状态以远端记录为准。
