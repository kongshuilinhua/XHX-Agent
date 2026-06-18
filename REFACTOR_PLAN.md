# XHX-Agent 架构重构实施计划（交接给执行方）

> 本文件是给下一位执行者（DeepSeek）的自包含任务说明。你**冷启动**，请先读完「背景」「硬约束」再动手。

---

## 0. 背景与当前状态

XHX-Agent 正把整体架构统一为「单一 Agent 主循环 + 工具/权限/记忆/Hook/SubAgent/Worktree/Teams」的新栈。参考实现位于 `D:\pycharmprojects\mewcode`（**仅作你移植时的参照源，严禁把它的名字/作者/项目信息写进本仓库任何代码、注释、提交信息**——见硬约束）。

**当前已经存在两套引擎，正在收敛为一套：**

- **新栈（目标，保留）**：
  - 主循环 `src/xhx_agent/agents/agent_runner.py`（`Agent.run()` 交互流 / `Agent.run_to_completion()` headless）
  - 入口 `src/xhx_agent/tui/app.py`（默认交互 TUI）、`src/xhx_agent/runtime/headless.py`（`xhx run` 走它）
  - 工具 `src/xhx_agent/tools/`（`create_default_registry`）、权限 `src/xhx_agent/safety/permissions/`、Hook `src/xhx_agent/hooks/`、记忆 `src/xhx_agent/memory/auto_memory.py`、子 Agent `src/xhx_agent/agents/`、Worktree `src/xhx_agent/worktree/`、Teams `src/xhx_agent/teams/`
  - 假模型 `client.MockClient`（`--profile mock` 用，无网络可跑）
  - verification 已作为 Hook 接入（`src/xhx_agent/hooks/executors.py: execute_verification` + `hooks/__init__.py: default_verification_hook`）

- **旧栈（待删）**：`src/xhx_agent/runtime/app.py` 的 `RuntimeApp`、`src/xhx_agent/planner/`、`src/xhx_agent/runtime/verify_loop.py`、`src/xhx_agent/evidence/`、`src/xhx_agent/agents/adapter.py`。
  - 仍在用 RuntimeApp 的消费者：`cli/rpc.py`、`evals/benchmark.py`、`evals/replay.py`、`__init__.py`（导出）、测试 `test_agents.py`/`test_runtime_mcp.py`/`test_package.py`。`cli/main.py` 已**不再**依赖 RuntimeApp。

**已完成（勿重复）**：第1工具、第2主循环、第3 SystemPrompt、第10 Hook、第11 SubAgent、第12 Worktree、第13 Teams 已对齐；`xhx run` 已走新 headless 循环；署名注释已全部清除。

---

## 1. 硬约束（任何一步都必须遵守）

1. **红线——无溯源痕迹**：提交的代码、注释、docstring、提交信息中，**不得出现** `mewcode` / `小林` / `xiaolin` / `公众号` / 该项目作者或网址等任何字样。移植代码时改写注释为中性中文。
2. **全程中文注释/回复**，代码标识符/库名保持原文。
3. **每个工作项 = 一次提交，且提交前测试必须绿**。测试命令：
   ```
   python -m pytest -p no:cacheprovider -m "not live" --timeout=120 -q
   ```
   （`pytest-timeout` 已安装；带 `--timeout` 以防个别慢用例。）
4. **不删有价值的 xhx 独有能力**：`repo_intel/`（仓库智能，已作为 `repo_query` 工具接入新栈）、verification（已作为 Hook 接入）必须保留。
5. 在当前分支 `refactor/mewcode-integration` 上继续；不要切回 main。
6. 删除任何文件前，先 `grep` 确认无存活引用；删除即同步更新 `__init__.py` 导出与相关测试。

---

## 2. 工作项（按顺序执行，每项独立提交）

### A. 删除旧引擎 RuntimeApp（Phase B 收尾，体量最大）

按子步推进，每步保持绿：

- **A1 迁 evals 到新循环**：`evals/benchmark.py`、`evals/replay.py` 改用 `runtime.headless.run_headless_task(workspace, task, profile=..., verify=True)`，从返回的 `HeadlessResult`（含 `status`/`summary`/`verification`）构造各自的结果指标。删除对 `RuntimeApp.run_task` 的依赖。更新 `test_benchmark_matrix.py`/`test_evals.py`。
  - 检查点：`xhx benchmark --profile mock --modes loop`（mock 下）能跑通并出结果。
- **A2 迁 `cli/rpc.py`**：`xhx rpc` 的 RPC 循环改为驱动新 `Agent`（参照 `headless.run_headless_task_async` 的构造）。若 rpc 现有协议复杂，最小化保留其输入/输出契约。更新 `test_runtime_mcp.py` 中依赖 RuntimeApp 的部分（MCP 注册逻辑迁到新栈或改测 `MCPManager` 直接）。
- **A3 删除旧栈**：删 `runtime/app.py`(RuntimeApp/RunResult/PlanPreview 等)、`planner/`、`runtime/verify_loop.py`、`evidence/`、`agents/adapter.py`。
  - 同步：`__init__.py` 移除 `RuntimeApp`/`RunResult` 导出；改 `test_package.py`（不再断言 RuntimeApp）；删/改 `test_agents.py` 中走 RuntimeApp 的用例。
  - 注意 `tools/terminal.py`、`tools/registry.py`(旧函数式注册表) 若仅被旧栈用，一并评估删除；被新 `web_fetch_tool`/`apply_patch_tool` 委托的 `tools/web.py`/`tools/patch.py` **保留**。
  - 检查点：全量测试绿；`grep -rn RuntimeApp src/` 为空。

