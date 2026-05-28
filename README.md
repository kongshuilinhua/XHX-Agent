# xhx-agent

xhx-agent 是一个上下文预算驱动、证据可追溯、执行可控的本地编码 Agent。它面向真实代码仓库，负责读取项目、规划任务、执行安全补丁、运行验证命令，并输出可审计的任务报告。

## 当前实现状态

当前代码按 [版本实施基线](docs/implementation/07-implementation-baseline.md) 推进。当前处于 **v0.5 TUI / Command Console**：默认 mock profile 可以离线跑通 read/search、apply_patch、验证推断、交互确认 / `--yes` 执行、checkpoint、只读 restore plan、policy trace、Raw Trace、Evidence Index 和 Markdown 报告；同时已有真实模型计划解析、工具结果反馈循环、dry-run 入口、原子化 `apply_patch` 写入内核、SafeExecutionKernel 边界、最多两轮的可选 repair loop、预算化上下文选择、context debug report、artifact_ref 展开、patch evidence id 绑定、Runtime 事件流、OpenAI-compatible SSE 模型增量事件、Rich Live 仪表盘、实验性 `--fullscreen` Textual 窗口后台任务执行、最小运行中 steer、`/model`、`/plan`、`/verify`、`/repair`、`/skills`、`/mode`、`/dashboard`、`/cancel`、`/live`、`/context`、`/evidence`、`/diff` 和一次性 `/allow` / `/deny` 权限确认、阶段边界取消、只读 `/diff` 摘要和 Rich 命令控制台。这些能力仍不等于完整自动编码 Agent，不要把当前版本理解为已经能在任意真实仓库中自动修代码。

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 项目文档与路线图 | 已实现 | README、架构文档、实施规格和测试计划已写入 `docs/`。 |
| `uv` Python 项目骨架 | 已实现 | 已提供 `xhx` / `xhx-agent` CLI 入口。 |
| `xhx init` | 已实现 | 创建 `.xhx/`、默认配置、profile 和 `XHX.md`。 |
| 项目扫描 | 已实现 | 可识别 Python、JavaScript、TypeScript 的基础项目特征。 |
| 命令风险分类 | 已实现 | 支持 safe / confirm / deny 的基础分类。 |
| v0.1-A 真实模型接入 | 部分实现 | 已有 OpenAI-compatible Chat Completions 请求、SSE 流式增量解析、API key 环境变量读取、mock model、JSON plan 解析和结构化错误诊断；provider response format 仍未完成。 |
| v0.1-B Tool-call loop | 部分实现 | 已有 Tool Registry、read/search/apply_patch、工具结果反馈循环、patch evidence、tool policy decision 和原子化 patch；patch 尚未强制绑定具体 evidence id。 |
| v0.1-C 验证闭环 | 基本完成 | 已有 Python/Node 验证推断、交互确认 / `--yes` 执行、验证 trace/evidence、只读任务跳过验证、失败停止和命令输出摘要；更细的源码文件到测试文件映射留到后续增强。 |
| v0.2 Safe Execution Kernel | 基本完成 | 已有 SafeExecutionKernel、checkpoint、失败后的只读 restore plan、tool/terminal policy decision trace/evidence、changed files 追踪和 `--auto-repair` 最多两轮 repair loop；尚未实现自动回滚，patch 也尚未强制绑定具体 evidence id。 |
| v0.3 Context Pack Compiler | 基本完成 | 每轮模型调用前生成预算化上下文包，支持 token budget、top-k evidence、changed files selection、recent failure selection 和 `.xhx/context/` debug report。 |
| 工具结果反馈循环 | 部分实现 | Runtime 可把上一轮工具结果摘要反馈给下一轮模型；repair loop 可复用该反馈机制，但 DAG 仍未实现。 |
| 真实模型计划诊断 | 部分实现 | 支持解析 JSON fenced block、分段 content 和 trailing prose，并在 JSON/Schema 错误时写入结构化诊断；仍依赖模型遵守计划协议。 |
| `apply_patch` 写入 | 部分实现 | 支持本项目定义的结构化 patch 子集，包含多 hunk、多文件、Add File、路径逃逸拒绝和失败不落盘；仍不是完整通用 patch 引擎。 |
| 工具注册与计划校验 | 已实现 | Runtime 通过 Tool Registry 执行工具，并在执行前拒绝未知工具和坏参数。 |
| v0.4 Evidence Runtime | 基本完成 | Raw Trace / Evidence Index 支持 JSONL 读回、artifact_ref 按需展开、patch evidence id 绑定和报告 evidence 渲染；复杂 TrailGraph 仍未实现。 |
| OpenAI-compatible 真实模型调用 | 部分实现 | 已有 Chat Completions 计划请求、SSE 模型增量事件、Context Pack 输入、API key 环境变量读取、结构化错误处理、`--dry-run` 计划预览和最多 4 轮工具反馈；仍要求模型按 JSON 计划协议返回工具步骤，不等于通用自动修代码。 |
| 通用自动修代码 | 未实现 | 当前不能保证自动修复任意仓库问题。 |
| v0.5 TUI / Command Console | 部分实现 | 已有 `xhx tui` / `xhx chat` Rich 命令控制台、`xhx tui --fullscreen` 实验性 Textual 窗口后台任务执行、全屏 `/help` / `/model` / `/status` / `/plan` / `/context` / `/evidence` / `/diff` / `/verify` / `/repair` / `/skills` / `/mode` / `/dashboard` / `/cancel` / `/live` / `/allow` / `/deny` / `/clear` / `/exit`、`/live` Rich Live 动态仪表盘、`/verify` 手动验证、`/repair` 默认一轮修复、`/repair loop` 最多两轮手动修复、`/diff` 只读 git diff 摘要、`/cancel` 阶段边界取消、follow-up 上下文包装、运行中输入排队为 steer、权限确认、Runtime 事件流、模型增量输出、ConsoleState 归约和状态摘要视图；运行中 steer 目前是排队并在安全边界后作为 follow-up 执行，不是强杀外部命令或完整交互式 repair。 |
| DAG 多 Agent 调度 | 未实现 | 计划在 v0.7 实现。 |
| Skill / MCP | 未实现 | 计划在 v0.8 实现。 |

