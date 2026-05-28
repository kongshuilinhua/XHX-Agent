# 全版本任务拆分

本文档把 v0.1 到 v1.0 拆成可执行任务。每个版本都必须保持项目可运行，不允许只提交空架构。

## v0.1 最小 Agent Runtime

v0.1 只能拆成三个固定子阶段：

- v0.1-A：真实模型接入。
- v0.1-B：Tool-call loop。
- v0.1-C：验证闭环。

不得新增 `v0.1-D`、`v0.1-E` 等临时版本。具体进入条件、当前状态和纠偏记录见 [版本实施基线](07-implementation-baseline.md)。

功能任务：

- 实现 `xhx init`、`xhx chat`、`xhx run`。
- 支持 OpenAI-compatible profile 和 mock model。
- 支持 `read_file`、`search`、`terminal`、`apply_patch`。
- 生成 `XHX.md`。
- 推断 Python / Node 验证命令。
- 生成 Markdown 总结。

模块任务：

- `cli`：Typer 命令入口和简单 REPL。
- `runtime`：配置、profile、run orchestration。
- `models`：OpenAI-compatible + mock。
- `tools`：四个基础工具。
- `safety`：safe / confirm / deny。
- `verification`：Python / Node 推断。
- `evidence`：报告渲染。

测试任务：

- 单元测试覆盖配置、工具、安全、验证和报告。
- 集成测试覆盖 Python 和 Node fixture。

文档任务：

- README 增加最小运行示例。
- 标注 v0.1 当前能力和非目标。

## v0.2 Safe Execution Kernel

功能任务：

- 所有工具调用统一 policy check。
- Git checkpoint。
- changed files 追踪。
- 验证失败识别。
- 最多两轮 repair loop。
- 失败停止报告。

模块任务：

- `safety`：PolicyEngine、RiskClassifier、CheckpointManager。
- `runtime`：用户确认流程。
- `tools`：返回 changed files 和命令摘要。
- `evidence`：记录 policy decision 摘要。

测试任务：

- deny 命令。
- confirm 命令用户拒绝。
- repair 超限。
- checkpoint 记录。

文档任务：

- 更新工具安全示例。
- 增加危险命令说明。

## v0.3 Context Pack Compiler

功能任务：

- Context Pack 数据结构。
- token budget。
- top-k evidence selection。
- changed files selection。
- recent failure selection。
- history summary。
- context debug report。

模块任务：

- `context`：Compiler、Budget、Selectors。
- `runtime`：每次模型调用前编译 Context Pack。
- `evidence`：提供 Evidence Index 查询接口。

测试任务：

- 大日志不进入 Prompt。
- 超预算裁剪。
- 调试和文档任务使用不同预算模板。

文档任务：

- 增加 Context Pack 示例。
- 说明完整日志和 Prompt 的边界。

## v0.4 Evidence Runtime

功能任务：

- Raw Trace JSONL。
- Evidence Index JSONL。
- TrailGraph 内部模型。
- patch 绑定 evidence id。
- artifact_ref 按需展开。
- Markdown 审计报告。

模块任务：

- `evidence`：TraceWriter、EvidenceIndex、TrailGraph、ReportRenderer。
- `runtime`：包装所有工具调用并写 evidence。
- `context`：只读取 evidence 摘要。

测试任务：

- 每次工具调用生成 Raw Trace。
- 每次重要观察生成 Evidence Index。
- 修改无 evidence 时失败。
- 报告可追踪修改依据。

文档任务：

- 更新 Evidence Runtime 示例。
- 明确 TrailGraph 是内部表示。

## v0.5 TUI / Command Console

功能任务：

- Claude Code 风格终端交互窗口。
- 流式输出。
- 工具状态展示。
- 权限确认 UI。
- 当前计划、验证状态、repair 状态展示。
- `/` 命令系统。
- 阶段边界取消。
- 最多两轮手动 repair loop。

模块任务：

- `tui`：terminal abstraction、component、input、render loop。
- `tui.page`：把 ConsoleState 渲染成可复用 Rich 终端页面，先服务 `/dashboard`，后续可迁移到 Textual。
- `tui.live`：封装 Rich Live 生命周期，按 RuntimeEvent 刷新固定区域仪表盘。
- `cli`：interactive mode 切换。
- `runtime`：发出 run、context、plan、policy、tool、verification、repair 和 report 事件。
- `models`：OpenAI-compatible SSE 增量解析，输出文本 delta 给 Runtime。
- `runtime`：在模型规划、工具执行和验证命令前检查取消请求并发出 cancel 事件。
- `tui.state`：把 RuntimeEvent 归约成 ConsoleState，供 Rich 控制台和后续 Textual TUI 复用。

测试任务：

- `/help`、`/model`、`/status`、`/plan`、`/context`、`/evidence`、`/diff` 解析。
- RuntimeEvent 到 ConsoleState 的 reducer 测试。
- OpenAI-compatible streaming delta 解析测试。
- Rich Live dashboard 渲染和 `/live` 命令测试。
- `/cancel` 命令、cancel event reducer 和取消状态渲染测试。
- `/repair loop` 两轮修复和验证测试。
- 权限确认同意和拒绝。
- fake terminal 渲染快照。

