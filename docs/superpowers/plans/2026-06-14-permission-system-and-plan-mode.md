# 权限系统(越界=询问/权限模式/自动放行) + plan 模式两段式重铸 Implementation Plan

> **For agentic workers (Gemini):** 自包含、可冷启动。按 Task 顺序 TDD 推进,步骤用 `- [ ]`。完成交回,Claude 两段式审查 + 全量回归 + 真模型联调 + 合并。
> **本文分两部分,可独立交付:Part A = 权限系统;Part B = plan 模式两段式。建议先 A 后 B**(B 的"只读阶段"复用 A 的内核拦截点)。

**Goal:**
1. **Part A**:让 `read_file`/`search`/`apply_patch` 碰到工作区外路径时**不再硬抛 `ValueError`,而是走确认流弹框询问**;批准后把该目录加进会话级白名单(同目录不再问)。新增**权限模式** `default / auto / bypass`(`/perm` 切换),`auto`=自动放行非危险操作。
2. **Part B**:把 `plan` 编排器从"自主 plan-and-execute"改成**两段式**:Phase1 只读规划 → 模型调 `present_plan` → 弹框 **执行/修改/取消** → Phase2 执行+验证。

**根因回顾(为什么要做):** 用户在 `XHX-Agent` 目录启动后让 agent "分析 `D:\pycharmprojects\all-in-rag`",结果每次 `read_file` 都被 `src/xhx_agent/tools/read_file.py:18` 的 `Path is outside workspace` 硬抛 → 模型退化成用 `terminal type` 逐文件读 → 20+ 轮、最终 failed。terminal 工具不沙箱(能穿透),文件工具却硬抛且够不到确认流,这种**不一致**是病灶。

---

## 当前状态(关键事实,带 file:line)

- **越界硬抛**:`src/xhx_agent/tools/read_file.py:15-22` `_resolve_inside()` 越界即 `raise ValueError`;`src/xhx_agent/tools/patch.py:281` 同理。`search` 则只在 workspace 内跑(`src/xhx_agent/tools/search.py:7-24`,`cwd=workspace`)。
- **确认流已存在但只接了 terminal**:`SafeExecutionKernel.run_command_tool`/`run_verification` 收 `confirm_callback`(`src/xhx_agent/safety/kernel.py:78-128`),terminal 风险档 CONFIRM 时调它(`src/xhx_agent/tools/terminal.py:32-49`)。TUI 侧 `confirm_terminal_command`(`src/xhx_agent/tui/textual_app.py:689-705`)用 `PendingConfirmation` 事件+超时弹框,并有 `/allow` `/deny` 预批(`textual_app.py:719-728`)。
- **文件工具够不到确认流**:`SafeExecutionKernel.execute_tool`(`kernel.py:31-51`)**不收 `confirm_callback`**;它只 `decide_tool`(查 read_only/destructive 标志,`safety/policy.py:37-57`)然后 `tool_registry.execute`。越界异常在 `_execute_tool_call_rich` 被泛 `except` 兜成 `[tool error]`(`src/xhx_agent/orchestrators/_toolturn.py:135-139`)——就是截图里的红 error。
- **`ToolContext`**:`src/xhx_agent/tools/registry.py:32-36`,只有 `workspace` + `max_file_bytes`。
- **权限模式不存在**:现有 `mode` 是**编排范式**(plan/loop/linear/graph,`orchestrators/registry.py:18-23`),不是权限模式。TUI 的 `state.mode` 与 `/mode`(`textual_app.py:591-593`)都指编排范式。
- **plan 编排器现状**:`src/xhx_agent/orchestrators/plan.py` 是 Plan-and-Execute 一条龙(`_drive` 批量规划+执行,`_verify_and_repair` 验证+≤2 自修复),**无只读阶段、无审批闸门**。系统提示词 `PLAN_SYSTEM_PROMPT`(`plan.py:22-30`)直接让它 `apply_patch`。
- **OrchestratorContext**:`src/xhx_agent/orchestrators/base.py:39-71`,已有 `confirm_callback`/`tool_context`/`event_callback`/`assume_yes` 等字段——新回调照此加。

---

# Part A — 权限系统

**Architecture:** 在**内核执行入口**(`execute_tool`)做路径裁决,而不是在工具内部抛错。引入 `PermissionMode` + 会话级 `allowed_dirs` 白名单。越界且未授权时,按权限模式决定:`bypass`/`auto`→放行并记目录;`default`→调 `confirm_callback` 弹框,批准则把目录加进 `allowed_dirs`(同目录后续静默放行),拒绝则返回**干净的 `status="denied"` 结果**(不是红 error)。

