# XHX-Agent 前瞻路线图（Roadmap）

> 本文是**前瞻性**的战略规划——"接下来要做什么、最终做成什么"。
> 已完成的版本史见 [`docs/02-version-roadmap.md`](docs/02-version-roadmap.md)（回顾性历史文献）。
> 最近更新：2026-06-10

---

## 1. 项目定位（North Star）

**作品集 / 学习项目，目标是深度展示 agent 架构功底。**

- 北极星：能证明"真懂 agent 架构"，且能讲出完整故事（面试 / 简历 / 技术分享）。
- 原则：**保留独特设计 + 补齐好用度 + 每一步都能跑**。做减法、不注水。

---

## 2. 最终架构：统一 tool-calling 协议 + 一套基座 + 3 个「真范式」

核心思想：**tool-calling 是统一的底层模型协议**（手写 plan-JSON 全部退役）；三种范式**只在控制流上不同**，协议恒定——让"三范式对比"成为变量只剩控制流的干净实验。

```
        ┌──────── 3 真范式（只差控制流，协议都用 tool-calling）────────┐
        │  plan              graph              loop                   │
        │  Plan-Execute      多 agent 工作流     ReAct tool-use loop     │
        │  前置规划→批量执行  coordinator→        交错 think→act→观察     │
        │  （必要时 replan）  execute→review      →再 think（Claude式）   │
        └──────────────────────────────────────────────────────────────┘
                              ↓ 共用 ↓
   ┌──────── 统一 tool-calling 协议（消息历史 + 原生 tool_calls）────────┐
   └─────────────────────────────────────────────────────────────────────┘
                              ↓ 共用 ↓
   ┌──────────────────── 一套共享基座 ────────────────────┐
   │ Context Pack · Repo Intel · Safe Kernel · Verify · Evidence │
   └─────────────────────────────────────────────────────────────┘
   支撑机制（不是范式）：停止策略（原 linear）· DAG 并发调度（原 dag）
```

**3 范式各对应一个公认 agent 模式**，互不重叠：

| 范式 | 概念 | 模型介入方式 | 适合 | 状态 |
|:--|:--|:--|:--|:--|
| `plan` | Plan-and-Execute | 前置规划一次→批量执行→必要时 replan；模型一轮产出**一组** `tool_calls` | 步骤清晰、少 LLM 调用的批量任务 | ✅ tool-calling 已落地（3a，`--mode plan`）；`linear`/默认收敛 → 3b |
| `graph` | 多 agent 工作流（LangGraph） | coordinator → execute → review，条件重执行（≤2 轮），各节点内用 tool-calling | 规划/执行/复核分离 | 🚧 由现有 `graph`/`dag` 迁移 |
| `loop` | ReAct tool-use loop（Claude Code 式） | 每步问模型，看到每个工具结果再决定下一步；回文本=对话，回 `tool_calls`=执行再循环 | 对话 + 真实改代码，最好用 | 🚧 新建（Phase 1 主攻） |

**命名修订（旧→新）**：原 `loop`（自主 plan-execute）→ **`plan`**；新建的 tool-use 循环 → **`loop`**（它才真在"循环"）；`graph` 不变。
**收敛**：原 `linear`（首改即停）→ `plan` 的停止策略开关；原 `dag`（Kahn 并发调度）→ `graph` 的执行层。

---

## 3. 能力清单（保留 / 改造 / 新增）

> 图例：✅ 已实现 · 🚧 本次新增/改造 · ⚠️ 取舍/风险。**注意**：部分 ✅ 能力（如 `mock`、会话、`benchmark`、MCP）会在"改造/新增"里被扩展或重写——同一项出现在两处不是矛盾，是"保留能力、升级实现"。