文档任务：

- TUI 使用指南。
- `/` 命令参考。

## v0.6 Repo Intelligence Graph

当前状态：部分实现。

已完成：

- 基础 `RepoMap`：文件路径、语言、类型、大小、验证提示。
- 基础 `SymbolIndex`：Python AST 符号提取，JavaScript / TypeScript 轻量符号提取。
- `symbol search`：exact、prefix、contains 排序。
- `context builder`：围绕符号生成带行号代码片段。
- `impact summary`：基础 Python / JavaScript / TypeScript 源文件到直接测试文件映射。
- 轻量 `import graph`：Python `import/from`、JavaScript / TypeScript `import` 和 `require()` 的一跳关系。
- impact fallback：direct test 命名匹配失败时，用 import graph 找直接或间接依赖变更源文件的测试。
- `repo intelligence index`：`xhx init` 写入 `.xhx/repo/index.json`，保存 repo map、symbol index 和 import graph。
- index reuse：Context Pack 和 Verification Router 优先复用 `.xhx/repo/index.json`，索引不可用时再即时构建。
- index refresh：成功 `apply_patch` 后刷新 `.xhx/repo/index.json`，并基于刷新后的索引重新推断验证命令。
- `XHX.md` 输出 Repo Map 和 Symbols 摘要。
- Context Pack 按任务文本选择少量 symbol context，并继续受 token budget 裁剪。
- Verification Router 使用 impact summary 选择 targeted pytest。

未完成：

- Tree-sitter。
- SQLite 索引；当前只有 JSON 格式的 `.xhx/repo/index.json`。
- 完整调用图 / 引用图。
- test runner 参数和跨语言影响面分析。
- 更强的 Context Pack 查询策略，例如调用图、引用图或语义检索。

功能任务：

- repo map。
- Tree-sitter 符号提取。
- symbol search。
- context builder。
- SQLite 可选索引。
- impact analysis。

模块任务：

- `repo_intel`：Scanner、RepoMap、SymbolIndex、ImpactAnalyzer。
- `verification`：使用 impact summary 选择更小验证命令。
- `context`：使用 symbol context。

测试任务：

- Python 符号提取。
- TypeScript 符号提取。
- 按函数定位上下文。
- impact 影响验证路由。

文档任务：

- Repo Intelligence 设计和限制。

## v0.7 Adaptive Planner + DAG

功能任务：

- mode classifier。
- DAG planner。
- topological scheduler。
- read-only 并行。
- 同文件写入串行。
- Reviewer quality gate。
- re-plan / re-execute。

模块任务：

- `planner`：ModeClassifier、DAGPlanner、Reviewer。
- `agent_core` / `graph`：DAG execution。
- `runtime`：并发控制和事件汇总。

测试任务：

- 简单任务不进入 DAG。
- 只读任务并行。
- 同文件写入串行。
- Reviewer 要求补证据。

文档任务：

- DAG 执行示例。
- Reviewer gate 规则。

## v0.8 Skills / Extensions / MCP

功能任务：

- Skill metadata loader。
- trigger matching。
- progressive disclosure。
- hooks。
- MCP client 可选接入。
- MCP permission registration。

模块任务：

- `skills`：Loader、Metadata、HookRunner、MCPAdapter。
- `runtime`：hook 调度。
- `safety`：插件权限约束。
- `context`：Skill 内容按需进入 Context Pack。

测试任务：

- 未命中 Skill 不加载正文。
- 命中 Skill 加载正文。
- Skill 不能提升权限。
- MCP 工具结果写 Evidence Runtime。

文档任务：

- Skill 编写指南。
- MCP 安全边界。

## v0.9 Evaluation / Headless / Replay

功能任务：

- `xhx run "<task>" --json` 完整结构化输出。
- JSONL RPC。
- Trail replay。
- benchmark fixture。
- metrics collection。
- Markdown check report。

模块任务：

- `evals`：Runner、Replay、Metrics。
- `runtime`：headless result。
- `evidence`：replay 读取。
- `cli`：JSON / RPC mode。

测试任务：

- replay 不重新执行工具。
- benchmark 对比 profile。
- CI 只读任务。
- 失败分类。

文档任务：

- Headless 使用指南。
- Benchmark 指南。

## v1.0 完整稳定版

功能任务：

- 稳定 CLI/REPL/TUI。
- 稳定 OpenAI-compatible profile。
- 稳定读改测闭环。
- 完整 Runtime 内核。
- 完整示例和测试。

模块任务：

- 梳理 public API。
- 清理实验性接口。
- 完善错误处理。
- 补全安装、配置、调试文档。

测试任务：

- 从干净 checkout 安装。
- Python fixture 成功率不低于 80%。
- JS/TS fixture 成功率不低于 70%。
- 修改型任务 100% 有 Evidence Runtime 记录。
- 失败任务 100% 有失败原因。

文档任务：

- README 当前能力更新。
- 完整安装指南。
- 示例任务教程。
- 故障排查指南。
