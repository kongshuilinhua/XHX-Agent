# 会话恢复系统重做设计

- 日期：2026-06-13
- 范围：Textual 全屏控制台的会话持久化与 `/sessions` `/resume` 恢复体验；编排器/模型层不动
- 状态：已与用户确认，待转实现计划

## 1. 背景与问题

用户反馈 `/sessions` 会话功能"做得很差，无法完全恢复之前的会话"。逐项确认后，四个痛点**全都要解决**：

1. **模型像不记得**：记忆机制本身是工作的（每轮把"系统提示 + 历史 + 本轮"整段存成模型 transcript，下一轮经 `prior_messages` 喂回模型，见 `orchestrators/plan.py:61`、`runtime/session.py:41`）。但有两个坑：
   - `/resume` 只对**下一轮**生效。用户截图里是"先提问拿到烂答案、之后才 resume"，那一轮自然没记忆。
   - **程序启动时不会自动接上最近会话**（`prior_messages=None`），新开即失忆，必须手动 `/resume`。
2. **界面还原不完整**：`tui/textual_app.py` 的 `handle_resume()`（约 1529 行）是**从模型 transcript 反推 UI**，只挑 `role=user` 与有文本的 `role=assistant`，丢掉工具调用、工具结果、`system` 消息、以及 `plan/verify/changed/run-finished` 这些**只存在于 UI 日志、根本不在模型消息里**的内容。恢复后对话比实时跑时稀疏、不完整。
3. **`/sessions` 列表难用**：标题截断到 40 字、`run_id` 难认、硬编码封顶 10 条、无搜索。
4. **重启担忧**：JSONL + 每 run transcript 已落盘，重启后能列能 resume，但被上面三条拖累，体验差。

## 2. 目标与非目标

**目标**
- 恢复会话时**界面忠实还原**：工具调用、工具结果、运行标记、system 行原样回来，不只剩稀疏 user/assistant 文本。
- 解决"必须先 resume 才有记忆"：**启动自动接最近会话**（记忆 + 界面双份回填），且 `/sessions` 可随时切到任意旧会话。
- `/sessions` 列表好用：清晰标题、相对时间、轮数、可滚动、可按关键词过滤、`/resume` 支持 run_id 前缀。

**非目标 / 范围边界**
- **不动编排器/模型层**。view-log 是纯 TUI 关注点，爆炸半径限定在 `tui/textual_app.py` + `runtime/session.py`。
- 不做"边打字边实时筛"的 picker（要重写 picker，收益小）；搜索只做 `/sessions <关键词>` 静态过滤（YAGNI）。
- 不改 agent 的决策/编排行为。

## 3. 设计

### 3.1 核心思路：双份持久化，恢复时双份回填

一个**会话(conversation)** 横跨多次 run，用 `conversation_id` 归并（机制已存在，见 `list_conversations`）。在已有"模型 transcript"之外，再加一份**界面日志(view-log)**：

| 持久化 | 内容 | 用途 | 文件 |
|---|---|---|---|
| 模型 transcript（已存在） | 完整 OpenAI 消息（system/user/assistant/tool） | 喂回模型 → 记忆 | `.xhx/sessions/{run_id}.json` |
| 界面日志 view-log（新增） | `self.messages`（含工具调用、运行标记、system 行的 `list[str]`） | 回填 UI → 忠实界面 | `.xhx/sessions/{run_id}.view.json` |

关键事实（已核实代码）：
- `self.messages` 在**一个会话内跨轮持续累积、从不在轮间清空**（只在 `/clear` 时清），所以**最近一次 run 的 view-log 就是该会话至此的完整界面**——和模型 transcript 的累积语义一致，归并规则可复用。
- `self.messages` **确实是忠实的**：运行期每个事件经 `_timeline_line_for_event`（`tui/textual_app.py:1115`）翻译成时间线行后 `append` 进 `self.messages`，已涵盖工具调用 `⟶ tool`、工具结果 `✓/✗ tool`、graph 子 agent `▸ agent`、`⚙ verify`、`plan>`、权限 system 行、`assistant>`、`summary>`、`run finished`。故落盘即忠实，无需另造结构。

### 3.2 数据落盘（`runtime/session.py`，纯增量）

- 新增 `save_view_log(workspace, run_id, lines) -> str` 与 `load_view_log(workspace, rel_path) -> list[str] | None`，镜像现有 `save_transcript/load_transcript_messages`，写入 `original_workspace`（与模型 transcript 同目录）。
- `SessionEntry` 新增字段：
  - `view_path: str | None = None` —— 界面日志相对路径；旧条目为 `None` → 触发兼容回退。
  - `turn_count: int = 0` —— 该会话累计轮数，定义为 `list_sessions` 中同一 `conversation_id` 的条目数（一次 run = 一个用户轮次）；TUI 写盘前按当前 `conversation_id` 计数后传入。
  - `updated_at: str` —— 该 run 写入时间（列表排序/相对时间展示用，默认同 `created_at`）。