**保留（现有独特资产）**
- ✅ Context Pack 编译器：tiktoken 预算、优先级裁剪、历史压缩
- ✅ Safe Execution Kernel：risk 三档（safe/confirm/deny）、worktree 隔离、checkpoint/restore
- ✅ Repo Intelligence：ast + tree-sitter，JSON + SQLite，增量刷新
- ✅ 验证路由 + 有界自修复（≤2 轮）
- ✅ Evidence 证据链 + 确定性回放（replay）
- ✅ 会话恢复：`--continue` / `--resume` / `sessions`（Phase 2 升级为完整历史持久化，见"改造"）
- ✅ 入口：CLI `run` / REPL `chat` / TUI / JSON-RPC
- ✅ 离线 `mock` profile、benchmark、Skills/Hooks/MCP
- ✅ LLM 接入：OpenAI 兼容（**DeepSeek 已真实连通**）

**改造（迁到 tool-calling）**
- 🚧 模型协议：手写 plan-JSON `{summary,status,steps}` 全退役 → **原生 `tool_calls` + 消息历史**
- 🚧 `mock` provider：改成**模拟 `tool_calls`**，保证离线/CI 仍可跑
- ✅ 会话管理：现有 `--continue`/`--resume` 从"**摘要续接**"升级为"**完整消息历史持久化**"（落盘 `loop` 的 H，真正还原整段对话）；与长期记忆（§7）分工——会话=单次完整状态，记忆=跨会话事实（Phase 2c 已落地，缺 transcript 的老会话回退摘要）
- 🚧 现有 `loop`/`linear` → `plan` 范式（**plan 部分已落地（3a）**：`--mode plan` 已走 tool-calling 批量规划+验证路由+有界修复；`linear`/默认收敛留 3b）；现有 `graph`/`dag` → `graph` 范式
- ⚠️ 取舍：依赖模型支持 function calling（DeepSeek 支持；放弃对不支持 tool-calling 模型的兼容）

**新增**
- 🚧 `loop` 范式：ReAct tool-use 统一循环（对话 + 干活）
- ✅ 《读 Claude Code 源码学到的》经验文档（学习产物 + 实现图纸）→ [docs/claude-code-learnings.md](docs/claude-code-learnings.md)

---

## 4. 分阶段（每步可跑、可 demo、可讲）

