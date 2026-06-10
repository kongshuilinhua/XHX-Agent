# 读 Claude Code 源码学到的：可迁移的 agent 架构经验

> **目的**：本文是 xhx-agent 改造的"图纸"——从 Claude Code 还原源码（以及 pi `pi-tui`）里提炼出**可迁移的架构经验**，并标注每条经验对应 xhx 的哪个 Phase。既服务"学习"目标，也作为后续实现的参照。
>
> **来源与免责**：研读对象是 `@anthropic-ai/claude-code` 从 npm source map **还原**的 TypeScript 源码（非官方、仅供学习；版权归 Anthropic）与 `pi-mono`（`@mariozechner/pi-*`，TS）。下文 `file:line` 均指还原源码树，可能随版本漂移——引用前先核对。
> 最近更新：2026-06-10

---

## 0. 一句话总纲

> **一个统一的 tool-calling agent 循环 + 一套声明式工具 + 多层上下文管理**，就是现代编码 agent（Claude Code 式）的骨架。其余（子 agent、记忆、TUI、健壮性）都挂在这副骨架上。

---

## 1. 统一 agent 循环（最核心）

**源码**：`src/query.ts` 的 `query()` / `queryLoop()`（async generator，`while(true)` 大循环）。

**机制**：每一轮调一次模型，流式 yield 出 assistant 消息：
- assistant 消息里**有 `tool_use` 块** → `needsFollowUp = true`（`query.ts:826-835`）：执行工具 → 把 `tool_result` 追加进 messages → `state = {...}; continue` 再进一轮。
- **只回纯文本**（无 tool_use）→ `query.ts:1062` 的 `if (!needsFollowUp)` → 跑完 stop-hook 检查就 `return { reason: 'completed' }` 结束本轮——**那段文本即是给用户的回答**。

**要点**：
1. **没有"聊天 vs 任务"分类器**。"对话"只是"模型这一轮选择回文本而非调工具"的自然结果。
2. **消息历史（messages）是唯一事实源**：user / assistant(含 tool_calls) / tool(tool_call_id)。循环只是不断把增长的消息列表重发给模型。
3. **stop hooks** 能在模型想停时注入阻断、强制再循环（`query.ts:1267` 附近）——这是"任务没真正完成就别停"的钩子。

**对 xhx 的映射**：✅ **已落地（Phase 1 `loop` 范式）**。xhx 的 `LoopOrchestrator.run` 即此循环；与旧"每轮重编 context pack + 计划 JSON"的本质区别是**消息历史追加式增长 + 原生 tool_calls**。

---

## 2. 声明式工具系统

**源码**：`src/Tool.ts` 的 `Tool<Input,Output>` 类型（`Tool.ts:362` 起）。每个工具声明：
- `call(args, ctx, canUseTool, parentMessage, onProgress) -> ToolResult`（`:379`）——执行体。
- `description(input, options)`（`:386`）、`inputSchema` / `inputJSONSchema`（`:394/:397`）——喂给模型的 schema。
- `isConcurrencySafe(input)`（`:402`）、`isReadOnly(input)`（`:404`）、`isDestructive?(input)`（`:406`）——驱动并发与权限。
- `checkPermissions(...)`（`:500`）、`userFacingName(input)`（`:524`）。
- `buildTool()` 用 `TOOL_DEFAULTS` 补默认（`:750-790`）：默认 `isReadOnly=false`、`isConcurrencySafe=false`、`checkPermissions=allow`。

**要点**：工具 = **一处声明 schema + 元数据 + 执行体**。模型靠 `inputSchema` 知道怎么调；运行时靠 `isReadOnly/isConcurrencySafe` 决定能否并发，靠 `checkPermissions` 决定是否放行。

**对 xhx 的映射**：
- ✅ 部分（Phase 1）：已加 `ToolDefinition`（name/description/parameters/read_only/destructive）+ `tool_schemas()` 导出。
- 🚧 待补（Phase 2 遗留项）：**schema 派生参数校验**（替换硬编码 `_validate_arguments`）、`ToolDefinition` 纳入 `runner` 做单一来源、`read_only/destructive` 接 `decide_tool` 风险门控、`isConcurrencySafe` 思路用于只读并发。

