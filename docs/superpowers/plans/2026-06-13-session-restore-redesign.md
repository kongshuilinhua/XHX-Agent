# 会话恢复系统重做 实现计划

> **For agentic workers:** 本计划按任务逐项实现。每个任务给出精确文件路径、签名/行为、以及"验收 check 点"。编码主体交 Gemini；Claude 负责关键 check 点与验收。步骤用 `- [ ]` 跟踪。

**Goal:** 让 `/sessions` `/resume` 能忠实还原之前的会话——界面（含工具调用/运行标记/system 行）与模型记忆双份回填，启动自动接最近会话，列表清晰可滚可过滤。

**Architecture:** 在已有"模型 transcript（记忆）"之外，TUI 把忠实的界面时间线 `self.messages` 落盘成 **view-log**；恢复时双份回填（transcript→`prior_messages`，view-log→`self.messages`）。编排器/模型层零改动，改动只落在 `runtime/session.py`、`runtime/config.py`、`tui/textual_app.py`。

**Tech Stack:** Python 3.12 + Pydantic + Textual；测试用 pytest。

参考 spec：`docs/superpowers/specs/2026-06-13-session-restore-redesign-design.md`

---

## 文件结构

- `src/xhx_agent/runtime/session.py` — 持久化与纯逻辑：`SessionEntry` 新字段、`save_view_log/load_view_log`、`record_session` 新参数、新纯函数 `resolve_run_id`、`format_session_line`。
- `src/xhx_agent/runtime/config.py` — `ProjectConfig` 新增 `auto_resume`。
- `src/xhx_agent/tui/textual_app.py` — `run_task`（落盘 view-log + 后移 record）、`handle_resume`（忠实回填 + 前缀解析 + 提示）、`on_mount`（自动恢复）、`handle_sessions`（富列表 + 过滤）、`/new` 别名与命令分发。
- `tests/test_session.py` — 扩展：新字段往返、view-log 往返、`resolve_run_id`、`format_session_line`、向后兼容。
- `tests/test_config.py`（若不存在则新建）— `auto_resume` 默认值与读写。

> 设计取舍：把 `resolve_run_id`/`format_session_line` 放进 `session.py` 做成**纯函数**，让"前缀解析、列表行格式"可脱离 Textual 单测——这是 TUI 任务的主要 check 点来源。

---

### Task 1: `SessionEntry` 新字段 + view-log 读写 + `record_session` 新参数

**Files:**
- Modify: `src/xhx_agent/runtime/session.py`
- Test: `tests/test_session.py`

**接口/行为：**
- `SessionEntry` 增字段（全部带默认值，保证旧 JSONL 可解析）：
  - `view_path: str | None = None`
  - `turn_count: int = 0`
  - `updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())`
- 新函数（镜像现有 `save_transcript`/`load_transcript_messages`）：
  - `def view_log_path(workspace: Path, run_id: str) -> Path:` → `xhx_dir(workspace)/"sessions"/f"{run_id}.view.json"`
  - `def save_view_log(workspace: Path, run_id: str, lines: list[str]) -> str:` 写 JSON（`ensure_ascii=False, indent=2`），返回相对 workspace 的 POSIX 路径。
  - `def load_view_log(workspace: Path, rel_path: str | None) -> list[str] | None:` 缺文件/空路径返回 `None`。