- **Phase 0**（✅ 已实现）：《读 Claude Code 源码学到的》经验文档 → [docs/claude-code-learnings.md](docs/claude-code-learnings.md)。
- **Phase 1**（✅ 已实现）：**tool-calling 基础设施 + `loop`(ReAct) MVP** —— 客户端 + **声明式工具接口**（schema + 风险档 + readonly/destructive + executor）+ 消息历史 + mock 模拟；支持**对话 + `read_file`/`search`/`apply_patch`**（读+写）。详见 [设计文档](docs/superpowers/specs/2026-06-10-agent-tool-calling-conversation-design.md)。
- **Phase 1 终审遗留 ✅ 已完成（切片 2a，2026-06-10）**：① TUI 渲染 `RunResult.answer` ✅；② schema 单一来源（`ToolDefinition` 纳入 runner + 自写 schema 派生校验，替换 `_validate_arguments`）✅；③ `read_only/destructive` 接 `decide_tool` 风险门控 ✅。另：`loop` 只读 tool_calls 并发 ✅。（confirm 回路随 Phase 2b 的 terminal 一起做。）
- **Phase 2**：`loop` 安全对齐（risk/confirm/worktree/evidence）+ **暴露受控 `terminal`/bash 工具**（过 `decide_terminal` 风险闸门）+ `verify` 工具 + 只读并发 + **会话持久化**（落盘 `loop` 完整消息历史，`--continue`/`--resume` 还原整段对话）。详见 §8。**（会话持久化已落地，见 Phase 2c。）**
- **Phase 2b ✅ 已完成（2026-06-10）**：受控 `terminal` 工具（命令级 `decide_terminal` 门控：SAFE 自动/CONFIRM 确认/DENY 拦截 + 120s 看门狗）+ `verify` 工具（默认项目测试命令）+ **confirm 回路落地**（CONFIRM 档经 `confirm_callback`）。真实联调：DeepSeek 经 `loop` 调 terminal 跑 `git status` 过闸门并总结。
- **Phase 2c ✅ 已完成（2026-06-11）**：会话持久化升级为**完整消息历史**——`loop` 每次结束把整段对话（含最终 assistant 回答）落盘到 `.xhx/sessions/<run_id>.json`，索引新增 `transcript_path`/`mode`；`--continue`/`--resume` 优先全量还原（新 system + 历史去旧 system + 新 task 经 `prior_messages` 注入），缺 transcript 的老会话回退摘要续接。
- **Phase 3a ✅ 已完成（2026-06-11）**：`plan` 范式迁 tool-calling（批量规划 + 执行 + 验证路由 + 有界自修复回喂）。新 `PlanOrchestrator` 走原生 tool-calling 自主多轮（批量规划、只读 tool_calls 并发、回纯文本即停），改动后经 `infer_verification` 路由验证；失败且 `--auto-repair` 时把"Verification failed"回喂同一 tool-calling 循环让模型修（`decide_repair` 门控、≤2 轮）。本切片**仅重指 `--mode plan`**；默认 `linear`/`dag`/`graph` 与 ModelPlan 路径不变。共享 `execute_tool_call`（`_toolturn.py`）由 `loop`/`plan` 共用，`loop` 行为零变更。`linear`/默认收敛留 3b。
- **Phase 3b-1 ✅ 已完成（2026-06-11）**：给 tool-calling `plan` 补齐证据 parity（apply_patch 证据 + patch-evidence-binding + checkpoint/restore），纯增量、默认路由不变；默认切换 + 测试迁移留 3b-2。
- **Phase 3b-2 ✅ 已完成（2026-06-12）**：**默认路由切到 tool-calling `loop`** —— 省略 `--mode` 时不再走 legacy ModelPlan 的自动分类(linear/dag)，而是统一的 tool-calling loop（`select_orchestrator(mode)` → `DEFAULT_MODE="loop"`）。`ModelPlan`/`linear`/`dag`/`planner`/dry-run **全部保留**（仅不再默认、仍可经 `--mode linear/dag` 与预览路径触达），不做大规模删除（纯删高风险零增益）。受影响的 18 个 legacy 行为测试钉到显式 `mode="linear"/"dag"` 继续覆盖保留路径；新增 `test_runtime_app_default_mode_is_loop`。真模型验证：`xhx run`（无 `--mode`）→ `mode="loop"` 成功。至此默认路径与三范式「同一套 tool-calling 协议」彻底自洽。
- **Phase 3**：`plan` 范式迁到 tool-calling（批量计划-执行 + 吸收 `linear` 停止策略）。**（plan 部分已落地，见 Phase 3a；剩 `linear`/默认收敛 → 3b。）**
- **Phase 4**：`graph` 范式迁到 tool-calling（吸收 `dag` 为并发执行层）。
- **Phase 5a ✅ 已完成（2026-06-11）**：只读 `explore` 子 agent —— `dispatch` 工具 + `agent_type` 注册表 + 隔离只读子循环（search/read_file，自己的消息历史/限定轮数），跑完只回浓缩结论，父上下文只长一句；真模型验证。
- **Phase 5b ✅ 已完成（2026-06-12）**：写型子 agent + 串行合并 + 冲突上报 —— `agent_type="edit"`（search/read_file/apply_patch）跑在自己的 **git worktree**（`tool_context.workspace` 重定向）里改代码，改完 `_merge_into_parent` **串行合并回父工作区**并对同文件做**先到先得冲突检测**（`ctx.subagent_claims`），非 git 仓库降级就地。自测 + 端到端验证：worktree 隔离（改动只进 worktree、父经合并才更新）、冲突先到先得上报。注：写型 dispatch 在 loop 里串行执行；真·并发执行为可选增强——安全保证来自隔离 + 串行冲突合并，不依赖并发。
- **Phase 5**：子 agent / 并行探索（`dispatch` 工具 + `agent_type` 注册表 + 隔离子循环；只读 explore + 写型 worktree；并行执行 + 串行合并 + 冲突上报）。详见 §6。
- **Phase 6a ✅ 已完成（2026-06-12）**：长期记忆 MVP —— `memory/store.py`（`.xhx/memory/` frontmatter 事实 + `MEMORY.md` 索引，4 类型，容错解析）+ `memory/recall.py`（**确定性** token 重叠召回，desc/name 权重>正文、中文按字，生命周期校验跳过点名失踪文件的陈旧记忆，渲染块空时返回 `""`→零回归）；注入 `compile_context_pack`（`memory:<type>` 来源，priority 88）+ 三编排器（loop/plan/graph）system prompt；REPL `/remember`·`/memory` + `xhx memory` CLI。**真 DeepSeek 端到端验证**：只存在于记忆的事实经召回注入 system prompt 后被真模型答出。suggest-confirm 自动抽取留 6b。
- **Phase 6b ✅ 已完成（2026-06-12）**：记忆自动抽取（suggest-confirm）—— `memory/extract.py`（跑后成功时 LLM 提议耐久事实，`EXTRACTION_SYSTEM_PROMPT` + **严格格式**解析 `MEMORY | type|name|desc|body`，`NONE`/无关文本→`[]`、slug 去重、上限 3、**绝不自动写盘**）；console `_maybe_suggest_memories` 跑后钩子（成功门控 + 空时静默 + best-effort）+ `_confirm_memory` 逐条确认 + `/automem on|off` 开关。**真 DeepSeek 验证**：耐久反馈→1 候选、琐碎任务→`NONE`。**至此 Phase 6（显式 `/remember` + 自动 suggest-confirm + 确定性召回注入）全貌闭合**。
- **Phase 6 ✅ 已完成（6a+6b，2026-06-12）**：长期记忆 / 跨会话上下文（`.xhx/memory/` 4 类型事实 + 建议-确认写入 + 确定性召回入 context-pack / 编排器 system prompt）。详见 §7。
- **Phase 7a ✅ 已完成（2026-06-12）**：流式渲染 + 细状态行 —— tool-calling 的 `chat()` 接成流式（`stream=True`+挂 `delta_callback` 时 `_chat_stream` 实时喂 content 增量 + 按 index 拼装分片 `tool_calls`，零行为变更），loop 把增量 emit 成 `model_delta`；渲染重做（顶部单行细状态行 state·mode·turn·tokens·streaming、流式区 `▌` 光标、`model_delta` 防抖不强刷整屏）；`scripts/render_dashboard.py` 导 SVG 可视核对。**真 DeepSeek 验证**：content 增量精确重组、流式 tool_call 正确组装。验收时撤掉 Gemini 又一处 `inspect.signature` 反射防御（改 fake 签名对齐真实）。历史压缩 microcompact + repo-intel-as-tool 留 7b。
- **Phase 7b ✅ 已完成（2026-06-12）**：消息历史压缩 + repo-intel 工具 —— `orchestrators/compaction.py`（`compact_messages` 超 token 阈值才把中间旧历史压成一条摘要、否则 no-op；**保 OpenAI 消息有效性**：保留尾部必从非 tool 消息起、杜绝孤儿 tool；loop 每轮 chat 前接入、`getattr` 守卫无 summarize 的 client）；只读 `repo_query` 工具（`kind=symbol|reference`，查 repo-intel 索引，过只读门控）。**真验证**：DeepSeek 压缩 14→6 保真且无孤儿；repo_query 在真仓库查到 `compact_messages`/`build_routed_client` 等符号与引用。
- **Phase 7c ✅ 已完成（2026-06-12）**：Textual 全屏富视图打磨 —— 把 7a 给 REPL 加的**细状态行 + 流式光标**带给全屏 `xhx tui`：`TextualSnapshot.status_line`（state·mode·turn·tokens·streaming）+ 对话区 `model (streaming…)>…▌` + App 新增 `#statusline` widget（CSS accent 粗体）。纯增量保留所有 widget id，35 TUI 测试零回归；Textual `export_screenshot()` 视觉验证。
- **Phase 7 ✅ 已完成（7a+7b+7c）**：流式渲染 + 细状态行 + microcompact + repo-intel 工具 + 全屏富视图。详见 §10。
- **Phase 8a ✅ 已完成（2026-06-12）**：三范式 benchmark 矩阵 —— `run_matrix` + `render_benchmark_report`（按范式聚合 + 逐任务明细，markdown + JSON），CLI `benchmark --modes loop,plan,graph`，落 `.xhx/benchmark/report.md|json`。
- **Phase 8b ✅ 已完成（2026-06-12）**：token 计量 —— `chat_and_count`/`_estimate_message_tokens` 包住每次模型调用，把 tiktoken 估算累加进 `metrics_tracker`，`loop`/`plan`/`graph`/`subagent` 全接入，`loop`/`plan` 设 `RunMetrics`；benchmark token 列可区分范式（真模型下 `graph` ~4× `loop`/`plan`）。
- **Phase 8**：三范式 benchmark —— 量化对比台架（任务集 × 范式矩阵，测成功率/token/轮数/时间，出对比报告）。详见 §9。
- **Phase 9 ✅ 已完成（2026-06-12）**：多模型路由 + fallback —— `config.routing`（`roles: role→profile` + `fallback` 降级链，默认空→零行为变更）+ `models/routing.py`（`resolve_profile_for_role` + `FallbackChatClient` 按序重试 `ModelClientError`、主成功则后续不调用 + `build_routed_client`）；loop/plan/graph/subagent 改走 `build_routed_client`（**explore 子 agent 可路由到便宜 profile**），token 仍只计一次。**真 DeepSeek 验证**：坏主 profile（key 未设）→ 自动降级到真模型返回答案。验收时撤掉 Gemini 为过 MagicMock 测试加的防御性 isinstance 护栏（改为测试传真实 workspace）。
- **Phase 9**：多模型路由 —— 按角色/子 agent 选模型（便宜探索、强模型改代码）+ fallback 降级。详见 §9。
- **Phase 10 ✅ 已完成（2026-06-12）**：README 改写为三范式（`loop`/`plan`/`graph`，原生 tool-calling）对比叙事 + 真实 DeepSeek benchmark 表（`graph` ~4× token / ~3× 墙钟、2/3 成功——"多 agent ≠ 更好"）+ 工程手记（`apply_patch` 真模型修复 / 提示词非银弹 / token 计量）；中英双语；顺修 CLI `--mode` 帮助文本滞后。
- **Phase 10**：README 三范式对比叙事（用 Phase 8 数据）+ 经验文档收尾 + 测试覆盖打磨。