---

## 3. 子 agent（Task / dispatch 工具）

**源码**：`src/Task.ts`（任务类型/状态/ID）、`src/tools/AgentTool/`（`prompt.ts`、`runAgent.ts`、`built-in/exploreAgent.ts` 等）。

**机制**：主循环可调 `Agent`/Task 工具，传 `{description, subagent_type, prompt, run_in_background?, isolation?}`。子 agent 自主跑完，**只返回一条结果消息**给父 agent。`prompt.ts` 关键约定：
- 「the result returned by the agent is **not visible to the user**」「it will return a **single message** back to you」——**上下文隔离**：子 agent 的中间工具输出**不进父上下文**。
- 「**Don't peek**」——别去读子 agent 的中间 transcript，否则白隔离。
- 「Launch multiple agents **concurrently**… single message with multiple tool uses」——并行；前台（要结果才能继续）vs 后台（独立并行）。
- 多种 agent 类型（`exploreAgent` 只读搜索、`generalPurposeAgent`、`planAgent`、`verificationAgent`），各带工具白/黑名单。
- 写型可 `isolation: "worktree"`，无改动自动清理、有改动返回分支路径。

**核心价值**：子 agent 本质是**一种上下文预算管理策略**——用一次工具调用的上下文代价，换一次任意大探索的浓缩结论。

**对 xhx 的映射**：🚧 **Phase 5**（已设计，ROADMAP §6）：`dispatch` 工具 + `agent_type` 注册表 + 隔离子循环；只读 explore + 写型 worktree（并行执行 + 串行合并 + 冲突上报）；depth ≤ 1。

---

## 4. 长期记忆（memdir）

**源码**：`src/memdir/`（`memoryTypes.ts`、`memoryScan.ts`、`findRelevantMemories.ts`）、`src/services/extractMemories/`。与 `CLAUDE.md`（静态手写规则，`utils/claudemd.ts`）**并存但分工不同**。

**机制**：
- **4 种类型**（`memoryTypes.ts: MEMORY_TYPES`）：`user`（用户是谁/偏好）、`feedback`（工作指导，正文带 **Why** / **How to apply**）、`project`（进行中工作与决策，相对日期转绝对）、`reference`（外部资源指针）。
- **每条 = 一个 frontmatter 文件**（`name` / `description` / `type` + 正文）；`MEMORY.md` 是**常驻索引**。
- **按相关度选择性召回**（`findRelevantMemories.ts`）：扫所有记忆的 name+description，用一个小模型调用（Sonnet sideQuery）**挑出 ≤5 条**当前查询用得上的，只加载相关的 → **预算化召回**。
- **自动抽取**（`extractMemories/`）+ 显式保存并存。
- **硬性不存**（`WHAT_NOT_TO_SAVE`）：能从代码/git/CLAUDE.md 推导的东西、修 bug 配方、临时状态。
- **防陈旧**（`MEMORY_DRIFT_CAVEAT`）：行动前先核对当前代码；记忆点名的文件/函数要先验证还在不在。

**对 xhx 的映射**：🚧 **Phase 6**（已设计，ROADMAP §7）：`.xhx/memory/` 同款 4 类型 + frontmatter；写入用"建议-确认"；召回取**确定性检索**（FTS over description，复用 SQLite，无额外 LLM 调用——这是 xhx 与 Claude Code 的差异化）。`XHX.md`（静态）/`.xhx/memory/`（累积）/`sessions/`（单 run 续接）三者分明。

---

## 5. TUI / 交互渲染

**两套主流殊途同归**：

**Claude Code（Ink/React）** — `src/screens/REPL.tsx`：
- `streamingToolUses` / `streamingThinking` 独立状态：正在流式的内容在小动态区实时渲染，**完成后"提交"进静态滚屏**。
- 已完成回合进 **append-only 滚屏**（Ink `<Static>`），**只渲染一次、永不重绘**。
- flex `<Box>` 布局（`flexGrow`/`width="100%"`）自适应宽度；底部一条 `flexGrow` 撑开的细状态行。

