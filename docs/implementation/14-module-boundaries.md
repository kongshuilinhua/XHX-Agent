# 模块边界

本文档定义 xhx-agent 的模块职责、依赖方向和禁止依赖。实现时先满足边界，再考虑内部优化。

## 总体依赖方向

```text
cli / tui
  -> runtime
    -> agent_core / graph
    -> models
    -> tools
    -> safety
    -> context
    -> evidence
    -> verification
    -> planner
    -> repo_intel
    -> skills
```

原则：

- `runtime` 是装配层，可以依赖多数模块。
- 底层模块不能反向依赖 `runtime`、`cli` 或 `tui`。
- `tui` 只消费事件和状态，不直接执行工具或调用模型。
- `tools` 只返回结构化结果，不自己写 Evidence Runtime。

## models

类似 pi 的 `pi-ai`。

负责：

- OpenAI-compatible 请求。
- 流式响应归一化。
- mock model。
- 模型 profile 到请求参数的转换。

不负责：

- CLI 参数。
- 工具执行。
- session 保存。
- 上下文选择。
- TUI 渲染。

输入：

- `ModelProfile`
- `ModelRequest`
- `ModelMessage[]`
- `ToolSpec[]`

输出：

- `ModelEvent` 流。
- 最终 `AssistantMessage`。
- token usage 摘要。

允许依赖：

- 标准库。
- HTTP client。
- `models.types`。

禁止依赖：

- `runtime`
- `tools`
- `tui`
- `evidence`
- `planner`

v0.1 先实现：

- OpenAI-compatible chat/completions 或 responses-like 兼容封装。
- mock model。
- text delta 和 tool call 事件的最小统一格式。

后续扩展：

- provider matrix。
- 多模态能力标记。
- usage 和 cost 归一化。

## agent_core / graph

类似 pi 的 `pi-agent-core`。

负责：

- 通用 Agent loop。
- 模型消息、工具调用、工具结果循环。
- Agent 生命周期事件。
- sequential / parallel 工具执行策略的调度接口。

不负责：

- 文件系统工具实现。
- 命令风险判断。
- session JSONL 保存。
- TUI 渲染。
- provider SDK 差异。

输入：

- `AgentState`
- `ModelClient`
- `ToolRegistry`
- `AgentMessage[]`
- `ExecutionPolicy` 接口。

输出：

- `AgentEvent` 流。
- 新增 `AgentMessage[]`。
- `RunResult`。

允许依赖：

- `models` 的公开协议。
- `tools` 的公开协议。
- `planner.modes` 的枚举。

禁止依赖：

- `cli`
- `tui`
- `runtime.app`
- 具体工具实现细节。

v0.1 先实现：

- 线性图：classify、gather、plan、patch、verify、summarize。
- 单工具调用循环。
- 事件流：run_start、model_delta、tool_start、tool_end、run_end。

后续扩展：

- DAG 调度。
- read-only 并行。
- write serialization by file。
- reviewer gate。

## runtime

类似 pi 的 `pi-coding-agent`。

负责：

- 装配配置、模型、工具、安全、上下文、证据、验证、session 和 skills。
- 管理一次任务运行。
- 把工具结果写入 Evidence Runtime。
- 把 AgentEvent 转换为 CLI/TUI 可消费状态。

不负责：

- provider 协议细节。
- TUI 底层渲染。
- 具体 parser 或 symbol index 细节。

输入：

- 用户任务。
- 当前 cwd。
- profile 名称。
- 命令行参数。

输出：

- `RunResult`
- Markdown 总结。
- JSON 结果。
- `.xhx/` 持久化文件。

允许依赖：

- 所有核心模块的公开接口。

禁止依赖：

- 模块内部私有函数。
- UI 组件内部状态。

v0.1 先实现：

- `RuntimeApp.run_task()`。
- `RuntimeApp.chat_once()`。
- `.xhx/` 初始化。
- Markdown 总结写入。

后续扩展：

- session resume。
- headless JSON。
- replay。
- RPC。

## tools

负责：

- 定义工具协议。
- 提供 `read_file`、`search`、`terminal`、`apply_patch`。
- 返回结构化 `ToolResult`。

不负责：

- 判断命令是否允许。
- 写 Evidence Runtime。
- 自动选择验证命令。
- 直接调用模型。

输入：

- `ToolCall`
- 工作区路径。
- tool-specific 参数。

输出：

- `ToolResult`
- changed files 摘要。
- stdout/stderr 摘要。

允许依赖：

- `safety` 的只读类型定义。
- 标准库。

禁止依赖：

- `models`
- `planner`
- `tui`
- `runtime.app`

v0.1 先实现：

- 文件大小限制的 `read_file`。
- 基于 `rg` 的 `search`。
- 受 policy 包装调用的 `terminal`。
- 结构化 `apply_patch`。

后续扩展：

- `code_outline`
- `symbol_search`
- `diagnostics`

## safety

负责：

- policy check。
- command risk classify。
- checkpoint。
- repair stop condition。
- 权限确认状态。

不负责：

- 执行 shell。
- 调用模型。
- 渲染确认 UI。
- 写入 Evidence Runtime。

输入：

- `ToolCall`
- 当前 mode。
- cwd。
- changed files。

输出：

- `PolicyDecision`
- `RiskLevel`
- checkpoint 摘要。

允许依赖：

- `tools.types`

禁止依赖：

- `models`
- `tui`
- `agent_core`

