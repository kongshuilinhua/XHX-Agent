# Phase 6a — 长期记忆 / 跨会话上下文（MVP）

> 上下文管理的**第 ④ 轴（时间轴：跨会话）**。核心：「索引 + 选择性召回 = 预算化记忆」。
> 对应 ROADMAP §7。本切片 = 存储 + 确定性召回 + 注入 + 读写面；**suggest-confirm 自动抽取留 Phase 6b**。

## 分工
- **核心已由 Claude 写好**（本分支提交）：`memory/store.py`、`memory/recall.py`、compiler 注入、三编排器 system prompt 注入。**不要重写核心。**
- **Gemini 的活** = REPL `/remember`·`/memory` 面 + 可选 `xhx memory` CLI 子命令 + **全部测试**。

## 设计要点（已定，照此理解）
- 存储：`.xhx/memory/`，`MEMORY.md` 常驻索引 + 每条事实一个 frontmatter 文件（`name` / `description` / `metadata.type` + 正文）。与 `XHX.md`、`sessions/` 三者分明。
- 4 类型：`user` / `feedback` / `project` / `reference`（`store.MEMORY_TYPES`）。
- 召回：**确定性**——任务文本与每条记忆 `description`+`name`+正文做 token 重叠打分，取 top-N；**无 LLM 调用、可复现**。
- 生命周期防腐烂：召回时若记忆正文点名的相对路径文件已不存在，则**跳过该条**（`recall` 内 `_verify_record`）。
- 注入两处：① `compile_context_pack` 新增 memory 来源（priority 88，接现有预算/裁剪）；② 三 tool-calling 编排器（loop/plan/graph）system prompt 追加 `render_recalled_memories(...)`（**空时返回 ""，对现有行为零影响**）。

## 核心 API（Gemini 据此接 CLI/REPL + 写测试，勿改签名）
`xhx_agent/memory/store.py`
- `MEMORY_TYPES: set[str]`，`memory_dir(workspace: Path) -> Path`
- `MemoryRecord`（`name`/`description`/`mtype`/`body`/`path`）
- `slugify(text: str) -> str`
- `write_memory(workspace, *, name, description, mtype, body) -> Path`（写 frontmatter 文件 + 更新 `MEMORY.md` 索引行；同 name 覆盖）
- `list_memories(workspace) -> list[MemoryRecord]`（按 name 稳定排序）
- `parse_memory_file(path) -> MemoryRecord | None`（解析失败返回 None，不抛）
- `delete_memory(workspace, name) -> bool`

`xhx_agent/memory/recall.py`
- `recall_memories(workspace, query, *, limit=5, verify=True) -> list[MemoryRecord]`（确定性打分；`verify=True` 跳过点名文件已失踪的记忆）
- `render_recalled_memories(workspace, query, *, limit=5) -> str`（markdown 块；无命中返回 `""`）

## Checkpoints（Gemini 写测试覆盖）
1. **store round-trip**：`write_memory` 后 `list_memories`/`parse_memory_file` 能取回同样的 name/description/mtype/body；`MEMORY.md` 出现该条索引行；同 name 再写=覆盖不重复；非法 mtype 抛 `ValueError`。
2. **recall 确定性 + 排序**：构造 3 条记忆，与查询高度相关的排在前；同一查询多次调用结果**完全一致**；`limit` 生效；无命中返回 `[]`。
3. **生命周期**：一条记忆正文点名一个不存在的相对路径文件 → `recall_memories(..., verify=True)` 跳过它，`verify=False` 不跳。
4. **compiler 注入**：`.xhx/memory/` 有相关记忆时，`compile_context_pack` 的结果里出现 `kind` 以 `memory` 开头的 `ContextItem`；空记忆目录时无该项、且**现有用例全绿**。
5. **编排器注入**：mock 下 `--mode loop`（plan/graph 任一亦可）跑一个任务，且 `.xhx/memory/` 有相关记忆时，发给模型的 system 文本包含该记忆内容；记忆为空时 system 文本不含记忆块（行为零变更）。
6. **读写面 + 全绿**：`/remember <text>` 写入一条（默认 `mtype="project"`，`description` 取首句/前 N 字）并能被 `/memory` 列出；`xhx memory`（如实现）列出记忆；最终 `PYTHONUTF8=1 uv run pytest -q` 全绿 + `ruff check .` 全绿，零回归。

## 纪律
TDD（红→绿→回归→提交）；命令前置 `PYTHONUTF8=1`；ruff B023 用默认参数绑定循环变量；只在本分支提交、只 add 你写的测试/CLI 文件；全绿后 `git push origin phase6a-longterm-memory`。完成报告：新增 commit 的 `git log --oneline`、pytest 统计行、ruff 结果、每个 check 点一句话如何满足。