> 说明：旧 `verify_loop.py` 的「验证+有界自动修复」比新 `execute_verification`（仅跑一次定向测试、不自动修复）更强。若要保留「失败后自动修复」，可在 `execute_verification` 失败时让 Agent 继续一轮（注入失败信息 + 设上限），作为 A3 的可选增强；否则接受功能简化。

### B. 第8章 Slash 命令对齐（最大用户可见缺口）

现状：`commands/handlers/` 只有 5 个（skill_register/tasks/trace/worktree）；`commands/defaults.py` 注册的是旧命令（`/mode /evidence /repair /dashboard /live /diff /context` 等旧栈概念）。

- **B1** 按参考架构补齐命令 handler：`/compact`、`/memory`、`/mcp`、`/permission`、`/rewind`、`/review`、`/status`、`/clear`、`/help`、`/plan`、`/skill`、`/session`（参照 `D:\pycharmprojects\mewcode\mewcode\commands\handlers\`，移植后改中性注释）。接到新 TUI 的 `command_registry`。
- **B2** 淘汰旧概念命令：删除 `defaults.py` 中依赖已删旧栈的命令（`/mode`/`/evidence`/`/repair`/`/dashboard`/`/live` 等）。保留仍有意义的（`/clear`/`/help`/`/model`/`/perm`→`/permission`）。
- 检查点：交互 TUI 里这些命令能用；`test_completion.py`/命令相关测试更新并绿。

### C. 取舍拍板类（每项先按建议做，差异不大可直接采纳）

- **C1 权限收口（第4章）**：删旧栈后，`safety/kernel·policy·repair` 多数随之消失。剩 `safety/risk.py`（命令风险分级）与 `safety/permissions/`（新）：**建议**保留 `safety/permissions/` 为唯一授权判定，`risk.py` 仅作为 `DangerousCommandDetector` 的补充或合并进去。确认 `safety/checkpoint.py`/`permission_mode.py`/`worktree.py` 是否仍被新栈引用，无则删。
- **C2 MCP（第5章）**：现用 xhx 自有 `skills/mcp.py`（`MCPManager`，已接入新 TUI/headless 可选）。**建议**：先评估它是否覆盖参考架构 `mcp/`（client/manager/tool_wrapper）的 stdio + 工具包装能力；若已覆盖则保留并删除空壳 `mcp/`（把 `mcp/__init__` 的桥接固定下来）；若缺 SSE/HTTP 传输等，再从参考项目补齐。**不要无脑替换**。
- **C3 上下文（第6章）**：新 `context/manager.py` 为主；确认 xhx 的 `context/compiler·pack·compaction·debug` 哪些仍被新栈用（`agent_runner` 用 `context.auto_compact` 等）。保留被用的，删纯旧栈专用的。
- **C4 记忆（第7章）**：新 `memory/auto_memory.py`（LLM 提取）+ xhx `recall/store/extract`（确定性召回）互补，**建议都保留**。评估是否需要从参考项目补 `memory/session.py`（会话记忆）/`instructions.py`。
- **C5 内置 Skill（第9章）**：`skills/builtins/` 现只有 `commit.md`/`review.md`。可按需补 `test` 等（低优先）。

### D. 横切清理（收尾）

- **D1 ruff 全量清理**：移植代码有较多 lint 债。`python -m ruff check --fix src/ tests/` 跑自动修，再手工处理剩余（`SIM103`/`B027` 等）。目标：`ruff check` 干净（CI 第一关）。
- **D2 mypy + 覆盖率**：CI 要求 `mypy` 通过、`pytest cov ≥80%`。补新栈薄弱处测试：`teams/`（mailbox/shared_task/coordinator）、`conversation.py`、`client.py`（用 `tests/test_headless.py` 里的 `FakeLLMClient` 套路）。
- **D3** 删除本文件 `REFACTOR_PLAN.md`（收尾后）。

---

## 3. 关键参照点（少走弯路）

- 构造新 Agent 的范式：见 `runtime/headless.py: build_headless_agent`（client/registry/permission_checker/memory/hook_engine 装配）。
- 非交互测试范式：`tests/test_headless.py` 的 `FakeLLMClient`（脚本化产出 `StreamEvent`）+ 同步 `run_headless_task`。
- Hook 事件：主循环在 `turn_start/turn_end/pre_tool_use/post_tool_use/stop` 触发；`HookEngine` 真实方法是 `run_hooks/collect_prompt_messages/drain_notifications`（**不是** `get_prompt_messages`/`run_pre_tool_hooks`，那两个曾是 bug，已修，别再引用）。
- `--profile mock` → `config.from_xhx_profile` 映射到 `protocol="mock"` → `client.MockClient`（确定性、无网络）。

## 4. 完成定义（DoD）

- `grep -rn RuntimeApp src/` 为空；`planner/`、`verify_loop.py`、`evidence/`、`agents/adapter.py` 已删。
- `grep -rniE "mewcode|小林|xiaolin|公众号" src/ tests/` 为空。
- 交互 TUI 与 `xhx run` 共用同一引擎；新命令集可用。
- CI 四关全绿：`ruff check`、`ruff format --check`、`mypy`、`pytest cov≥80%`。