v0.1 先实现：

- safe / confirm / deny 分类。
- 禁止明显危险命令。

后续扩展：

- Git checkpoint。
- repair loop 限制。
- 敏感文件策略。

## context

负责：

- Context Pack 编译。
- token budget。
- 证据摘要选择。
- 历史摘要选择。

不负责：

- 执行工具。
- 读取 Raw Trace 全量内容。
- 写文件。
- 规划任务。

输入：

- 用户任务。
- `XHX.md` 摘要。
- 当前计划。
- changed files。
- Evidence Index 摘要。
- 最近失败摘要。

输出：

- `ContextPack`
- context debug report。

允许依赖：

- `evidence` 的索引查询接口。
- `repo_intel` 的摘要接口。

禁止依赖：

- `tools.terminal`
- `models.openai_compatible`
- `tui`

v0.1 先实现：

- 简单 prompt context 拼装。

后续扩展：

- top-k evidence。
- budget 裁剪。
- 历史压缩。

## evidence

负责：

- Raw Trace JSONL。
- Evidence Index JSONL。
- TrailGraph 内部表示。
- Markdown 审计报告渲染。

不负责：

- 决定任务计划。
- 执行工具。
- 判断命令风险。
- 选择上下文。

输入：

- tool execution event。
- model event 摘要。
- decision event。
- verification result。

输出：

- trace entry。
- evidence entry。
- report markdown。

允许依赖：

- 公开事件类型。

禁止依赖：

- `models` provider 实现。
- `tools` 具体实现。
- `tui`

v0.1 先实现：

- Markdown 总结。
- 轻量 session/task summary。

后续扩展：

- Raw Trace。
- Evidence Index。
- TrailGraph。

## verification

负责：

- 根据项目结构和 changed files 推荐验证命令。
- 输出验证命令风险和跳过原因。
- 解析验证结果摘要。

不负责：

- 执行命令。
- 修改代码。
- repair patch。

输入：

- changed files。
- 项目文件列表。
- `package.json` / `pyproject.toml` 摘要。
- 用户指定命令。

输出：

- `VerificationPlan`
- `VerificationResultSummary`

允许依赖：

- `repo_intel` 的项目扫描结果。
- `safety` 的风险等级类型。

禁止依赖：

- `tools.terminal` 具体执行。
- `models`
- `tui`

v0.1 先实现：

- Python / Node 验证推断。

后续扩展：

- 文档检查。
- 配置 smoke test。
- impact-aware test selection。

## planner

负责：

- 任务模式选择。
- 计划结构。
- reviewer gate 的策略接口。

不负责：

- 执行工具。
- 生成具体 patch。
- 保存证据。

输入：

- 用户任务。
- 上下文摘要。
- 风险摘要。
- 验证能力摘要。

输出：

- `ExecutionMode`
- `TaskPlan`
- `ReviewDecision`

允许依赖：

- `context` 输出类型。
- `verification` 摘要类型。

禁止依赖：

- `tools`
- `tui`
- `models` provider 实现。

v0.1 先实现：

- 简单模式分类。
- 线性计划。

后续扩展：

- DAG planner。
- reviewer quality gate。

## repo_intel

负责：

- 项目扫描。
- `XHX.md` 生成。
- repo map。
- symbol index。
- impact analysis。

不负责：

- 执行修改。
- 调用模型。
- 安全审批。

输入：

- cwd。
- 文件树。
- 项目配置文件。

输出：

- `ProjectScan`
- `RepoMap`
- `SymbolIndex`
- `ImpactSummary`

允许依赖：

- 标准库。
- Tree-sitter。
- SQLite。

禁止依赖：

- `models`
- `tui`
- `agent_core`

v0.1 先实现：

- 项目扫描。
- `XHX.md`。

后续扩展：

- Tree-sitter。
- SQLite symbol index。
- impact graph。

## tui

类似 pi 的 `pi-tui`。

负责：

- 终端渲染。
- 输入框。
- `/` 命令面板。
- 工具状态展示。
- 权限确认 UI。
- diff / evidence / context 摘要展示。

不负责：

- 调用模型。
- 执行工具。
- 保存 session。
- 判断安全策略。

输入：

- Runtime event。
- 用户键盘输入。
- 当前 view state。

输出：

- 用户命令。
- 用户确认。
- 渲染帧。

允许依赖：

- `runtime` 公开事件类型。
- `cli.commands` 的命令定义。

禁止依赖：

- `models`
- `tools`
- `evidence` 写入接口。

v0.1 先实现：

- 不实现完整 TUI，只保留 CLI/REPL。

v0.5 实现：

- Claude Code 风格 TUI。
- `/` 命令系统。

## skills

负责：

- Skill metadata。
- trigger 匹配。
- progressive disclosure。
- hook 定义。
- MCP 工具注册适配。

不负责：

- 提升权限。
- 绕过 `apply_patch`。
- 直接执行工具。

输入：

- `.xhx/skills/<name>/SKILL.md`
- 当前任务。
- 当前 mode。

输出：

- `SkillSummary`
- `LoadedSkill`
- hook result。

允许依赖：

- `safety` 权限类型。
- `context` hook 类型。

禁止依赖：

- `tools` 具体实现。
- `models` provider 实现。
- `tui`

v0.1 先实现：

- 不实现完整 Skill 系统。

v0.8 实现：

- metadata loader。
- trigger。
- hook。
- MCP client 可选接入。
