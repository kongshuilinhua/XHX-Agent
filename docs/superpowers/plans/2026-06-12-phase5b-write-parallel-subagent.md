# Phase 5b — 写型子 agent（隔离 worktree + 串行合并 + 冲突上报）

> Phase 5a 给了只读 `explore` 子 agent；5b 加**可写**子 agent：跑在自己的 git worktree 里改代码，
> 改完**串行合并回父工作区并对冲突文件做检测**（先到先得）。对应 ROADMAP Phase 5 / §6 的「写型 worktree + 串行合并 + 冲突上报」。
>
> **工作流**：核心 Claude 已写好并验证；**全部测试交 Gemini**（核心改动小且已自测，本切片几乎只剩测试）。

## 已完成（Claude，本分支提交，勿改逻辑）
- `orchestrators/base.py`：`OrchestratorContext.subagent_claims: dict[str,str]`——跨写型子 agent 的「rel_path → 最先改它的子 agent 标签」，冲突检测用。
- `orchestrators/subagent.py`：
  - `AGENT_TOOLSETS["edit"] = {search, read_file, apply_patch}`、`WRITE_AGENT_TYPES = {"edit"}`、`WRITE_SUBAGENT_SYSTEM_PROMPT`。
  - `run_write_subagent(ctx, *, description, prompt, turn) -> (结论, 已合并文件列表)`：开 `WorktreeContext(ctx.original_workspace, sub_id)`，用 `tool_context.model_copy(update={"workspace": worktree})` 的 sub-ctx 跑写型子循环（`_drive_write_loop`，收集改动 rel 路径），再 `_merge_into_parent` 串行合并回 `ctx.tool_context.workspace`。非 git 仓库降级就地执行。
  - `_merge_into_parent`：逐文件合并，`subagent_claims` 里被别的子 agent 占用的文件 → 记为冲突、**保留先到者**、不覆盖。
- `orchestrators/_toolturn.py`：`dispatch` 按 `agent_type` 分流——`edit` → `run_write_subagent`（返回 changed 进父 changed_files）；其余 → `run_subagent`（只读，行为不变）。
- `tools/registry.py`：`dispatch` schema 的 `agent_type` enum 加 `"edit"` + 描述更新。
- **已自测**：worktree 隔离（apply_patch 经 sub-ctx 写进 worktree、父不动）；合并/冲突（同文件先到先得、冲突上报）；全量 374 passed 零回归。

## 边界（不许动）
- 不改 `run_write_subagent` / `_merge_into_parent` / 路由的逻辑与签名。
- 只读 `explore` 路径行为零变更；现有 374 passed 必须保持。
- 写型子 agent 在 loop 里本就**串行**（dispatch 非 read_only，不进只读并发批）——「并行执行」属可选增强，安全保证来自隔离 + 串行冲突合并，**不要求**真并发。

## Gemini 要做：全部测试（`tests/test_write_subagent.py`）
覆盖下方 check 点。优先用「构造 OrchestratorContext + fake edit client」或走 `RuntimeApp(tmp_path).run_task(...)` + monkeypatch（参考 `tests/test_subagent.py` 的 `test_subagent_e2e_loop` 写法：在 tmp git 仓库 init_project，monkeypatch `subagent.build_chat_client`/`build_routed_client` 让子 client 产出 apply_patch 工具调用）。

## Checkpoints
1. **toolset/schema**：`AGENT_TOOLSETS["edit"]` 含 `apply_patch`；`WRITE_AGENT_TYPES == {"edit"}`；`default_tool_registry()` 的 dispatch schema `agent_type` enum 含 `"edit"`。
2. **路由**：`_execute_tool_call_rich`/dispatch 在 `agent_type="edit"` 时调 `run_write_subagent` 并把其改动文件作为 changed 返回（可 monkeypatch `run_write_subagent` 计数/断言）；`explore` 仍走 `run_subagent`、返回 changed 为 `[]`。
3. **隔离 + 合并（端到端）**：在 tmp git 仓库里跑一个会触发 `dispatch(agent_type="edit")` 的（mock/fake）父循环，子 agent 用 apply_patch 改一个文件 → 该改动**最终出现在父工作区**，且子 agent 跑的过程隔离在 worktree（父文件只通过合并更新）。
4. **冲突上报（先到先得）**：两个写型子 agent 改**同一个文件** → 第二个的该文件进 conflicts、父工作区保留**第一个**子 agent 的版本；互不重叠的文件都正常合并。结论文本含 `CONFLICT`，`subagent_done` 事件带 `conflicts` 列表。
5. **非 git 降级**：非 git 仓库（或 worktree 不可用）时 `run_write_subagent` 不抛、就地执行并仍返回结论。
6. **全绿**：`PYTHONUTF8=1 uv run pytest -q`（374→更多 passed）+ `ruff check .` 全绿，零回归。

## 纪律
TDD；命令前置 `PYTHONUTF8=1`；ruff B023 默认参数绑定循环变量；只在分支 `phase5b-write-parallel-subagent` 提交；全绿后 `git push origin phase5b-write-parallel-subagent`。报告：新增 commit `git log --oneline`、pytest 统计行、ruff 结果、每个 check 点一句话。