> 落地顺序可微调；总思路：先把 tool-calling 基础 + `loop`（Phase 1–2）跑通，再迁 `plan`/`graph`（Phase 3–4，顺带把 `linear`/`dag` 收敛为支撑机制），子 agent / 记忆等增量功能其后。

---

## 5. 达成的效果（最终可演示）

1. `xhx chat` 问"这个项目架构怎么设计的？" → `loop` 读代码后给**真实回答**。
2. "给某函数加类型注解" → `loop` `read → apply_patch`，`/diff` 看真实改动（worktree 隔离）。
3. 同一任务分别 `--mode plan` / `graph` / `loop`，**对比三种控制流**（协议都用 tool-calling，对比纯净）。
4. `xhx replay <run-id>` 回放证据链；`--dry-run` 看 token 预算。

**面试一句话**：「本地编码 agent，统一用原生 tool-calling，三种控制流范式（Plan-Execute / 多 agent / ReAct）可直接对比；同时具备预算化上下文工程、安全隔离与可回放证据链等工程深度——并照 Claude Code 源码复刻了核心循环。」

---

## 6. 子 agent / 并行探索（已设计 · 对应 Phase 5）

**价值**：一套设计同时拿到三个价值——上下文隔离 / 并行加速 / 专精分工。其中**上下文隔离**接上 xhx 预算化主线，形成"**四轴上下文管理**"叙事：①每轮 context pack 预算化（纵向）②`loop` 内历史压缩（纵向）③跨 agent 委派子 agent（横向）④长期记忆（时间轴，见 §7）。