**Tech Stack:** 复用现有 `confirm_callback`/`PendingConfirmation`/`/allow`-`/deny`、`PolicyDecision`、pydantic、pytest。

## File Structure(Part A)

- `src/xhx_agent/safety/permission_mode.py` — **新增** `PermissionMode` 枚举 + 文案/循环切换。
- `src/xhx_agent/tools/paths.py` — **新增** `resolve_with_scope(workspace, allowed_dirs, path) -> (target, in_scope: bool)`,纯函数,供工具与内核共用。
- `src/xhx_agent/tools/read_file.py` / `search.py` / `patch.py` — 越界判定**移除硬抛**,改为返回/抛一个可被内核识别的 `PathOutsideScope`(带 resolved dir),或直接由内核预检(见下)。
- `src/xhx_agent/tools/registry.py` — `ToolContext` 加 `allowed_dirs: list[Path] = []` 与 `permission_mode: str = "default"`。
- `src/xhx_agent/safety/kernel.py` — `execute_tool` 增参 `confirm_callback`;新增越界预检 + 授权流。
- `src/xhx_agent/orchestrators/_toolturn.py` — `execute_tool(...)` 调用处把 `ctx.confirm_callback` 传进去(`_toolturn.py:119`)。
- `src/xhx_agent/runtime/app.py` — 构造 `ToolContext` 处注入 `permission_mode`/`allowed_dirs`(`runtime/app.py:174` 附近);`allowed_dirs` 在一次会话内可被授权流就地追加。
- `src/xhx_agent/runtime/config.py` — `AppConfig` 加 `default_permission_mode: str = "default"`。
- `src/xhx_agent/tui/textual_app.py` — **Shift+Tab 循环切换权限模式**(对标 Claude Code) + 状态条展示;越界询问复用 `confirm_terminal_command` 路径(把"读取工作区外目录 X?"作为 prompt 文本)。`/perm [mode]` 仅作可选的显式设置/查看(非主入口)。

## 关键设计约定(必须遵守)

1. **安全底线不破**:越界**写**(apply_patch/重定向)即使在 `auto` 也要弹框确认;`bypass` 也仅放行非 DENY 档,DENY 档 terminal 命令(rm/curl/sudo…)永远拒(`safety/risk.py` 既有逻辑不动)。对标 Claude Code"safetyCheck bypass-immune"。
2. **目录级授权,不是文件级**:用户批准 `D:\...\all-in-rag\README.md` 时,把其**父目录(或用户确认的根)** 加进 `allowed_dirs`,使整次"分析该项目"只问一次。授权粒度 = 目录子树(`target == d or d in target.parents`)。
3. **denied 是干净结果,不是异常**:越界被拒返回 `ToolExecutionResult(status="denied", summary="用户拒绝访问工作区外路径: X", ...)`,让模型看到可读反馈并改走别的路,而非红 error。
4. **无人值守**:`assume_yes=True` 或 `confirm_callback is None` 时,`default` 模式对越界**读**默认拒绝并给清晰 summary(不阻塞);`auto`/`bypass` 放行。(避免 benchmark 卡死。)
5. **会话级、不落盘**(本期):`allowed_dirs` 活在 `ToolContext`/会话内存,进程退出即清。落盘成规则(Claude Code 式 settings)留作后续。

---

### Task A1: PermissionMode 枚举 + 配置

**Files:** 新增 `src/xhx_agent/safety/permission_mode.py`;改 `src/xhx_agent/runtime/config.py`;Test `tests/test_permission_mode.py`(新增)

- [ ] **Step 1:写测试** — `PERMISSION_MODES == ("default","auto","bypass")`;`next_permission_mode("default")=="auto"`,`"auto"→"bypass"`,`"bypass"→"default"`;`permission_mode_from_string("x")=="default"`(未知兜底)。
- [ ] **Step 2:实现** `permission_mode.py`:常量元组、`next_permission_mode(m)`、`permission_mode_from_string(s)`、`permission_mode_title(m)`(中文文案,供状态条)。
- [ ] **Step 3:** `AppConfig` 加 `default_permission_mode: str = "default"`(`runtime/config.py`),`write_default_config` 写出该键。
- ✅ **Check:** `pytest tests/test_permission_mode.py` 绿;`xhx-agent config list` 能看到新键。

