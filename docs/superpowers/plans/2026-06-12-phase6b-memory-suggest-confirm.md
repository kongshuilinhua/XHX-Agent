# Phase 6b — 记忆自动抽取（suggest-confirm）

> Phase 6a 做了**显式**写入（`/remember`）；6b 补**自动抽取**：跑完（成功）后 LLM 提议耐久跨会话事实，
> 用户**一键确认**才落盘（防污染、保留学习）。对应 ROADMAP §7「写入」。

## 分工
- **核心已由 Claude 写好**（本分支提交）：`memory/extract.py`（抽取 prompt + 严格解析 + 去重）+ console 集成（`_maybe_suggest_memories` 跑后钩子 + `_confirm_memory` 确认 UI + `auto_memory` 标志）。**已真模型验证、不要重写。**
- **Gemini 的活** = `/automem on|off` 开关（REPL slash）+ **全部测试**。

## 设计要点（已定，照此理解）
- 抽取在 `CommandConsole.run_task` 收尾（`print_run_result` 后）调 `_maybe_suggest_memories(result)`：仅 `status=="success"` 且 `self.auto_memory` 时跑；全程 try/except **静默**，只有真正写入才打印 → mock 下确定性返回空、对现有用例零扰动。
- 严格输出格式（防误报）：模型只能输出 `NONE` 或每行 `MEMORY | type=.. | name=.. | desc=.. | body=..`；`body=` 之后整段为正文（允许含 `|`/`=`）。mock/无关文本一律解析为 `[]`。
- 候选对已有记忆 `name`(slug) 去重；上限 3 条/次；候选**绝不自动写盘**，逐条经 `_confirm_memory`（默认否，非交互视为否）确认后才 `write_memory`。

## 核心 API（Gemini 据此写测试，勿改签名）
`xhx_agent/memory/extract.py`
- `EXTRACTION_SYSTEM_PROMPT: str`
- `parse_memory_candidates(text, *, existing_names=None, limit=3) -> list[MemoryRecord]`（纯函数：严格解析 + 去重 + 截断）
- `propose_memories(client, task, transcript, *, existing_names=None, limit=3) -> list[MemoryRecord]`（调 `client.chat(messages, [])`，解析 `.content`）

`xhx_agent/cli/console.py`（已接好）
- `CommandConsole.auto_memory: bool`（默认 True）
- `_maybe_suggest_memories(result)`、`_confirm_memory(record) -> bool`

## Gemini 要做
1. `/automem on|off`：加进 `SLASH_COMMANDS` + `handle_command` 分发（翻转 `self.auto_memory`，无参时打印当前状态）+ `print_help` 命令表 + `completion.py` 补全。
2. 全部测试（追加到 `tests/test_memory.py` 或新建 `tests/test_memory_extract.py`）。

## Checkpoints（Gemini 写测试覆盖）
1. **严格解析**：`parse_memory_candidates` 对合法多行返回对应条数与正确 `mtype`；`"NONE"` / 空串 / 无关文本（如 `"Mock loop reply: ..."`）→ `[]`；非法 `type=` 或缺 `name=` 的行被丢弃。
2. **body 容错**：`body=` 后含 `|` 和 `=` 时正文完整保留。
3. **去重 + limit**：`existing_names` 命中的 slug 被跳过；超过 `limit` 截断。
4. **propose（fake client）**：注入返回固定 `.content` 的 fake client，`propose_memories` 产出对应候选；返回非格式文本 → `[]`。
5. **mock 确定性**：用真实 `MockModelClient` 作 client（或 mock profile）跑 `propose_memories` → `[]`（防误报）。
6. **suggest-confirm 集成**：monkeypatch `propose_memories` 返回 1 条候选 + monkeypatch `_confirm_memory`（或 `typer.confirm`）→ 确认时 `_maybe_suggest_memories` 调用 `write_memory` 落盘、否决时不写；`auto_memory=False` 或 `status!="success"` 时**完全不抽取**。
7. **`/automem` 开关**：`handle_input("/automem off")` 后 `auto_memory is False`、`"/automem on"` 后为 True；`/automem` 自动补全可命中。
8. **全绿**：`PYTHONUTF8=1 uv run pytest -q` 349→更多 passed + ruff 全绿，零回归。

## 纪律
TDD（红→绿→回归→提交）；命令前置 `PYTHONUTF8=1`；ruff B023 默认参数绑定循环变量；只在本分支提交、只 add 你写的测试/`/automem` 改动；全绿后 `git push origin phase6b-memory-suggest-confirm`。报告：新增 commit `git log --oneline`、pytest 统计行、ruff 结果、每个 check 点一句话。