后续开发不得新增未记录的小版本名。版本名称、进入条件和验收标准以 [版本实施基线](docs/implementation/07-implementation-baseline.md) 为准。

项目参考 HPD-Agent 的图调度思路、pi 的 Runtime/Skill 工程结构、aider 的仓库地图与补丁实践，以及 Cline、OpenHands、SWE-agent、Continue 等项目在上下文、安全、验证和评测上的经验。

核心目标不是“记录一切并塞进 Prompt”，而是构建一个 **Context-Budgeted Agent Runtime**：

- 完整轨迹写入磁盘。
- 证据摘要进入索引。
- 每轮只把少量高价值上下文编译进 Prompt。
- 所有执行都经过权限、补丁、验证和修复策略。

## 目标

- 构建一个基于 Python + LangGraph 的本地编码 Agent。
- 支持从用户需求到代码修改、验证、总结的完整闭环。
- 用上下文预算控制每轮进入模型的信息量，避免 token 爆炸。
- 用 Evidence Runtime 保留完整审计轨迹，并按需检索证据。
- 用 Safe Execution Kernel 管理工具权限、补丁、验证、回滚和修复。
- 从可用 CLI 逐步演进成完整多 Agent 项目。

## 核心概念

- **Context Pack Compiler**：每轮 LLM 调用前编译上下文包，按预算选择项目规则、当前计划、相关代码、证据摘要和最近错误。
- **Evidence Runtime**：完整记录 Raw Trace，维护 Evidence Index，只将少量 Context Evidence 放入 Prompt。
- **Safe Execution Kernel**：统一处理工具请求、风险分级、用户确认、补丁写入、验证和失败停止条件。
- **Adaptive Planner**：根据任务复杂度选择 direct、research-only、linear-edit、plan-review-act、dag-execute 或 repair-loop。
- **Verification Router**：根据变更类型和项目结构推断最小验证命令。
- **TrailGraph**：Evidence Runtime 的内部表示之一，用于组织任务、证据和决策关系。