### Task A2: 路径作用域纯函数 + ToolContext 扩展

**Files:** 新增 `src/xhx_agent/tools/paths.py`;改 `src/xhx_agent/tools/registry.py`;Test `tests/test_paths_scope.py`

- [ ] **Step 1:写测试**(纯函数,无 IO 依赖用 tmp_path):
  - 工作区内相对路径 → `in_scope=True`;
  - 工作区外绝对路径(如另一 tmp 目录)→ `in_scope=False`,且返回的 `resolved` 指向该外部 target;
  - 外部路径但其父目录在 `allowed_dirs` → `in_scope=True`;
  - `..` 逃逸 → 解析后按真实位置判定(逃出工作区且不在白名单 → False)。
- [ ] **Step 2:实现** `resolve_with_scope(workspace, allowed_dirs, path) -> ResolvedScope(target: Path, in_scope: bool, outside_root: Path | None)`。`outside_root` = 越界时该 target 的父目录(给授权用)。
- [ ] **Step 3:** `ToolContext` 加 `allowed_dirs: list[Path] = []` 与 `permission_mode: str = "default"`(pydantic 默认值)。
- ✅ **Check:** `pytest tests/test_paths_scope.py` 绿。

### Task A3: 内核越界裁决 + 确认流接入

**Files:** 改 `src/xhx_agent/safety/kernel.py`、`src/xhx_agent/tools/read_file.py`/`search.py`/`patch.py`、`src/xhx_agent/orchestrators/_toolturn.py`;Test `tests/test_safety_kernel.py`(扩充)

- [ ] **Step 1:写测试**(用 mock `confirm_callback`):
  - `default` + 越界读 + `confirm_callback` 返回 True → 工具执行成功,且 **`tool_context.allowed_dirs` 被追加该目录**;同一目录第二次读 **不再调** `confirm_callback`。
  - `default` + 越界读 + `confirm_callback` 返回 False → 结果 `status=="denied"`,summary 含路径,**不抛异常**。
  - `auto` + 越界读 → 直接成功,`confirm_callback` **未被调用**。
  - `auto` + 越界**写**(apply_patch 到外部)→ **仍调** `confirm_callback`(安全底线)。
  - `assume_yes=True` + `confirm_callback=None` + `default` + 越界读 → `status=="denied"`,不阻塞。
- [ ] **Step 2:重构工具越界判定** — `read_file`/`search`/`patch` 内部改用 `resolve_with_scope`(传入 `allowed_dirs`);**越界不再自己抛**,而是当 `in_scope=False` 时由**内核**在 `execute_tool` 里先行裁决(推荐:内核预检,工具保持"给了就读")。`search` 越界根目录时,用越界根作为 `rg` 的 `cwd`/`path`。
- [ ] **Step 3:** `execute_tool` 增参 `confirm_callback: ConfirmationCallback | None = None`。流程:解析路径作用域 → `in_scope` 直接走原逻辑;越界则按 `permission_mode` + 操作类型(读/写)裁决(放行/弹框/denied),放行时 `ctx.tool_context.allowed_dirs.append(outside_root)` 并 `record_policy`。
- [ ] **Step 4:** `_toolturn.py:119` 调用处补 `confirm_callback=ctx.confirm_callback`。
- ✅ **Check:** `pytest tests/test_safety_kernel.py tests/test_tool_registry.py` 绿;审计链(`policy_decision` trace)对越界授权有记录。

### Task A4: TUI 接线(/perm + 越界询问文案 + 状态条)

**Files:** 改 `src/xhx_agent/tui/textual_app.py`;`src/xhx_agent/runtime/app.py`(注入 permission_mode/allowed_dirs)