**Pi（`pi-tui` 自研）** — `packages/tui/src/tui.ts`：
- 组件树 `Component.render(width) -> string[]`；`Container` 组合。
- **差分渲染**：`previousLines` 缓存上次行，新帧逐行 diff，**只重绘变化的行**，绝不清屏整重绘。
- `requestRender()` **防抖调度**，多次状态变化合并成一次渲染。
- **agent 核心与 TUI 解耦**：`pi-agent-core` 不认识 TUI。

**5 条普适原则**：①diff 重绘不清屏 ②合并/防抖渲染 ③按宽度的组件布局（不手算列） ④渲染器与 loop 解耦 ⑤流式内容独立区 + 细状态行。

**对 xhx 的映射**：🚧 **Phase 7**（已设计，ROADMAP §10）。xhx 架构已对（`event_callback` 事件流已解耦 + 已有 Textual app）——问题只在 rich REPL 的"整页重绘"策略：改成追加式滚屏 + 细 `Live`；富视图交给 Textual。（表头列溢出 bug 已先修，commit `77424c7`。）

---

## 6. 大规模下的上下文 / 健壮性

**源码**：`query.ts` 的恢复逻辑 + `services/compact/`。

- **压缩（compaction）**：prompt-too-long（413）时分级恢复——先 context-collapse 抽干暂存（`query.ts:1089` 附近），再 reactive compact 全量摘要（`:1119` 附近）。日常还有 microcompact 按 `tool_use_id` 删旧工具输出。
- **max-output-token 恢复**：截断时升档重试或注入"继续"meta 消息（`:1188-1252`）。
- **fallback model**：主模型失败/限流时切备用模型重试（`fallbackModel`，`query.ts:894` 附近）。
- **token 预算**：到阈值注入 nudge 续作（`:1308` 附近）。
- **stop hooks 死循环防护**：API 错误时跳过 stop hooks，避免"错误→hook 阻断→重试→错误"螺旋（`:1258-1264`）。

**对 xhx 的映射**：
- 🚧 历史压缩 → **Phase 7**（对标 microcompact / reactive compact）。
- 🚧 fallback model → **Phase 9**（多模型路由的健壮性部分）。

---

## 7. 经验 → xhx Phase 映射总表

| 经验 | Claude Code 源码 | xhx Phase | 状态 |
|:--|:--|:--|:--|
| 统一 tool-calling 循环 | `query.ts` | Phase 1 | ✅ 已实现 |
| 声明式工具系统 | `Tool.ts` | Phase 1（基础）/ Phase 2（校验·门控·并发） | ◑ 部分 |
| 子 agent / 并行 + 上下文隔离 | `Task.ts`、`AgentTool/` | Phase 5 | 🚧 已设计 |
| 长期记忆（4 类型 + 选择性召回） | `memdir/` | Phase 6 | 🚧 已设计 |
| 流式 TUI（静态滚屏 + 差分） | `REPL.tsx`、`pi-tui/tui.ts` | Phase 7 | 🚧 已设计 |
| 压缩 / fallback / 预算 健壮性 | `query.ts`、`services/compact/` | Phase 7 / 9 | 🚧 已设计 |

---

## 8. 给 xhx 的几条"反过来"的判断（不照抄的地方）

- **召回机制**：Claude Code 用**模型选择器**挑相关记忆；xhx 取**确定性检索**（复用 SQLite/FTS，无额外 LLM 调用）——更贴 xhx 的"预算化/确定性"气质，也是差异化卖点。
- **保留多范式**：Claude Code 只有一套统一循环；xhx 刻意保留 `plan`/`graph`/`loop` 三范式做**可对比的实验**（统一 tool-calling 协议下只变控制流）——这是 xhx 的研究价值所在。
- **TUI 不必换框架**：Ink/pi-tui 是 JS 自研；xhx 用 Python 的 **Textual**（原生具备差分/防抖/响应式）即可达成同样原则，不必手搓差分渲染器。