## 版本规划

- **v0.1 最小 Agent Runtime**：CLI、模型配置、基础工具、`apply_patch`、验证推断和 Markdown 总结。
- **v0.2 Safe Execution Kernel**：命令权限、风险分级、checkpoint、changed files、验证失败处理。
- **v0.3 Context Pack Compiler**：上下文预算、项目地图、证据摘要选择、历史压缩。
- **v0.4 Evidence Runtime**：Raw Trace、Evidence Index、按需展开、审计报告。
- **v0.5 TUI / Command Console**：接近 Claude Code 的终端交互体验和 `/` 命令系统。
- **v0.6 Repo Intelligence Graph**：repo map、Tree-sitter、符号搜索、影响面分析。
- **v0.7 Adaptive Planner + DAG**：简单任务线性执行，复杂任务进入 DAG 和多 Agent 调度。
- **v0.8 Skills / Extensions / MCP**：轻量 Skill、Hook、可选 MCP 工具接入。
- **v0.9 Evaluation / Headless / Replay**：fixture 仓库、benchmark、JSON 输出、轨迹回放。
- **v1.0 完整 Agent 项目**：稳定 CLI、完整 Runtime、代码智能、多 Agent、Skill、评测和文档。

详见 [版本路线图](docs/02-version-roadmap.md)。

## 文档导航

- [项目概览](docs/00-overview.md)
- [架构设计](docs/01-architecture.md)
- [版本路线图](docs/02-version-roadmap.md)
- [Evidence Runtime 与 TrailGraph](docs/03-trailgraph.md)
- [工具与安全](docs/04-tools-and-safety.md)
- [Skills 与扩展](docs/05-skills-and-extensions.md)
- [测试与评测](docs/06-testing-and-evaluation.md)
- [参考 Agent 项目](docs/07-reference-agents.md)
- [Context Pack Compiler](docs/08-context-pack-compiler.md)
- [Safe Execution Kernel](docs/09-safe-execution-kernel.md)
- [Adaptive Planner](docs/10-adaptive-planner.md)
- [Verification Router](docs/11-verification-router.md)
- [完整开发文档](docs/12-development-plan.md)
- [实施文档索引](docs/implementation/00-implementation-index.md)
- [版本实施基线](docs/implementation/07-implementation-baseline.md)

## 第一阶段实现目标

v0.1 只实现最小 Agent Runtime，不直接堆完整 LSP、RAG、MCP 或多 Agent。

1. 启动 CLI/REPL。
2. 加载模型配置。
3. 扫描项目并生成 `XHX.md`。
4. 读取和搜索文件。
5. 通过 `apply_patch` 应用安全补丁。
6. 推断验证命令，并在用户确认后执行。
7. 输出包含文件、命令、验证结果和风险的 Markdown 总结。

## 反模式

- 不把完整命令日志塞进 Prompt。
- 不让每个任务默认走 DAG。
- 不让 Skill 绕过权限策略。
- 不在 v0.1 实现完整 LSP、RAG 或 MCP。
- 不自动 commit 或 push。

## 基本假设

- 第一版运行栈使用 Python + LangGraph。
- 第一版交互方式使用 CLI/REPL。
- 第一版优先支持 Python 和 JavaScript/TypeScript 仓库。
- 星穹铁道主题彩蛋暂缓，等核心 Runtime 可用后再加。
- 第一批文档面向工程落地，不写成宣传型文案。
