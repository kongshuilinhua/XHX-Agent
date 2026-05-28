# 版本实施基线

本文档是后续开发的执行基线。它固定版本名称、版本顺序、验收条件和变更规则，避免实现过程中随意新增小版本或改变路线。

## 使用规则

- 开发前先确认当前版本和目标版本。
- 版本名称只能使用本文档列出的名称。
- 不新增 `v0.1-D`、`v0.1-E` 这类临时版本名。
- 需要偏离路线时，必须先更新本文档的“路线变更记录”，说明原因、影响和新的验收条件。
- 每次提交后必须同步 README 的“当前实现状态”。
- 当前实现状态必须区分已实现、部分实现、未实现，不能把规划能力写成已完成能力。

## 当前版本线

```text
v0.1-A 真实模型接入
  -> v0.1-B Tool-call loop
  -> v0.1-C 验证闭环
  -> v0.2 Safe Execution Kernel 强化
  -> v0.3-v0.4 Context + Evidence
  -> v0.5-v0.7 产品能力
  -> v0.8-v0.9 扩展、评测、回放
  -> v1.0 完整可发布版本
```

## 当前提交归属

已有提交按下面方式归类，不再改写历史提交名。

| 提交 | 原提交名 | 归属版本 | 说明 |
| --- | --- | --- | --- |
| `61df280` | `Initial xhx-agent v0.1-A` | v0.1-A / v0.1-B / v0.1-C 的 mock 骨架 | 建立 CLI、配置、mock 闭环、基础工具、验证和报告。 |
| `6fbe08d` | `Implement v0.1-B context planning loop` | v0.1-B，含 v0.3 前置能力 | 增加 tool-call loop、Context Pack 最小版和 dry-run。Context Pack 是后续前置，不代表 v0.3 完成。 |
| `1db7523` | `Implement v0.1-C model planning diagnostics` | v0.1-A / v0.1-B 稳定性补强 | 增强真实模型计划解析和循环停止诊断，不单独作为新路线节点。 |
| `ffe7cf4` | `Implement v0.1-D atomic patch engine` | v0.1-B 写入工具补强，兼作 v0.2 前置 | 强化 `apply_patch` 原子性。历史提交名保留，但后续文档不再使用 v0.1-D 作为版本。 |
| `d32122c` | `Complete v0.1-C verification loop` | v0.1-C | 补齐交互确认、验证结果摘要、失败停止和验证报告。 |
| `3279d04` | `Add v0.2 safety checkpoint groundwork` | v0.2 前半 | 增加 checkpoint、policy trace/evidence 和默认不自动 repair 的失败停止报告。 |
| `845127a` | `Complete v0.2 safe execution loop` | v0.2 | 抽出 SafeExecutionKernel，加入 `--auto-repair` 最多两轮修复循环。 |
| `65b3d6f` | `Add v0.2 checkpoint restore plan` | v0.2 | 增加失败后的只读 restore plan，不自动回滚。 |
| `e8dddf6` | `Implement v0.3 context pack compiler` | v0.3 | 正式化 Context Pack Compiler，加入预算选择和 context debug report。 |
| `544615a` | `Implement v0.4 evidence runtime` | v0.4 | 加入 EvidenceStore 读回、artifact_ref 展开、patch evidence id 绑定和报告渲染。 |

## v0.1-A 真实模型接入

目标：真实模型能返回结构化工具计划，不再只依赖 mock。

必须实现：

- `OpenAICompatibleClient`。
- OpenAI-compatible `base_url`、`api_key_env`、`model`、`temperature` 配置。
- API key 从环境变量读取。
- 普通 JSON 输出解析。
- 常见 JSON fenced block 解析。
- 分段 content 解析。
- JSON / schema 错误结构化诊断。
- 保留 `MockModelClient` 作为测试线。

验收标准：

- 缺少 API key 时失败可解释，不执行工具。
- HTTP 错误、非 JSON 响应、坏 plan schema 都写入 trace。
- 模型返回合法 JSON plan 时能生成 `ModelPlan`。
- mock 测试不依赖真实网络。

当前状态：基本完成。

已完成：

- OpenAI-compatible 非流式 Chat Completions 请求。
- API key 环境变量读取。
- mock model。
- JSON plan 解析、fenced block、分段 content、错误诊断。
- `--dry-run` 可预览计划并写 trace。