- [ ] **Step 1:** `run_task`→`OrchestratorContext`→`ToolContext` 一路把 `permission_mode`(来自会话状态/config)与共享的 `allowed_dirs` 列表传下去;授权流就地 append 的目录在**同一会话后续 run 仍生效**(把 `allowed_dirs` 挂在 app/session 级而非每 run 新建)。
- [ ] **Step 2:Shift+Tab 循环切换(主入口)。** Textual 里 Tab/Shift+Tab 默认是焦点遍历,**必须拦截**,否则会去切焦点而非切模式。**推荐做法:在 `on_key`(`textual_app.py:1589`)开头、早返回(`if self._picker_on_select is None: return`,`:1593`)**之前**插入:
  ```python
  if event.key == "shift+tab":
      event.stop(); event.prevent_default()   # 两个都要,挡住 Textual 内置焦点遍历
      self.action_cycle_permission_mode()
      return
  ```
  并实现 `action_cycle_permission_mode()`:`self.state.permission_mode = next_permission_mode(self.state.permission_mode)`,更新传给后续 run 的共享值,append 一条 `system> 权限模式: <title>`,置脏触发重绘。`ConsoleState` 加 `permission_mode: str = "default"` 字段。
  > 备选(不如 on_key 稳):`BINDINGS` 用 `Binding("shift+tab", "cycle_permission_mode", "Perm", priority=True)`(需 `from textual.binding import Binding`)。**无论哪种,都必须实测 Shift+Tab 不再移动焦点**。
- [ ] **Step 3:状态条** 加 `perm: <mode>`(`textual_app.py:138` 渲染 `state.status / mode / turn` 那行同处加一段;模式文案用 `permission_mode_title`)。
- [ ] **Step 4:`/perm` 仅作可选显式入口** — `/perm` 显示当前,`/perm auto|default|bypass` 直接设置(便于脚本/无 TTY)。**不是主入口**,主入口是 Shift+Tab。
- [ ] **Step 5:** 越界询问走现有 `confirm_terminal_command` 同一通道(prompt 文本=「允许读取工作区外目录?\n<path>」),`/allow`/`/deny` 自动复用。
- ✅ **Check(我来验收,真模型):** 在 `XHX-Agent` 里启动 → 输入"分析 D:\pycharmprojects\all-in-rag" → 第一次读弹框 → `/allow` → 后续 read_file 全绿、无红 error、无逐文件 type;按 **Shift+Tab** 状态条在 `default→auto→bypass→default` 循环、**焦点不跳**;切到 `auto` 后再分析另一外部目录**不弹框直接读**。

---

# Part B — plan 模式两段式

**Architecture:** `PlanOrchestrator` 拆成 **Phase1 只读规划 → present_plan 闸门 → Phase2 执行**。Phase1 用过滤后的只读 schema + 内核 `read_only_phase` 兜底拦写;模型调新工具 `present_plan({plan, files_to_change})` 表示规划完;orchestrator 暂停并调 `plan_review_callback(plan) -> PlanReview{decision, feedback}`;执行/修改/取消三分支。Phase2 复用现有 `_drive`+`_verify_and_repair`(几乎不改)。

**Tech Stack:** 复用 `_drive`/`_verify_and_repair`/`tool_schemas`、事件系统、`PendingConfirmation` 模式。

## File Structure(Part B)

- `src/xhx_agent/orchestrators/plan.py` — 两段式重写 run();新 `PLAN_PHASE1_PROMPT`(只读规划)。
- `src/xhx_agent/tools/registry.py` — 加 `present_plan` 工具定义(仅 plan 模式注入,或全局存在但仅 plan 用)。
- `src/xhx_agent/orchestrators/base.py` — `OrchestratorContext` 加 `plan_review_callback: Callable[[str], PlanReview] | None`;定义 `PlanReview` dataclass。
- `src/xhx_agent/safety/kernel.py` / `tools/registry.py` — `read_only_phase` 标志:为真时 `apply_patch`/命令工具一律 `denied`(兜底,即使 schema 漏了)。
- `src/xhx_agent/tui/textual_app.py` — `plan_proposed` 事件 → 三选一弹框(执行/修改/取消;修改收一行反馈),回填 `plan_review_callback`。
- `src/xhx_agent/runtime/events.py` — 加 `plan_proposed` 事件类型(如需)。

## 关键设计约定(Part B)

1. **只读靠双保险**:Phase1 ① schema 只给 `search/read_file/repo_query/dispatch(explore)/present_plan`;② 内核 `read_only_phase=True` 时硬拦任何写/命令工具 → `denied`。
2. **`present_plan` 是显式信号**,不是"停下说话"。模型若直接出纯文本无 present_plan,视为澄清/未完成 → 继续(或提示其调用 present_plan)。
3. **三分支**:执行→关 `read_only_phase`、复用现有执行管线,把 plan 文本作为已批准规范追加进 messages;修改→把 `feedback` 作为新 user 消息,回 Phase1 重规划(轮数累加,设上限防死循环);取消→`status="cancelled"`,无改动返回。
4. **无人值守兜底**:`plan_review_callback is None` 或 `assume_yes` → 自动"执行"(保持 CLI/benchmark 行为)。
5. **复用而非重写** Phase2:`_drive`(执行)+`_verify_and_repair` 原样调用。