- `record_session(...)` 增加可选参数 `view_path: str | None = None`、`turn_count: int = 0`，由 TUI 计算后传入；签名向后兼容（默认值不破坏现有调用与测试）。

### 3.3 忠实恢复（`handle_resume`）

```
handle_resume(run_id):
    entry = load_session(run_id)
    prior_messages = load_transcript_messages(entry.transcript_path)   # 记忆，不变
    view = load_view_log(entry.view_path)                              # 新增
    if view is not None:
        self.messages = list(view)        # 原样回填，忠实界面
    else:
        self.messages = 旧的有损重建(prior_messages)   # 兼容旧会话
    self.conversation_id = entry.conversation_id or self.conversation_id
    self.append_message("system> 已恢复会话（完整界面+记忆），直接提问即可继续")
    refresh_snapshot()
```

- 现有"从 transcript 反推 UI"的代码降级为**仅旧会话的回退分支**，不删除（保证旧 `.xhx` 仍能恢复）。

### 3.4 启动自动接最近 + 可切换（`on_mount`）

- 启动时若 `auto_resume` 为真且存在历史会话 → 自动 `handle_resume(最近会话.run_id)`（等价于用户立刻 `/resume`，记忆+界面双份回填，`conversation_id` 续上）。提示 `system> 已自动恢复最近会话；/clear 开新对话，/sessions 切换其它会话`。
- 全新 workspace 无历史 → 维持现状空白。
- 开新会话：复用现有 `/clear`（清 `messages`/`prior_messages`/换 `conversation_id`），顺手加别名 `/new`。
- 配置项 `auto_resume`（默认开）可关，留逃生口（放 config，沿用现有 config 读取路径）。

### 3.5 `/sessions` 列表好用（`handle_sessions`）

- **标题**：用开场任务全文（`list_conversations` 已把 `task` 还原为开场任务），列表项展示不再截到 40 字（picker 可滚动横排有限，用合理宽度而非死截）。
- **元信息**：每项附 `updated_at` 相对时间（如 `3分钟前`）、`turn_count`、状态、短 `run_id`（尾 8 位）。
- **不再硬编码 10 条上限**：`present_picker` 的 `WrappingOptionList` 本就可滚动，去掉 `[:10]`，全量可滚。
- **过滤**：`/sessions <关键词>` 按标题子串过滤后再进 picker；无参数列全部。
- **resume 容错**：`/resume <run_id前缀>` 支持前缀/尾段匹配，唯一命中即恢复；多命中提示让用户补全。

### 3.6 改动边界与迁移

- view-log 由 **TUI** 在 `run_task` 结束、`record_session` 之时写盘并回传 `view_path`——编排器/模型层零改动。
- 迁移：旧会话无 `view_path`/`turn_count`/`updated_at` → Pydantic 默认值 + 恢复回退分支，老 `.xhx/sessions` 不报错、可恢复（仅界面退化为旧的稀疏还原）。

## 4. 数据流（一图）

```
每轮 run 结束（TUI.run_task）
  ├─ 模型 transcript 已由编排器写 {run_id}.json     → prior_messages（记忆）
  ├─ TUI: save_view_log(self.messages) → {run_id}.view.json
  └─ record_session(..., view_path, turn_count)      → history.jsonl 追加 SessionEntry

启动（on_mount, auto_resume）/ 用户 /resume run_id
  └─ handle_resume:
       load_transcript_messages → self.prior_messages   （模型记得）
       load_view_log            → self.messages          （界面忠实还原）
       续 conversation_id；下一轮提问直接带记忆+完整界面
```

## 5. 测试要点

- `save_view_log/load_view_log` 往返：写入再读出 `list[str]` 完全一致；空路径/缺文件返回 `None`。
- `SessionEntry` 新字段向后兼容：旧 JSONL 行（无新字段）能被 `model_validate_json` 解析，默认值正确。
- `handle_resume`：有 view_path → `self.messages` 等于落盘内容；无 view_path → 走旧重建分支不抛异常。
- `on_mount` 自动恢复：有历史→自动回填 `prior_messages` 与 `messages` 且 `conversation_id` 续上；无历史→空白；`auto_resume=false`→不自动恢复。
- `/sessions <关键词>` 过滤命中正确；`/resume <前缀>` 唯一命中可恢复、多命中给提示。
- 回归：现有 `tests/test_session.py` 全绿（新增参数默认值不破坏既有断言）。