未完成：

- 流式模型输出。
- provider 级 response format 适配。
- 更完整的真实模型端到端验收。

下一步如果继续 v0.1-A：

- 增加可选 `response_format` 配置。
- 增加真实模型 profile 的手动验收文档。
- 保持 mock 测试为 CI 默认路径。

## v0.1-B Tool-call loop

目标：Runtime 能执行“模型 -> 工具调用 -> 工具结果 -> 再调用模型”的最小循环。

必须实现：

- 模型返回 tool steps。
- 支持 `read_file`、`search`、`apply_patch`。
- Tool Registry 校验工具名和参数。
- 工具结果写 trace。
- 工具结果摘要进入下一轮模型上下文。
- `apply_patch` 作为唯一仓库写入工具。
- patch 必须写入 evidence。

验收标准：

- 模型不能调用未注册工具。
- 坏工具参数不会执行。
- 工具失败时停止并报告。
- 成功 patch 后记录 changed files。
- Python / Node fixture 能通过工具循环完成修改。

当前状态：部分完成。

已完成：

- Tool Registry。
- `read_file`、`search`、`apply_patch`。
- 工具结果反馈下一轮模型。
- `apply_patch` 多 hunk、多文件、Add File、路径逃逸拒绝、失败不落盘。
- patch 成功写入 Evidence Index。

未完成：

- 工具层还没有完全统一进入 Safe Execution Kernel。
- patch 尚未强制绑定具体 evidence id。
- terminal 仍主要用于验证，不允许模型直接调用。

下一步如果继续 v0.1-B：

- 保持工具集合小而稳定。
- 不引入 DAG。
- 不引入 Skill/MCP。

## v0.1-C 验证闭环

目标：完成真实 v0.1 的“读 -> 改 -> 测 -> 报告”闭环。

必须实现：

- Verification Router。
- 根据 changed files 和项目结构推断验证命令。
- terminal confirm / `--yes` 执行。
- 验证结果写 Raw Trace。
- 验证结果写 Evidence Index。
- 验证失败立即停止并报告。
- 不急着做 repair loop。

验收标准：

- Python fixture 修改后默认运行 `python -m pytest`，必要时才由用户指定 `uv run pytest`。
- Node fixture 修改后运行 `npm test`、`npm run typecheck` 或 `npm run build`。
- 用户不传 `--yes` 时 confirm 级命令不自动执行。
- 只读任务不触发验证。
- 验证失败时状态为 failed，报告中有命令、退出码摘要和风险。
- 每次任务生成 Markdown 报告。

当前状态：部分完成。

已完成：

- Python / Node 基础验证推断。
- `--yes` 执行 confirm 级验证命令。
- 无 `--yes` 时可以通过 CLI 交互确认验证命令。
- 验证结果写 Raw Trace 和 Evidence Index。
- 只读任务标记为 `skipped_no_changes`。
- 验证失败时明确 `status=failed`，不进入 repair。
- Markdown 报告包含 command、risk、decision、exit_code 和输出摘要。
- 输出摘要有行数和字符数截断，避免大日志直接进入报告。
- Python 测试文件变更时可以推断 targeted pytest 命令。
- Python / Node fixture smoke 通过。

未完成：

- 验证命令选择还没有从源码文件精确映射到对应测试文件。
- terminal confirm 仍是 CLI 级基础交互，TUI 权限确认要到 v0.5。

下一步必须优先补齐：

1. 源码文件到测试文件的更细验证路由。
2. 补充一次 CLI 非 JSON 交互确认 smoke。
3. 确认 v0.1-C 验收全部通过后，进入 v0.2。

## v0.2 Safe Execution Kernel 强化

目标：失败能安全停住，危险操作不会自动执行。

必须实现：

- Git checkpoint。
- changed files 追踪增强。
- repair loop 最多 2 轮。
- deny / confirm 策略补强。
- policy decision 写 trace 和 evidence。

验收标准：

- `rm -rf`、`git reset --hard`、全局安装默认拒绝。
- confirm 命令必须由用户确认或 `--yes` 放行。
- repair loop 不会无限循环。
- 修复失败时保留 trace、evidence 和报告。

进入条件：

- v0.1-C 验证闭环完成。

当前状态：基本完成。

已完成：

