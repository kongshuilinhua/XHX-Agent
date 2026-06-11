# 计划（精简）：Phase 5a 子 agent（dispatch / 只读 explore）—— 测试

> 精简计划（见 `docs/superpowers/gemini-handoff-workflow.md`）。**核心已由 Claude 写好（commit `0586f87`）；本切片你的活 = 写测试 + e2e**，不要重写核心实现（如发现核心 bug，最小修正并说明）。

## Goal
为 Phase 5 子 agent 的 MVP（只读 `explore` 子 agent）补齐测试。父（loop/plan）调 `dispatch(description, prompt, agent_type)` 工具 → 开**隔离子循环**（自己的消息历史、**仅 search/read_file 只读工具**、限轮数 `MAX_SUBAGENT_TURNS=4`）→ 跑完只回浓缩结论喂回父。

## 现状：核心已由 Claude 完成（commit `0586f87`）
- `src/xhx_agent/orchestrators/subagent.py`（新）：`run_subagent(ctx, *, description, prompt, agent_type, turn)`——隔离只读子循环，`AGENT_TOOLSETS={"explore":{"search","read_file"}}`，受限工具外的调用回 `[dispatch] tool '...' not allowed`，未知 `agent_type` 回 `[dispatch] unknown agent_type`，限轮数兜底，emit `subagent_start`/`subagent_done`，返回 `"[sub-agent explore] <结论>"`。
- `src/xhx_agent/orchestrators/_toolturn.py`：`_execute_tool_call_rich` 顶部路由 `tc.name=="dispatch"` → `run_subagent`（loop/plan 都获得能力）。
- `src/xhx_agent/tools/registry.py`：`dispatch` ToolDefinition（无 runner、非 is_command；schema 含 description/prompt/agent_type）。
- `tests/test_tool_registry.py::test_definitions_carry_runner` 已更新（dispatch 属"特殊路由、无 runner"类）。
- 现状 **322 passed, 1 skipped, ruff 绿**；Claude 已内联冒烟过：explore 读文件回结论、写工具被拦且 workspace 不变、未知类型优雅报错。

## 先读
- `src/xhx_agent/orchestrators/subagent.py`、`_toolturn.py`（dispatch 路由）、`tools/registry.py`（dispatch 定义）。
- `tests/test_loop_orchestrator.py` 与 `tests/test_plan_orchestrator.py`——**复用它们的 fake `chat` client 写法**（monkeypatch `build_chat_client`）。

## 边界（不许动）
- 不改父 loop/plan 控制流、不改 `run_subagent` 行为契约。
- MVP 只读：不加写型/worktree 子 agent、不加并行多 dispatch、agent_type 不扩展（只 explore）。
- 现有测试保持全绿。

## 关键 check 点（每个要有测试）
1. **schema 暴露**：`default_tool_registry().tool_schemas()` 里有 `dispatch`（模型看得见）。
2. **e2e（父→子→父）**：新建 `tests/test_subagent.py`。monkeypatch **两处** `build_chat_client`：① `xhx_agent.orchestrators.loop`（父：首轮回一个 `dispatch` tool_call，次轮回纯文本结束）② `xhx_agent.orchestrators.subagent`（子：回 read_file 再回结论）。跑 `RuntimeApp(tmp).run_task(..., mode="loop")`，断言父最终 `answer` 正常、且父消息流里 dispatch 的 tool 结果含子结论（可从 transcript `.xhx/sessions/*.json` 里找 `[sub-agent explore]`）。
3. **隔离/安全**：explore 子 agent 发 `apply_patch` 被拦（结果含 "not allowed"），且 workspace 内目标文件**未被修改**（写没泄漏）。直接调 `run_subagent` + fake 子 client 即可（参考 Claude 冒烟思路）。
4. **未知 agent_type**：`run_subagent(..., agent_type="writer")` 返回含 "unknown agent_type"。
5. **限轮数兜底**：子 client 每轮都回 tool_call（永不结束）→ `run_subagent` 在 `MAX_SUBAGENT_TURNS` 后返回兜底结论（含 "turn limit"）。
6. **plan 也支持**：`mode="plan"` 下父发 dispatch 同样能跑（共享 `_execute_tool_call_rich`）——加一个最简断言。
7. **全绿**：`PYTHONUTF8=1 uv run pytest -q`（≥322 + 新测试）、ruff 绿、loop/plan 既有测试不破。

## 纪律 / 明确排除
- TDD；命令前置 `PYTHONUTF8=1`；每步零回归 + ruff 绿；只在分支提交，不 push main，commit 只 add 测试文件（核心已提交）。
- **不做**：写型/worktree 子 agent、并行 dispatch、agent_type 扩展、父循环改动。
