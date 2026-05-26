# TUI / Command Console 规格

本文档定义 v0.5 的终端交互界面。目标是接近 Claude Code 的使用体验，但保持模块解耦：TUI 只处理渲染、输入和确认，不直接调用模型或工具。

## 目标

TUI 必须让用户看到 Agent 正在做什么，并能在关键时刻控制执行。

必须支持：

- 输入框。
- 流式输出。
- 工具调用状态。
- 权限确认。
- 当前计划展示。
- 验证状态展示。
- repair loop 状态展示。
- `/` 命令系统。
- diff / evidence / context 摘要查看。

## 非目标

v0.5 不做：

- Web UI。
- 文件树编辑器。
- 完整 IDE。
- 鼠标驱动的多窗格复杂布局。
- 直接在 TUI 内执行未授权工具。

## 模块边界

TUI 依赖：

- Runtime event。
- Runtime command API。
- CLI command definitions。

TUI 不依赖：

- `models.openai_compatible`
- `tools.terminal`
- `tools.patch`
- `evidence` 写入接口。

所有动作必须通过 Runtime：

```text
keyboard input
  -> tui command parser
  -> runtime public command
  -> runtime emits events
  -> tui updates state
```

## 布局

默认布局：

```text
┌ xhx-agent ─────────────────────────────────────────────┐
│ status: planning | running | verifying | waiting       │
├────────────────────────────────────────────────────────┤
│ conversation / streaming assistant output              │
│                                                        │
│ tool: search README.md              done               │
│ tool: apply_patch src/foo.py        waiting confirm    │
│                                                        │
├────────────────────────────────────────────────────────┤
│ plan / evidence / context / diff panel                 │
├────────────────────────────────────────────────────────┤
│ > user input                                           │
└────────────────────────────────────────────────────────┘
```

小终端宽度时：

- 保留 conversation 和 input。
- 状态面板折叠为单行。
- plan/evidence/context/diff 通过 `/plan`、`/evidence`、`/context`、`/diff` 临时展开。

## RuntimeEvent 到 UI 状态

TUI 消费这些事件：

- `run_start`：显示运行开始。
- `scan`：更新项目语言和文件数量。
- `context_pack`：更新上下文预算、选中条目和省略条目。
- `model_plan_start`：显示计划生成中。
- `model_plan`：更新当前计划摘要、状态和步骤数量。
- `model_delta`：追加流式文本。
- `tool_start`：显示工具开始。
- `policy_decision`：显示确认或拒绝状态。
- `tool_result`：显示工具结果摘要。
- `verification_start`：显示验证中。
- `verification_result`：显示验证结果。
- `checkpoint`：更新本轮 changed files。
- `repair_decision`：显示修复判断。
- `repair_start`：显示修复轮次。
- `restore_plan`：显示失败后的只读恢复计划状态。
- `cancel_requested`：显示用户已请求取消。
- `run_cancelled`：显示 Runtime 已在安全边界停止。
- `run_end`：显示最终摘要。
- `error`：显示错误。

v0.5 当前实现使用 `ConsoleState` 作为事件归约层。Rich 控制台只读取该状态渲染 `/status`、`/dashboard`、`/plan`、`/context`、`/evidence`、`/verify` 和 `/diff`，不直接读取模型或工具内部对象。OpenAI-compatible profile 在 `stream=true` 时会把 SSE 文本增量转成 `model_delta`，控制台直接打印增量并在 dashboard 中保留最近模型输出摘要。`xhx tui --fullscreen` 提供实验性 Textual 窗口，当前负责全屏布局、`ConsoleState` 快照展示、输入提交、Runtime 任务执行事件刷新和 `/help`、`/status`、`/clear`、`/exit` 本地命令；全部 slash 命令路由和交互式权限确认仍以 Rich Command Console 为稳定路径。

当前边界：

- `model_delta` 只表示模型原始文本增量，不代表工具已经执行。
- 模型输出仍必须解析成 JSON plan 后才进入工具执行。
- Rich 控制台会直接追加增量文本；Textual 全屏路径已支持普通任务执行和 Runtime 事件刷新，但还没有接入交互式权限确认、完整 slash 命令和运行中 steer。

`tui.page` 负责把 `ConsoleState` 渲染成 Rich 终端页面，包含状态栏、conversation、runtime state、context、changed files、events 和命令提示。它不处理输入、不调用 Runtime，也不读写 Evidence Runtime。

`tui.live` 负责 Rich Live 生命周期。它只接收 `ConsoleState` 和显示选项，调用 `tui.page` 生成 renderable，并在 Runtime event 到来时刷新固定区域。它不读取模型、工具、Evidence Runtime 或 session 文件。

`tui.textual_app` 负责实验性全屏 shell。它接收 `ConsoleState`，生成 header、conversation、runtime、changed files 和 command hints，处理少量只读本地命令，并通过 Runtime 公开 API 执行普通任务。它不直接调用模型、工具或 Evidence Runtime。权限确认在本阶段默认拒绝，后续必须接入显式用户确认。

## 权限确认

当 Runtime 发出 confirm decision：

```text
Command requires confirmation:
  npm test
Reason:
  npm test may execute project scripts.

[y] allow once   [n] deny
```

规则：

- `y` 只允许本次执行。
- `n` 拒绝本次执行。
- 拒绝后 Runtime 不得绕过。
- TUI 不自己执行命令，只把确认结果传回 Runtime。