- 修改后验证前写入 `.xhx/checkpoints/<run-id>.json`。
- checkpoint 记录 changed files 的 sha256、size、Git HEAD 和 dirty 状态。
- 验证失败且已有 checkpoint 时写入 `.xhx/checkpoints/<run-id>-restore-plan.json` 只读恢复计划。
- 已抽出 `SafeExecutionKernel`，由 Runtime 通过该边界执行工具、创建 checkpoint、运行验证和记录 policy。
- tool policy decision 写入 Raw Trace 和 Evidence Index。
- terminal policy decision 写入 Raw Trace 和 Evidence Index。
- changed files 随 `RunResult`、checkpoint、报告和 evidence 贯通。
- `--auto-repair` 可在验证失败后最多执行 2 轮 repair loop。
- repair loop 会把最近验证失败摘要反馈给下一轮模型计划。
- repair loop 达到上限后停止，不会无限循环。
- 验证失败时记录 repair decision，并明确说明未尝试自动修复。
- 失败报告中记录 restore plan 路径，明确 v0.2 不自动回滚。
- repair 成功或失败都会在报告中记录 attempts、decision、verification 和 risk。

未完成：

- 还没有自动回滚；v0.2 只提供只读 restore plan。
- patch 尚未强制绑定具体 evidence id。
- SafeExecutionKernel 仍是单进程最小内核，还没有独立沙箱或进程隔离。

进入 v0.3-v0.4 前仍需处理：

1. 将只读 restore plan 和 Evidence Runtime 的 artifact_ref 展开机制衔接起来。
2. 在 Evidence Runtime 中实现 patch 到具体 evidence id 的强绑定。
3. 保持 SafeExecutionKernel 边界稳定，避免 Runtime 重新直接编排安全逻辑。

## v0.3-v0.4 Context + Evidence

目标：解决 token 爆炸和可追溯问题。

v0.3 必须实现：

- Context Pack Compiler 正式化。
- token budget。
- top-k evidence selection。
- changed files selection。
- recent failure selection。
- context debug report。

v0.4 必须实现：

- Raw Trace 完善。
- Evidence Index 完善。
- patch 绑定 evidence id。
- report evidence 渲染。
- artifact_ref 按需展开。

进入条件：

- v0.2 Safe Execution Kernel 完成。

当前前置状态：

- 已有最小 Context Pack。
- 已有 Raw Trace / Evidence Index 基础写入。
- 这些只能算前置能力，不代表 v0.4 已完成。

v0.3 当前状态：基本完成。

v0.3 已完成：

- Context Pack Compiler 正式化为预算驱动选择器。
- 每轮记录 `budget_tokens`、`used_tokens_estimate` 和 reserved token 估算。
- 支持 top-k evidence selection，并按 evidence kind 和 confidence 排序。
- 支持 changed files selection，避免变更文件过多时全部进入上下文。
- recent failure 以高优先级进入 repair-loop 上下文。
- 每轮写入 `.xhx/context/<run-id>-turn-<n>.json` context debug report。
- Raw Trace 中记录 `context_debug_report` 路径。

v0.3 未完成 / 后续增强：

- token 估算仍是字符数近似，不是 provider tokenizer。
- changed files 还没有结合 repo_intel 做影响面排序。
- 历史会话摘要还没有正式接入。

v0.4 当前状态：基本完成。

v0.4 已完成：

- Raw Trace 支持 JSONL 写入和读回。
- Evidence Index 支持 JSONL 写入和读回。
- EvidenceStore 支持按 evidence id 查找证据。
- EvidenceStore 支持按需展开 `trace://...` 和 `checkpoint://...` artifact_ref。
- `apply_patch` 成功后写入 patch evidence，并通过 `patch_evidence_binding` trace 绑定 tool trace id、evidence id 和 changed files。
- Markdown 报告渲染 Evidence Summary，包含 evidence id、kind/source、confidence、summary、artifact_ref 和 artifact 展开状态。

v0.4 未完成 / 后续增强：

- 复杂 TrailGraph 结构仍未实现。
- artifact_ref 展开目前限定在当前 run 的 trace/checkpoint，不跨 run 检索。
- Evidence Index 仍是 JSONL 文件，不是 SQLite 或全文索引。

## v0.5-v0.7 产品能力

目标：从可用 CLI 演进到接近 Claude Code 的产品体验和复杂任务能力。

v0.5：TUI / Command Console。