**机制**：`loop` 调 `dispatch(description, prompt, agent_type)` 工具 → 新开**隔离子循环**（自己的消息历史、自己的 context pack、受限工具、限轮数），跑完**只回浓缩结论**；父上下文只长一句。

**三价值如何落地**
- 上下文隔离：子 agent 的中间噪音留在自己历史，父只收浓缩结论（**Don't peek**）。
- 并行加速：一个 turn 里多个 `dispatch` 并发执行（复用现有只读并发线程池思路）。
- 专精分工：`agent_type` 注册表（explore / review / verify …）+ 每型工具白名单。

**读写范围（首版即含写）**
- 只读 `explore`：自由并行。
- 写型子 agent：各自跑在独立 git worktree；**并行执行 + 串行合并 + 冲突上报**——父给子 agent 划不重叠范围，撞了就把"合并冲突"作为工具错误回报父 `loop`，让模型重新规划。复用 `SafeExecutionKernel` 的 worktree / checkpoint / restore。

**护栏**：depth ≤ 1（子 agent 不能再派子 agent，防 worktree 嵌套爆炸）。

**全程复用基座**：tool-calling 客户端、kernel（工具白名单门控）、context-pack 编译器（子 agent 为窄任务编自己的包）、evidence（子 trace 嵌套在父 run 下）。