## `/` 命令

### /help

显示可用命令和一句话说明。

### /model

行为：

- 无参数：显示当前 profile。
- 带参数：请求 Runtime 切换 profile。

示例：

```text
/model
/model default
```

### /status

显示：

- 当前 cwd。
- 当前 run id。
- 当前 mode。
- changed files。
- 最近验证结果。
- 当前 profile。

### /plan

显示当前任务计划。

如果没有计划：

```text
No active plan.
```

### /evidence

显示本次 run 的 Evidence 摘要。

v0.5 只显示 Evidence Index 摘要，不展开 Raw Trace 全文。

### /context

显示当前 Context Pack 摘要：

- goal。
- mode。
- project summary。
- selected files。
- selected evidence。
- recent failures。
- budget 使用情况。

### /verify

请求 Runtime 手动触发 Verification Router。

规则：

- 如果没有 changed files，显示 no changes。
- 如果需要 confirm，进入权限确认。
- v0.5 当前实现只验证当前 changed files，不重新规划、不修复失败、不改文件。

### /repair

请求 Runtime 手动触发 repair loop。

规则：

- 只有最近验证失败时可用。
- `/repair` 默认只执行一轮手动 repair。
- `/repair loop` 最多执行两轮手动 repair，每轮修复后都会重新经过 Verification Router。
- repair 仍必须经过模型计划、`apply_patch`、SafeExecutionKernel 和 Verification Router。
- 当前仍不是运行中实时 steer 的完整交互式 repair 工作流。

### /diff

显示本轮 changed files 和 diff 摘要。

v0.5 不要求完整 diff viewer，但必须能显示文件列表和 patch 摘要。

当前实现通过 Runtime 的只读 diff API 获取摘要：

- TUI 不直接执行 git 或工具。
- Runtime 使用 `git diff -- <changed-files>` 的 argv 调用，不经过 shell。
- 输出默认截断，避免大 diff 刷屏。
- 没有 git worktree 或没有 diff 输出时，只显示 changed files 和说明。

### /skills

显示 Skill 列表。

v0.5 可以只提示：

```text
Skills are planned for v0.8.
```

### /mode

显示或切换执行模式。

允许值：

- `direct`
- `research-only`
- `linear-edit`
- `plan-review-act`
- `dag-execute`
- `repair-loop`

v0.5 中 `dag-execute` 可以显示为 planned。

### /live

切换 Rich Live 动态仪表盘。

行为：

- `/live`：显示当前 live 状态。
- `/live on`：启用 Rich Live 动态刷新。
- `/live off`：关闭 Rich Live 动态刷新，回到普通事件日志输出。

规则：

- 真实交互终端默认启用。
- 记录型测试控制台默认关闭。
- live 只负责刷新展示，不改变 Runtime 执行逻辑。
- v0.5 的 live 仍基于 Rich；Textual 全屏路径通过 `xhx tui --fullscreen` 单独启动，当前是实验性 shell。

### /cancel

请求 Runtime 在下一个安全边界取消当前任务。

规则：

- 如果当前没有运行中的任务，显示 `No running task to cancel.`。
- 如果任务正在运行，写入 `cancel_requested` 状态。
- Runtime 在模型规划、工具执行、验证命令开始前检查取消状态。
- v0.5 当前实现不是异步强杀；已经启动的外部命令不会被立即终止。

### /clear

清空当前屏幕显示，不删除 session、trace 或 evidence。

### /exit

退出 TUI。

如果 run 正在执行：

- 请求 Runtime cancel。
- 等待 Runtime 到达下一个安全边界。
- 写入 cancelled 状态。

## 输入行为

普通文本：

- 如果没有 active run，创建新任务。
- 如果已有上一轮结果，作为 follow-up，并携带上一轮 run id、状态、验证结果、changed files 和报告路径。
- v0.5 当前实现只支持任务之间的 follow-up 上下文包装，还不支持运行中的实时 steer。
- `xhx tui --fullscreen` 当前普通文本输入会调用 Runtime 执行任务，并将 Runtime 事件刷新到窗口状态。
- `xhx tui --fullscreen` 当前只处理 `/help`、`/status`、`/clear`、`/exit`，其余命令显示未知命令或后续接入提示。
- `xhx tui --fullscreen` 遇到 confirm 级权限时默认拒绝，直到交互式权限确认面板接入。

快捷键：

- `Enter`：提交。
- `Ctrl+C`：请求取消或退出。
- `Ctrl+L`：清屏。

快捷键必须集中配置，不散落在组件代码里。

## 渲染要求

- 支持中文宽度。
- 支持 Markdown 基础渲染。
- 支持代码块。
- 支持 diff 摘要。
- 流式输出不能刷屏。
- 终端 resize 后重新布局。

## 测试要求

单元测试：

- `/` 命令解析。
- TUI state reducer。
- 权限确认输入。
- 中文宽度处理。

集成测试：

- fake terminal 中完成 `/help`、`/status`、`/context`。
- confirm 命令允许和拒绝。
- runtime event 转 UI 状态。

E2E：

- 在 TUI 中完成一次 Python fixture 的读改测任务。

## 反模式

- TUI 直接调用工具。
- TUI 直接写 Evidence Runtime。
- TUI 把完整 Raw Trace 打到屏幕。
- TUI 只做外观，不显示真实 Runtime 状态。
- `/clear` 删除 session。