- 终端交互窗口。
- 流式输出。
- 工具状态。
- 权限确认 UI。
- `/` 命令系统。

v0.5 当前状态：基本完成。

v0.5 已完成：

- 新增 `xhx tui` 入口。
- `xhx chat` 复用同一个 Command Console。
- 基于 Rich 的终端命令控制台可运行任务并保存最近一次结果。
- 新增 `xhx tui --fullscreen` 实验性 Textual 窗口，可以渲染 `ConsoleState` 的 header、conversation、runtime、changed files、details 和 command hints，支持后台 Runtime 任务执行、事件刷新、`/model`、`/plan`、`/context`、`/evidence`、`/diff`、`/verify`、`/repair`、`/skills`、`/mode`、`/dashboard`、`/cancel`、`/live`、pending confirm 的 `/allow` / `/deny` 交互响应，并支持 `/help`、`/status`、`/clear`、`/exit` 本地命令。
- 支持 `/help`、`/model`、`/status`、`/plan`、`/evidence`、`/context`、`/verify`、`/repair`、`/diff`、`/skills`、`/mode`、`/dashboard`、`/live`、`/cancel`、`/clear`、`/exit`。
- 权限确认在控制台中以表格展示 command、risk 和 reason。
- `/plan` 可 dry-run 预览计划，不执行工具。
- Runtime 支持轻量事件回调。
- Runtime 发出 `model_delta`、`policy_decision`、`tool_result`、`verification_result` 等事件，控制台用 `ConsoleState` 做状态归约。
- OpenAI-compatible profile 在 `stream=true` 时可消费 SSE 增量，并把模型输出追加为 `model_delta` 事件。
- Command Console 实时打印 run、context、model、tool、verification、repair 和 report 事件。
- `/dashboard` 使用 `tui.page` 渲染一个 Rich 终端页面，包含状态栏、conversation、runtime state、context、changed files、events 和命令提示。
- `tui.live` 提供 Rich Live 动态仪表盘，真实交互终端中可以用固定区域刷新当前 Runtime 状态；记录型测试控制台默认关闭。
- `/live on|off` 可以切换 Rich Live 动态仪表盘。
- `/plan` 无参数时展示当前 run 计划摘要；带参数时仍执行 dry-run 预览。
- `/verify` 可以基于当前 changed files 手动触发 Verification Router，验证仍经过 SafeExecutionKernel、权限确认、Raw Trace 和 Evidence Index。
- `/repair` 可以在最近验证失败后手动执行一轮修复，`/repair loop` 可以执行最多两轮手动修复；两者都复用模型计划、`apply_patch`、SafeExecutionKernel 和 Verification Router。
- `/diff` 通过 Runtime 的只读 API 展示 changed files 和 `git diff -- <changed-files>` 摘要，TUI 不直接调用 git 或工具，长 diff 会截断。
- 普通输入支持最小 follow-up steering：已有上一轮结果时，会把上一轮 run id、状态、验证结果、changed files 和报告路径包装进新任务上下文。
- Textual 全屏路径的普通任务、手动 `/verify` 和手动 `/repair` 已经改为后台 worker 执行，Runtime event 通过 UI 线程归约到 `ConsoleState`，输入框不会被同步 Runtime 阻塞。
- Textual 全屏路径支持 pending confirm：后台 Runtime 等待确认时，用户可输入 `/allow` 或 `/deny` 放行或拒绝当前命令；没有 pending confirm 时，`/allow` / `/deny` 仍可作为下一次 confirm 的一次性预设。
- Textual 全屏路径支持固定 details 面板：`/plan`、`/context`、`/evidence`、`/diff`、`/verify`、`/repair`、`/dashboard`、`/model` 和 `/skills` 会把当前详情写入固定区域，conversation 只保留历史摘要。
- Textual 全屏路径支持最小运行中 steer：运行中输入普通文本时，先记录为 pending steer 并请求当前 run 在下一安全边界取消；当前 run 结束后，再把 steer 作为 follow-up 执行。
- Textual runtime 面板展示 pending steer、cancel、当前 pending confirm、下一次 confirm 决策、最近 confirm policy、活动工具、活动验证和 repair 轮次。
- `/context`、`/evidence`、`/diff` 优先展示当前会话摘要，不展开完整 Raw Trace。
- `/cancel` 和 `Ctrl+C` 支持请求取消，Runtime 在模型规划、工具执行和验证命令前的安全边界停止，并写入取消事件；当前不是异步强杀正在运行的外部命令。