- `record_session(...)` 增可选参数：`view_path: str | None = None`、`turn_count: int = 0`，写入 `SessionEntry`。签名向后兼容（默认值）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_session.py`）
  - `test_view_log_roundtrip`：`save_view_log(tmp_path, "run-v", ["user> hi", "  ⟶ tool search", "assistant> ok"])` 返回值 `endswith("run-v.view.json")`；`load_view_log` 读回完全相等。
  - `test_load_view_log_missing_returns_none`：缺文件路径与 `None` 都返回 `None`。
  - `test_session_entry_new_fields_default`：用旧式 JSON 行（无 `view_path/turn_count/updated_at`）`SessionEntry.model_validate_json` 不报错，`view_path is None`、`turn_count == 0`、`updated_at` 非空。
  - `test_record_session_stores_view_path_and_turn_count`：`record_session(tmp_path, "t", stub, conversation_id="c1", view_path=".xhx/sessions/run-x.view.json", turn_count=3)` 后，`load_latest_session` 的 `view_path`/`turn_count` 与传入一致。
- [ ] **Step 2: 跑测试确认 FAIL**
  Run: `python -m pytest tests/test_session.py -q`
  Expected: 新测试 FAIL（属性/函数不存在）。
- [ ] **Step 3: 实现**（按上面接口在 `session.py` 落地；Gemini 编码）
- [ ] **Step 4: 跑测试确认 PASS（含旧用例不回归）**
  Run: `python -m pytest tests/test_session.py -q`
  Expected: 全 PASS。
- [ ] **Step 5: Commit**
  `git add src/xhx_agent/runtime/session.py tests/test_session.py`
  `git commit -m "feat(session): view-log persistence + SessionEntry view_path/turn_count/updated_at"`

**Claude 验收 check 点：** 旧 `test_record_session_backward_compatible_stub`、`test_list_conversations_collapses_turns_of_one_conversation` 仍绿；新字段默认值不破坏既有 JSONL。

---

### Task 2: 纯函数 `resolve_run_id` 与 `format_session_line`

**Files:**
- Modify: `src/xhx_agent/runtime/session.py`
- Test: `tests/test_session.py`

**接口/行为：**
- `def resolve_run_id(entries: list[SessionEntry], token: str) -> tuple[str | None, list[str]]:`
  - 先精确匹配 `run_id == token`；命中返回 `(token, [])`。
  - 否则收集 `run_id` 以 `token` 开头**或**结尾的**去重** run_id 列表 `cands`：
    - `len(cands) == 1` → `(cands[0], [])`
    - `len(cands) == 0` → `(None, [])`
    - `len(cands) > 1` → `(None, cands)`（歧义，返回候选供提示）
- `def format_session_line(entry: SessionEntry, now: datetime) -> str:`
  - 形如：`{相对时间} | {status} | 轮{turn_count} | …{run_id尾8位} | {task单行,最多60字}`
  - 相对时间用 `updated_at`（解析失败回退 `created_at`）：`<60s→"刚刚"`，`<3600s→"N分钟前"`，`<86400s→"N小时前"`，否则 `"N天前"`。
  - `task` 把换行压成空格，超 60 字省略号。

- [ ] **Step 1: 写失败测试**
  - `test_resolve_run_id_exact_prefix_suffix`：精确命中；只给尾段（如 `"16d83a85"`）唯一命中；只给前缀（如 `"run-178"`）若唯一则命中。
  - `test_resolve_run_id_ambiguous_returns_candidates`：两个 run_id 同前缀 → 返回 `(None, [两个])`。
  - `test_resolve_run_id_miss`：无匹配 → `(None, [])`。
  - `test_format_session_line_shape`：构造 `updated_at` 为 `now-5分钟` 的 entry，返回串含 `"5分钟前"`、`status`、`"轮"`、run_id 尾 8 位、task 文本；超长 task 被截断带 `…`。
- [ ] **Step 2: 跑测试确认 FAIL**
  Run: `python -m pytest tests/test_session.py -q`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑测试确认 PASS**
  Run: `python -m pytest tests/test_session.py -q`
- [ ] **Step 5: Commit**
  `git commit -am "feat(session): pure helpers resolve_run_id + format_session_line"`

**Claude 验收 check 点：** 这两个纯函数是后续 TUI 行为的单测替身——它们绿了，`/resume <前缀>` 与列表展示的核心逻辑即受测。

---

### Task 3: `ProjectConfig.auto_resume`

**Files:**
- Modify: `src/xhx_agent/runtime/config.py`
- Test: `tests/test_config.py`（无则新建）

**接口/行为：** `ProjectConfig` 增 `auto_resume: bool = True`（默认开）。位置紧挨其它旋钮字段，加行内中文注释说明"启动自动接最近会话"。

- [ ] **Step 1: 写失败测试**（`tests/test_config.py`）
  - `test_auto_resume_defaults_true`：`default_config().auto_resume is True`。
  - `test_auto_resume_roundtrip`：写入 `auto_resume=False` 的 config.json，`load_config` 读回为 `False`；缺文件时回退默认 `True`。
- [ ] **Step 2: 跑测试确认 FAIL**
  Run: `python -m pytest tests/test_config.py -q`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑测试确认 PASS**
  Run: `python -m pytest tests/test_config.py -q`
- [ ] **Step 5: Commit**
  `git commit -am "feat(config): add auto_resume flag (default on)"`

---

### Task 4: `run_task` 落盘 view-log + 后移 `record_session`（关键时序）

**Files:**
- Modify: `src/xhx_agent/tui/textual_app.py`（`run_task`，约 576–584 行）

**行为（重排 + 新增）：** 现状 `record_session` 在 `apply_run_result`/`run finished` **之前**调用，会漏掉本轮结尾的 `assistant>`/`summary>`/`run finished` 行。改为：

```
self.last_result = result
self.apply_run_result(result)
self.append_message(f"system> run finished: {result.status}, verification: {result.verification}")
# 此刻 self.messages 已是本轮完整界面 → 落盘 view-log，再记录会话
view_path = save_view_log(self.workspace, result.run_id, self.messages)
turn_count = sum(1 for e in list_sessions(self.workspace) if e.conversation_id == self.conversation_id) + 1
record_session(self.workspace, task, result, conversation_id=self.conversation_id, view_path=view_path, turn_count=turn_count)
self._refresh_prior_messages(result)
self._maybe_suggest_memories(result)
self.run_pending_steer()
```
- 顶部 import 增 `save_view_log`, `list_sessions`（已 import `record_session`）。
- 注意：`save_view_log` 用 `self.workspace`（TUI 主工作区即 original，运行时 worktree 已在 runtime 内部切回）。

- [ ] **Step 1: 实现重排**（Gemini 编码）
- [ ] **Step 2: 全量测试不回归**
  Run: `python -m pytest -q`
  Expected: 全 PASS（此处主要确保未破坏既有测试；TUI 行为下一步手测）。
- [ ] **Step 3: Commit**
  `git commit -am "feat(tui): persist view-log after turn completes; record session at turn end"`

**Claude 验收 check 点（手测，关键）：**
- 跑真实 LLM（DeepSeek profile，见记忆 `real-llm-testing`），提一个会触发工具调用的问题，跑完后到 `.xhx/sessions/` 确认存在 `{run_id}.view.json`，内容是含 `⟶ tool` / `✓ tool` / `assistant>` / `summary>` / `run finished` 的完整行列表。
- 多轮后，最近一次 run 的 `.view.json` 应包含**全部历史轮**的行（累积）。

---

### Task 5: `handle_resume` 忠实回填 + 前缀解析 + 提示

**Files:**
- Modify: `src/xhx_agent/tui/textual_app.py`（`handle_resume`，约 1497–1553 行）

**行为：**
- 入口先做前缀解析：若 `load_session(run_id)` 为空，用 `resolve_run_id(list_sessions(...), run_id)`：
  - 唯一命中 → 用解析出的 run_id 继续；
  - 歧义（候选>1）→ `append_message("system> 多个会话匹配，请补全：" + ", ".join(候选))` 后 return；
  - 无命中 → 保持现有 `"Session '{run_id}' not found."`。
- 取到 `entry` 后，记忆与状态部分不变（`prior_messages = load_transcript_messages(...)`、`conversation_id`、`last_result`、`state`）。
- **界面回填改造**：
  ```
  view = load_view_log(self.workspace, entry.view_path)
  if view is not None:
      self.messages = list(view)          # 忠实回填
  elif messages:                          # 旧会话无 view_path → 保留现有有损重建分支
      self.messages.clear(); <现有 user>/assistant> 重建逻辑不动>
  ```
  即：**新增 view-log 优先分支，现有反推分支降级为 else 回退**，不要删。
- 末尾提示按入口区分（见 Task 6 的签名 `auto`）：
  - 手动 `/resume`：`system> 已恢复会话（完整界面+记忆），直接提问即可继续`
  - 替换原 `Switched follow-up context to session: {run_id}`。
- 顶部 import 增 `load_view_log`, `resolve_run_id`, `list_sessions`。

- [ ] **Step 1: 实现**（Gemini 编码）
- [ ] **Step 2: 全量测试不回归**
  Run: `python -m pytest -q`
- [ ] **Step 3: Commit**
  `git commit -am "feat(tui): faithful resume from view-log + run_id prefix resolve"`

**Claude 验收 check 点（手测，关键）：**
- 接 Task 4 的会话，`/resume <尾8位>` → 对话区**原样**还原工具调用、运行标记、system 行（与实时跑时一致，不再稀疏）。
- 删除某旧会话的 `.view.json` 模拟旧会话 → `/resume` 仍能回到有损但不报错的界面（回退分支生效）。
- 故意给一个会匹配多条的前缀 → 出现"多个会话匹配，请补全"提示。

---

### Task 6: `on_mount` 启动自动恢复 + `handle_resume(auto=...)`

**Files:**
- Modify: `src/xhx_agent/tui/textual_app.py`（`on_mount` 约 398–405；`handle_resume` 签名）

**行为：**
- `handle_resume` 签名加 `*, auto: bool = False`；`auto=True` 时末尾提示改为 `system> 已自动恢复最近会话；/clear 或 /new 开新对话，/sessions 切换其它会话`。
- `on_mount` 末尾追加（在 `widgets_ready=True`、`refresh_snapshot()`、focus 之后）：
  ```
  from xhx_agent.runtime.config import load_config
  from xhx_agent.runtime.session import load_latest_session
  if load_config(self.workspace).auto_resume:
      latest = load_latest_session(self.workspace)
      if latest is not None:
          self.handle_resume(latest.run_id, auto=True)
  ```
  - `load_latest_session` 返回全局最新 entry，即"最近一次会话的最新 run"，其 transcript/view-log 即完整会话。
  - 无历史 → 跳过，维持空白（现状）。

- [ ] **Step 1: 实现**
- [ ] **Step 2: 全量测试不回归**
  Run: `python -m pytest -q`
- [ ] **Step 3: Commit**
  `git commit -am "feat(tui): auto-resume latest conversation on startup (config.auto_resume)"`

**Claude 验收 check 点（手测，关键）：**
- 跑一轮会话后**退出**，重新 `xhx-agent` 进入控制台 → 启动即自动回填上次的界面+记忆，顶部状态/`run` 对上；**直接提问**（不先 resume）模型能接着上次内容答（解决"必须先 resume 才有记忆"）。
- `config.json` 设 `auto_resume=false` 后重进 → 启动空白、不自动恢复。
- 全新 workspace（无 `.xhx/sessions`）→ 启动空白、无报错。

---

### Task 7: `handle_sessions` 富列表 + 过滤 + `/new` 别名 + 命令分发

**Files:**
- Modify: `src/xhx_agent/tui/textual_app.py`（`handle_sessions` 约 1476–1495；命令分发约 767–771；帮助/补全列表）

**行为：**
- `handle_sessions(self, query: str = "")`：
  - `conversations = list_conversations(self.workspace)`；空则提示后 return。
  - `query` 非空 → 按 `entry.task` 子串（不区分大小写）过滤。
  - **去掉 `[:10]` 硬上限**（`WrappingOptionList` 本就可滚）；最新在前 `reversed`。
  - 行文本与 picker 选项标签都用 `format_session_line(entry, datetime.now(UTC))`；picker 选项 id 仍是 `entry.run_id`。
  - `set_detail("sessions", "\n".join(lines))` 同步右侧明细。
- 命令分发：
  - `/sessions` → `self.handle_sessions(argument)`（把已有参数透传）。
  - 新增 `/new` → 复用 `self.action_clear()`（开新会话）。
- 帮助文本与补全候选（约 695–697、716–717、466–467、501–502、1307–1309 等出现命令清单处）补上 `/new` 与 `/sessions <关键词>` 说明。保持各处一致。

- [ ] **Step 1: 实现**
- [ ] **Step 2: 全量测试不回归**
  Run: `python -m pytest -q`
- [ ] **Step 3: Commit**
  `git commit -am "feat(tui): richer /sessions list + query filter + /new alias"`

**Claude 验收 check 点（手测）：**
- `/sessions` 列表显示相对时间/状态/轮数/run_id 尾段/清晰标题，且条目>10 时可上下滚。
- `/sessions 方案` 只列标题含"方案"的会话。
- `/new` 清空开新会话；`/resume <尾8位>` 切回旧会话。

---

## 收尾：全量验证

- [ ] `python -m pytest -q` 全绿。
- [ ] 真实 LLM 端到端走查（记忆 `real-llm-testing` 的 DeepSeek profile + 控制台 UTF-8）：
  1. 新会话问 A → 触发工具 → 退出；2. 重进：启动自动恢复，界面忠实、直接追问 B 能接上记忆；3. `/new` 开新会话问 C；4. `/sessions` 看到两条清晰会话、`/resume` 在二者间来回切换，界面与记忆都对。
- [ ] 复核 spec 的"测试要点"逐条有对应任务覆盖。

## Self-Review（计划自检结论）
- **Spec 覆盖**：①模型记忆/必须先 resume→Task 6 自动恢复；②界面忠实→Task 1+4+5；③列表难用→Task 2+7；④重启丢失→Task 1 落盘 + Task 6 启动恢复。全覆盖。
- **占位符**：无 TBD/TODO，所有签名/行为具体。
- **类型一致**：`save_view_log/load_view_log/view_path/turn_count/updated_at/resolve_run_id/format_session_line/auto_resume/handle_resume(auto=)` 跨任务命名统一。
