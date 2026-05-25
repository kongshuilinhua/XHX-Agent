# 测试与评测

xhx-agent 既需要确定性代码测试，也需要针对 Agent 行为的评测场景。完整项目必须同时具备这两类验证。

## 单元测试

覆盖：

- 配置加载。
- 模型 profile 解析。
- 命令风险分类。
- 验证命令推断。
- `apply_patch` 解析和 dry-run 行为。
- Context Pack 预算裁剪。
- Evidence Runtime 序列化。
- Skill metadata 解析。

## 集成测试

覆盖：

- Python 项目读改测流程。
- JavaScript/TypeScript 项目读改建流程。
- 验证失败和 repair loop。
- Skill 触发和加载。
- JSONL session resume。
- Evidence Runtime 到 Markdown 日志的渲染。
- Safe Execution Kernel 的 confirm / deny / execute 分支。
- TUI `/` 命令解析和状态渲染。

## 端到端场景

维护小型 fixture 仓库，用于测试：

- 修复 Python 函数 bug。
- 修复 JavaScript build error。
- 根据 README 增加一个小功能。
- 只分析测试失败原因，不修改文件。
- 生成 `XHX.md`。
- 验证大日志不会完整进入 Prompt。
- 在 TUI 中完成一次读改测任务。

## 指标

每次运行记录：

- `task_success`
- `verification_passed`
- `patch_count`
- `repair_rounds`
- `command_count`
- `context_budget_used`
- `context_items_selected`
- `evidence_coverage`
- `policy_decision_count`
- `verification_route`
- `user_confirmation_count`
- `tui_command_count`
- `model_name`
- `tokens_in`
- `tokens_out`

## 评测报告

评测报告应包含：

- 场景名称。
- 模型 profile。
- 策略 profile。
- 执行模式。
- 成功或失败。
- 验证结果。
- 上下文预算使用情况。
- 证据覆盖率。
- 失败原因。

## v1.0 验收标准

- 示例 Python 任务成功率不低于 80%。
- 示例 JavaScript/TypeScript 任务成功率不低于 70%。
- 每个修改型任务都创建 Evidence Runtime 输出。
- 每个失败任务都有明确失败原因。
- 测试套件可以从干净 checkout 运行。