v0.5 验收清单：

- `xhx tui` / `xhx chat` 可以启动 Rich 命令控制台。
- `xhx tui --fullscreen` 可以启动 Textual 全屏窗口。
- Rich 控制台支持运行任务、事件流、`/dashboard`、`/live`、`/plan`、`/context`、`/evidence`、`/verify`、`/repair`、`/diff`、`/cancel`。
- Textual 全屏路径支持后台 Runtime worker，输入框不会被同步 Runtime 阻塞。
- Textual 全屏路径支持 pending confirm，后台 Runtime 等待时可以用 `/allow` 或 `/deny` 响应当前命令。
- Textual 全屏路径可以在 Python fixture 中通过真实 Runtime 完成一次读改测任务，并在验证等待时用 `/allow` 放行。
- Textual 全屏路径支持 details 面板，常用查看命令不会只刷入 conversation。
- Textual 全屏路径支持运行中输入排队为 steer，并请求 Runtime 在下一安全边界取消当前 run。
- TUI 不直接调用模型、工具、Evidence Runtime 或 git；执行和只读 diff 都通过 Runtime 公开 API。
- `uv run pytest tests/test_tui_textual.py -q`、`uv run pytest tests/test_command_console.py -q`、`uv run xhx tui --help` 通过，其中 `tests/test_tui_textual.py` 必须覆盖真实 Runtime + Python fixture + pending confirm 的全屏闭环。
- 全量 `uv run pytest` 通过。

v0.5 保留边界 / 后续增强：

- Textual 全屏路径目前仍是实验性 shell，已支持后台普通任务执行、后台手动验证和 repair、固定 details 面板、任务间 follow-up 上下文包装、最小运行中 steer、`/model`、`/plan`、`/context`、`/evidence`、`/diff`、`/verify`、`/repair`、`/skills`、`/mode`、`/dashboard`、`/cancel`、`/live` 和 pending confirm 交互响应。
- Rich Live 动态仪表盘已具备固定区域刷新基础，但仍是 Rich 路径，不是完整 Textual 组件系统。
- `/repair loop` 已支持最多两轮手动修复，但仍不是完整运行中 steer 的交互式 repair 工作流。
- 运行中 steer 仍是安全边界后的排队 follow-up，不是 token 流级别的实时改写。
- 取消能力只覆盖阶段边界，还不能中止已经启动的长时间外部命令。
- 没有做完整 diff viewer、滚动历史管理、命令历史补全和完整 Textual 组件化布局；这些属于后续产品体验增强，不阻塞进入 v0.6。

v0.6 进入条件：

- v0.5 验收清单通过。
- README 当前实现状态已同步为 v0.5 基本完成。
- 没有新增未记录的小版本名。
- 若任一 v0.5 验收项失败，不得进入 v0.6，必须继续修 v0.5。

v0.6：Repo Intelligence Graph。

- repo map。
- Tree-sitter 符号提取。
- symbol search。
- context builder。
- impact analysis。

v0.6 当前状态：部分实现。

v0.6 已完成：