---

## 7. 长期记忆 / 跨会话上下文（已设计 · 对应 Phase 6）

**价值**：上下文管理的**第 ④ 轴（时间轴：跨会话）**，补全四轴叙事。核心仍是 xhx 主线：「索引 + 选择性召回 = 预算化记忆」。

**存储**：`.xhx/memory/` —— `MEMORY.md` 常驻索引 + 每条事实一个 frontmatter 文件（`name` / `description` / `type` + 正文）。与 `XHX.md`（静态项目地图）、`sessions/`（单 run 续接）**三者分明、各管一摊**。

**记什么（4 类型）**：`user`（用户是谁/偏好）、`feedback`（工作指导，带 **Why** / **How to apply**）、`project`（进行中工作与决策，相对日期转绝对）、`reference`（外部资源指针）。
**硬性不存**：可从代码 / git / `XHX.md` / repo-intel 推导的东西、修 bug 配方、临时状态。

**写入**：显式（`/remember` 或"记住 X"）必存 + 自动抽取做**"建议-确认"**（agent 提议存什么，用户一键确认/否决，复用现有 confirm 交互——防污染又保留学习能力）。

**召回**：作为 **context-pack 的一个新来源**注入（接入现有预算/裁剪）。机制取**确定性检索**（关键词 / FTS 在记忆 `description` 上匹配，复用 repo-intel 的 SQLite，无额外 LLM 调用、确定性——最贴 xhx 气质；以后可升级为"检索粗筛 + 模型精选"混合）。

**生命周期（防腐烂）**：召回前先核对当前代码/文件再用（点名的文件/函数先验证还在不在）；MVP 提供 `/memory` 查看与手动清理；自动合并去重留后期。

---

## 8. 工具生态：Kernel 作为万能闸门（部分已设计）

**原则**：不管是内置工具、bash 命令、还是外部 MCP 工具，**每次调用都过同一个风险分级闸门**（Safe Execution Kernel）。骨架已就位——`decide_tool` 覆盖内置 / `mcp_` / `custom_`，`decide_terminal` 对命令做 shlex 分级（危险可执行文件黑名单 + shell 元字符/内联解释器一律 deny + 120s 看门狗）。**新工具 = 声明 schema + 风险档 + executor，自动接入闸门**。

**现状**
- 已暴露给模型：`search` / `read_file` / `apply_patch`。
- `terminal`：已存在但**仅用于验证**，未暴露给模型。
- **MCP：已有可用 stdio 客户端**（连接/握手/`tools/list`/`tools/call`/动态注册 `mcp_` 前缀 + mock + CONFIRM 门控）——非从零。