---

### Task B1: present_plan 工具 + PlanReview 类型 + read_only_phase 兜底

**Files:** 改 `tools/registry.py`、`orchestrators/base.py`、`safety/kernel.py`;Test `tests/test_tool_registry.py`、`tests/test_safety_kernel.py`

- [ ] **Step 1:写测试** — `read_only_phase=True` 时 `execute_tool`/`run_command_tool` 对 `apply_patch`/`terminal`/`verify` 返回 `status=="denied"`;只读工具照常。`present_plan` 定义存在且 schema 含 `plan`(必填)、`files_to_change`(可选数组)。
- [ ] **Step 2:** 加 `present_plan` ToolDefinition(read_only=True,runner 返回结构化 plan 载荷);`PlanReview` dataclass(`decision: Literal["execute","revise","cancel"]`, `feedback: str | None`);`OrchestratorContext` 加 `plan_review_callback`。
- [ ] **Step 3:** `ToolContext`/kernel 加 `read_only_phase` 兜底拦截。
- ✅ **Check:** 相关单测绿。

### Task B2: PlanOrchestrator 两段式重写

**Files:** 改 `orchestrators/plan.py`;Test `tests/test_plan_orchestrator.py`(新增/扩充,用 MockModel)

- [ ] **Step 1:写测试**(MockModel 脚本化):
  - 模型先发若干只读工具调用,再调 `present_plan` → orchestrator 调 `plan_review_callback` 拿到 `execute` → 进入 Phase2 → 产生 changed_files。
  - `revise` 分支:反馈被作为 user 消息回喂,模型重规划后再 present_plan。
  - `cancel` 分支:`status=="cancelled"`,`changed_files` 为空,**无 apply_patch 被执行**。
  - Phase1 中模型若误发 `apply_patch` → 被 `denied`(双保险)。
  - `plan_review_callback is None` → 自动执行(无人值守)。
- [ ] **Step 2:** 重写 `run()`:Phase1 用只读 schema + `read_only_phase=True` 驱动到 `present_plan`;emit `plan_proposed`;调 `plan_review_callback`;按三分支走;execute 时关 `read_only_phase`、复用 `_drive`+`_verify_and_repair`。新 `PLAN_PHASE1_PROMPT`。
- ✅ **Check:** `pytest tests/test_plan_orchestrator.py` 绿。

### Task B3: TUI 审批弹框

**Files:** 改 `tui/textual_app.py`

- [ ] **Step 1:** 监听 `plan_proposed` 事件 → 展示计划文本 + 三选项(执行/修改/取消);"修改"再收一行反馈。通过 `plan_review_callback` 回填(仿 `PendingConfirmation` 的事件+超时机制)。
- ✅ **Check(我来验收,真模型):** `/mode plan` → 提一个改代码需求 → 看到**只读探查**(无 apply_patch)→ 弹出计划 → 选"修改"补一句 → 重新出计划 → 选"执行" → 真正改文件并验证。

---

## 验收(Claude 统一回归,两部分都完成后)

- [ ] `pytest`(全量)绿,无新增 flaky。
- [ ] `ruff`/类型检查(项目既有)通过。
- [ ] **真模型联调**(DeepSeek profile,控制台 UTF-8):
  1. Part A:`XHX-Agent` 内分析 `all-in-rag`,确认"问一次→授权→流畅读",对比改造前的"逐文件 type/红 error 消失"。
  2. Part B:plan 模式跑一个真实小改动,确认只读→计划→修改→执行闭环。
- [ ] 安全回归:越界写在 `auto` 仍弹框;DENY 档命令在 `bypass` 仍被拒。
- [ ] 文档:`README`/`xhx.md` 补 `/perm`、plan 两段式、权限模式说明。

## 不在本计划(后续)

- 权限规则**落盘**(Claude Code 式 settings.json allow/deny/ask 规则、`additionalWorkingDirectories` 持久化)。
- `auto` 模式的**分类器**版本(用小模型判危险度);本期 `auto`=按风险档规则放行,不调模型。
- `--workspace`/`--cwd` CLI 参数(有了目录授权后非必需,可选补)。