- `repo_intel.repo_map` 可以生成基础 RepoMap，记录文件路径、语言、类型、大小和验证提示。
- `repo_intel.symbols` 可以提取 Python 函数 / 类，以及 JavaScript / TypeScript 的基础函数、箭头函数和类符号。
- `search_symbols` 支持 exact、prefix、contains 顺序的轻量 symbol search。
- `repo_intel.context_builder` 可以围绕符号生成带行号的代码片段。
- `repo_intel.impact` 可以把 Python 源文件变更映射到直接测试文件，例如 `src/calc.py` -> `tests/test_calc.py`。
- `repo_intel.impact` 已支持常见 JavaScript / TypeScript direct test 命名，例如 `src/index.js` -> `test/index.test.js`、`src/view.ts` -> `tests/view.spec.ts`。
- `repo_intel.imports` 提供轻量 import graph，能识别 Python `import/from`、JavaScript / TypeScript `import` 和 `require()` 的相对依赖。
- impact analysis 在 direct test 命名匹配失败时，会用 import graph 的反向递归依赖找直接或间接依赖变更源文件的测试文件。
- `repo_intel.index` 可以生成结构化 Repo Intelligence Index，并在 `xhx init` 时写入 `.xhx/repo/index.json`。
- `.xhx/repo/index.json` 当前包含 repo map、symbol index 和 import graph，作为 JSON 产物落盘，后续可替换或补充 SQLite 索引。
- `load_repo_intel_index` 会优先读取 `.xhx/repo/index.json`，索引缺失或损坏时再即时构建。
- Runtime 在成功 `apply_patch` 后会刷新 `.xhx/repo/index.json`，并在刷新后重新推断验证命令。
- repo index refresh 会写入 Raw Trace，并通过 Runtime event 暴露；刷新失败只记录风险，不自动回滚已成功的 patch。
- Verification Router 已开始使用 impact summary，能优先运行 targeted pytest。
- Verification Router 的 impact analysis 会优先复用落盘 repo map 和 import graph，避免每次都重新扫描。
- Verification Router 在 Node 项目中会识别 direct JS/TS test 映射，但仍使用 `npm test` / `npm run typecheck` / `npm run build` 这类 package scripts 作为便携验证命令。
- `XHX.md` 生成时会包含 Repo Map 和 Symbols 摘要，供 Context Pack 后续读取。
- Context Pack 已开始按任务文本进行 symbol search，并优先从 `.xhx/repo/index.json` 读取 symbol index，把少量带行号的 symbol context 放入预算化上下文。

v0.6 未完成 / 后续增强：

- 尚未接入 Tree-sitter，当前 Python 使用标准库 AST，JS/TS 使用轻量正则。
- 尚未实现 SQLite 持久化索引；当前只有 JSON 格式的 `.xhx/repo/index.json`。
- 尚未实现完整跨语言引用关系和调用图。
- impact analysis 目前只覆盖基础 source -> direct test 文件命名映射和有限深度 import graph，不解析完整调用图、跨语言关系或 test runner 参数。
- Context Pack 的 symbol context 选择仍是轻量关键词匹配，尚未使用调用图、引用图或语义检索。

v0.7：Adaptive Planner + DAG。

- mode classifier。
- DAG planner。
- read-only parallel execution。
- write serialization。
- Reviewer quality gate。

进入条件：

- v0.3-v0.4 的上下文和证据机制稳定。

## v0.8-v0.9 扩展、评测、回放

v0.8：Skills / Extensions / MCP。

- Skill metadata。
- trigger matching。
- progressive disclosure。
- hooks。
- optional MCP client。

v0.9：Evaluation / Headless / Replay。

- `xhx run "<task>" --json` 完整化。
- JSONL RPC。
- Trail replay。
- benchmark。
- metrics。

进入条件：

- v0.5-v0.7 的产品和规划能力稳定。

## v1.0 完整可发布版本

目标：形成可以作为完整项目发布的本地编码 Agent。

必须具备：

- 稳定 CLI/REPL/TUI。
- 稳定 OpenAI-compatible profile。
- 稳定读改测闭环。
- Safe Execution Kernel。
- Context Pack Compiler。
- Evidence Runtime。
- Repo Intelligence Graph。
- Adaptive Planner + DAG。
- Skill / MCP。
- Headless / Replay / Evaluation。
- README、安装指南、示例、测试和故障排查。

## 路线变更规则

只有以下情况可以改路线：

- 当前版本的验收标准无法支撑下一个版本。
- 发现安全风险，必须先修。
- 发现架构耦合，继续开发会造成明显返工。
- 用户明确要求调整优先级。

变更步骤：

1. 在本节新增一条记录。
2. 写明原计划、新计划、原因、影响范围。
3. 更新 README 当前状态。
4. 再开始代码实现。

## 路线变更记录

### 2026-05-25：收敛 v0.1 小版本命名

原计划：

- 实现过程中出现了 `v0.1-D`、`v0.1-E` 这类临时版本名。

新计划：

- 只保留 `v0.1-A`、`v0.1-B`、`v0.1-C` 三个 v0.1 子阶段。
- 已提交的 patch engine 强化归入 v0.1-B 写入工具补强和 v0.2 前置能力。
- 后续直接补齐 v0.1-C，再进入 v0.2。

原因：

- 临时版本名会破坏用户确认过的 7 步路线。
- 后续开发需要稳定的验收基线。

影响：

- 不改写历史 Git 提交。
- README 和实施文档改为使用本基线版本名。