**本阶段已选**（并入 Phase 1/2）：声明式工具接口 + **暴露受控 `terminal`/bash**（最高杠杆，最能展示安全内核）。

**候选工具（未来 · 按 价值÷成本）**
- 只读 git（status/diff/log）：独立工具保证只读、输出结构化，与裸 bash 分开。
- MCP 接入新 `loop` + `.xhx/mcp.json` 多服务器配置化（客户端已有，主要是接线/配置）。
- web 检索（WebFetch/WebSearch）：有用但引入外部依赖/key + SSRF 风险，优先级最低。

---

## 9. 量化对比 benchmark + 多模型路由（已设计）

### 9.1 三范式 benchmark（对应 Phase 8）
**价值**：把"一套基座、多范式、可直接对比"从口号变成**数字表**——核心论点的实证，也是 README/面试的王牌素材。
**做什么**：同一批任务分别用 `plan`/`graph`/`loop` 跑，测 成功率（验证通过）/ token / 轮数·LLM 调用 / 墙钟 / 改动文件 / 修复轮数 → 出对比报告（markdown + JSON）。
**复用**：已有 `benchmark` 命令 + `evals/metrics.py`（`RunMetrics`）+ 运行时 `metrics_tracker`；任务集用 `tests/fixtures` 夹具当种子。mock 跑确定性进 CI，真实 LLM 跑真数字。

### 9.2 多模型路由（对应 Phase 9）
**价值**：成本优化（便宜模型做探索/历史压缩，强模型做规划/改代码）+ fallback 健壮性；与子 agent 天然组合（explore 子 agent 走便宜模型）。
**做什么**：让一次 run 按角色/步骤/子 agent 选不同 profile；强模型失败/限流时 **fallback 降级**（对标 Claude Code 的 `fallbackModel`）。
**复用**：已有 profiles 系统（`.xhx/profiles.json`）；`summarize()` 压缩历史这条路最适合先换便宜模型。

---

## 10. TUI / 交互重做（部分已修 · 绑定 loop/流式）

**问题**：现有 REPL 把"全屏仪表盘"当主界面——一次交互整页重绘多遍、噪音大；`Table.grid` 固定列导致长 workspace 路径与 `cancel` 糊连。和即将到来的流式 `loop` 也不搭。

**参照（两套主流殊途同归）**
- **Claude Code**（Ink/React）：Static 滚屏 + 流式小区 + flex 布局 + 细状态行。
- **Pi**（`pi-tui` 自研，`D:\pycharmprojects\pi`）：组件树 `render(width)→lines` + `previousLines` **逐行 diff** + `requestRender` **防抖** + agent 核心与 TUI **解耦**。

**5 条普适原则**：①diff 重绘不清屏 ②合并/防抖渲染 ③按宽度的组件布局 ④渲染器与 loop 解耦 ⑤流式内容独立区 + 细状态行。

**xhx 关键判断**：架构已对——loop 全程 `emit_event`/`event_callback`，rich REPL / Textual TUI / JSON-RPC 都是同一事件流的消费者（天然解耦）；且**已有 Textual app**（Textual 原生具备 diff/防抖/响应式）。**问题只在 rich REPL 的渲染策略**。

**落地**
- `xhx chat`（REPL）：改成**追加式滚屏 + 一个细 `Live` 只渲染流式行 + 状态**，表头宽度自适应。基础版随 loop MVP（Phase 1/2）落地，流式打磨在 Phase 7。
- `xhx tui`（Textual）：作为富视图主场打磨（Phase 7）。
- ✅ **已修**：表头 workspace/cancel 溢出糊连（`tui/page.py`，改为全宽 fold 行，commit `77424c7`）。

---

## 11. 待讨论功能（停车场 · Parking Lot）

> 下面是后续要继续讨论、尚未定型的候选功能。讨论清楚后会移入上方正式规划。

- _（暂空——继续讨论中……）_
