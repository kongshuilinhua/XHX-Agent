# 会话 picker v2 实现计划（基于已上线的 v1 增量）

> **For agentic workers:** 这是对已合并的 session-restore v1 的修订。逐 Task、TDD、频繁提交。步骤用 `- [ ]`。

**Goal:** ① 启动不再自动打开上次会话（启动即新会话）；② 会话 picker 改 Claude 风格两行式；③ 加 `/sessions clear` 直接清理旧脏数据（不备份）。

**Architecture:** 纯 TUI + session.py 纯函数。v1 的 view-log 落盘/忠实恢复/前缀 resume 全部保留，仅改这三点。**不动编排器/模型层。**

参考：picker 视觉样式见对话中的 mockup（两行：标题 + 暗色 `时间·状态·轮数·…id尾8`）。

---

### Task 1: 撤回启动自动恢复（启动即新会话）

**Files:** Modify `src/xhx_agent/tui/textual_app.py`（`on_mount`、`handle_resume`）、`src/xhx_agent/runtime/config.py`；Test 同步删改。

**行为：**
- 删除 `on_mount` 末尾的自动恢复块（`if load_config(...).auto_resume: ... handle_resume(latest.run_id, auto=True)`）。启动恒为空白新会话。
- 移除 `ProjectConfig.auto_resume` 字段。
- `handle_resume` 去掉 `*, auto: bool=False` 参数及其分支，统一末尾提示为 `system> 已恢复会话（完整界面+记忆），直接提问即可继续`。
- **删测试**：`tests/test_config.py` 的 `test_auto_resume_*`；`tests/test_tui_textual.py` 的 `test_textual_app_auto_resume_on_startup`、`test_textual_app_no_auto_resume_when_disabled`。

- [ ] Step 1 实现 + 删上述测试
- [ ] Step 2 `python -m pytest -q` 全绿（确认无残留引用 `auto_resume`）
- [ ] Step 3 commit：`feat(tui): start fresh on launch — drop auto-resume (revert T6)`

---

### Task 2: 纯函数 `format_session_meta`（picker 第二行）

**Files:** Modify `src/xhx_agent/runtime/session.py`；Test `tests/test_session.py`

**行为：** 加 `def format_session_meta(entry: SessionEntry, now: datetime) -> str:`，返回暗色元信息行：`f"{相对时间} · {status} · {turn_count}轮 · …{run_id[-8:]}"`。相对时间复用现有 `format_session_line` 内的算法——把相对时间计算抽成内部 helper `_relative_time(t_str, created_at, now) -> str`，`format_session_line` 与 `format_session_meta` 都调用它（DRY）。

- [ ] Step 1 写失败测试：`updated_at=now-5分钟` 的 entry → 含 `"5分钟前"`、`status`、`"轮"`、run_id 尾 8 位；未来时间→`"刚刚"`。
- [ ] Step 2 跑 → FAIL
- [ ] Step 3 实现
- [ ] Step 4 跑 → PASS
- [ ] Step 5 commit：`feat(session): format_session_meta + shared _relative_time helper`

---

### Task 3: picker 两行式渲染 + 位置/快捷键提示

**Files:** Modify `src/xhx_agent/tui/textual_app.py`（`handle_sessions`、`present_picker` 调用处）

**行为：**
- 每个 picker 选项的 prompt 改为 **Rich `Text`/`Group` 两行**：第1行标题（`entry.task`，超长截断 `…`，亮色）；第2行 `format_session_meta(entry, now)`（暗色）。选项 id 仍是 `entry.run_id`。（Textual `Option` 接受 Rich 多行 renderable，箭头导航不变。）
- picker 标题：`Resume session ({N})`；输入框 placeholder：`↑/↓ 选 · Enter 恢复 · Esc 开新会话 · /sessions <词> 过滤`。
- 文本回显精简成一行摘要 `system> {N} 个会话（↑↓ 选，Enter 恢复，Esc 开新）`，不再逐条 dump（避免和 picker 重复刷屏）。
- 保留 v1 的 `/sessions <关键词>` 过滤与可滚动（无 `[:10]`）。

- [ ] Step 1 实现
- [ ] Step 2 全量测试不回归：`python -m pytest -q`（更新 `test_textual_command_console_handles_sessions_and_resume`、`test_textual_app_interactive_session_selection` 中对回显文案的断言为新格式；选项 id 仍是 run_id 不变）
- [ ] Step 3 commit：`feat(tui): two-line Claude-style session picker with hints`

---

### Task 4: `/sessions clear` 直接清理旧脏数据（不备份）

**Files:** Modify `src/xhx_agent/runtime/session.py`（新增清理函数）、`src/xhx_agent/tui/textual_app.py`（分发）；Test `tests/test_session.py`

**行为：**
- `session.py` 加 `def prune_legacy_sessions(workspace: Path) -> int:`：读 `history.jsonl`，**删除** `view_path` 为空的条目（不可忠实恢复的旧噪声），用保留下来的条目**重写** `history.jsonl`（**直接覆盖，不备份**——用户明确要求），返回删除条数。文件不存在→返回 0。
- `textual_app.py` 分发：`/sessions` 的 argument 若 `strip()=="clear"` → 调 `prune_legacy_sessions(self.workspace)`，`append_message(f"system> 已清理 {n} 条旧会话")` 并刷新；否则按关键词过滤（现状）。
- 帮助文本补 `/sessions clear - 清理无法恢复的旧会话`。

- [ ] Step 1 写失败测试：构造 3 条有 `view_path` + 2 条无 `view_path` 的 history → `prune_legacy_sessions` 返回 2，重读 `list_sessions` 只剩 3 条且都带 view_path；空文件→0。
- [ ] Step 2 跑 → FAIL
- [ ] Step 3 实现
- [ ] Step 4 跑 → PASS；加一个 TUI 测试：`/sessions clear` 后 `app.messages[-1]` 含 `已清理`。
- [ ] Step 5 commit：`feat(tui): /sessions clear prunes legacy view-log-less sessions (no backup)`

---

## 收尾验证（Claude 验收）
- [ ] `python -m pytest -q` 全绿；`ruff check` 三文件无新增违规。
- [ ] 真机：启动→空白新会话（不自动恢复）；`/sessions`→两行式列表（标题+暗色元信息、可滚、`/sessions <词>` 过滤）；`/resume <尾8位>`→忠实恢复；`/sessions clear`→旧脏数据消失、真会话仍在。

## Self-Review
- 覆盖：撤回自动打开(T1)、两行 picker(T2/T3)、清理命令(T4)。
- 占位符：无。命名一致：`format_session_meta`/`_relative_time`/`prune_legacy_sessions`。
- 兼容：v1 的 view-log/忠实恢复/前缀 resume 不动。
