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
| `plan` | Plan-and-Execute | 前置规划一次→批量执行→必要时 replan；模型一轮产出**一组** `tool_calls` | 步骤清晰、少 LLM 调用的批量任务 | 🚧 由现有 `loop`/`linear` 迁移 |
| `graph` | 多 agent 工作流（LangGraph） | coordinator → execute → review，条件重执行（≤2 轮），各节点内用 tool-calling | 规划/执行/复核分离 | 🚧 由现有 `graph`/`dag` 迁移 |
| `loop` | ReAct tool-use loop（Claude Code 式） | 每步问模型，看到每个工具结果再决定下一步；回文本=对话，回 `tool_calls`=执行再循环 | 对话 + 真实改代码，最好用 | 🚧 新建（Phase 1 主攻） |

**命名修订（旧→新）**：原 `loop`（自主 plan-execute）→ **`plan`**；新建的 tool-use 循环 → **`loop`**（它才真在"循环"）；`graph` 不变。
**收敛**：原 `linear`（首改即停）→ `plan` 的停止策略开关；原 `dag`（Kahn 并发调度）→ `graph` 的执行层。

---

## 3. 能力清单（保留 / 改造 / 新增）

**保留（现有独特资产）**
- ✅ Context Pack 编译器：tiktoken 预算、优先级裁剪、历史压缩
- ✅ Safe Execution Kernel：risk 三档（safe/confirm/deny）、worktree 隔离、checkpoint/restore
- ✅ Repo Intelligence：ast + tree-sitter，JSON + SQLite，增量刷新
- ✅ 验证路由 + 有界自修复（≤2 轮）
- ✅ Evidence 证据链 + 确定性回放（replay）
- ✅ 会话恢复：`--continue` / `--resume` / `sessions`
- ✅ 入口：CLI `run` / REPL `chat` / TUI / JSON-RPC
- ✅ 离线 `mock` profile、benchmark、Skills/Hooks/MCP
- ✅ LLM 接入：OpenAI 兼容（**DeepSeek 已真实连通**）

**改造（迁到 tool-calling）**
- 🚧 模型协议：手写 plan-JSON `{summary,status,steps}` 全退役 → **原生 `tool_calls` + 消息历史**
- 🚧 `mock` provider：改成**模拟 `tool_calls`**，保证离线/CI 仍可跑
- 🚧 现有 `loop`/`linear` → `plan` 范式；现有 `graph`/`dag` → `graph` 范式
- ⚠️ 取舍：依赖模型支持 function calling（DeepSeek 支持；放弃对不支持 tool-calling 模型的兼容）

**新增**
- 🚧 `loop` 范式：ReAct tool-use 统一循环（对话 + 干活）
- 🚧 《读 Claude Code 源码学到的》经验文档（学习产物 + 实现图纸）

---

## 4. 分阶段（每步可跑、可 demo、可讲）

- **Phase 0**：《Claude Code 源码经验》文档（零代码风险，作后续图纸）。
- **Phase 1**：**tool-calling 基础设施 + `loop`(ReAct) MVP** —— 客户端 + 工具 JSON schema + 消息历史 + mock 模拟；支持**对话 + `read_file`/`search`/`apply_patch`**（读+写）。详见 [设计文档](docs/superpowers/specs/2026-06-10-agent-tool-calling-conversation-design.md)。
- **Phase 2**：`loop` 安全对齐（risk/confirm/worktree/evidence）+ `terminal`/`verify` 工具 + 只读并发。
- **Phase 3**：`plan` 范式迁到 tool-calling（批量计划-执行 + 吸收 `linear` 停止策略）。
- **Phase 4**：`graph` 范式迁到 tool-calling（吸收 `dag` 为并发执行层）。
- **Phase 5**：子 agent / 并行探索（`dispatch` 工具 + `agent_type` 注册表 + 隔离子循环；只读 explore + 写型 worktree；并行执行 + 串行合并 + 冲突上报）。详见 §6。
- **Phase 6**：长期记忆 / 跨会话上下文（`.xhx/memory/` 4 类型事实 + 建议-确认写入 + 确定性召回入 context-pack）。详见 §7。
- **Phase 7**：流式渲染 + 消息历史压缩（对标 microcompact）+ repo-intel 作为工具。
- **Phase 8**：README 三范式对比叙事 + 经验文档收尾 + 测试覆盖打磨。

> 落地顺序的细节（如先收敛旧范式还是先建 loop）将在动手前进一步确定；总思路是先把 tool-calling 基础 + `loop` 跑通，再迁 `plan`、`graph`。

---

## 5. 达成的效果（最终可演示）

1. `xhx chat` 问"这个项目架构怎么设计的？" → `loop` 读代码后给**真实回答**。
2. "给某函数加类型注解" → `loop` `read → apply_patch`，`/diff` 看真实改动（worktree 隔离）。
3. 同一任务分别 `--mode plan` / `graph` / `loop`，**对比三种控制流**（协议都用 tool-calling，对比纯净）。
4. `xhx replay <run-id>` 回放证据链；`--dry-run` 看 token 预算。

**面试一句话**：「本地编码 agent，统一用原生 tool-calling，三种控制流范式（Plan-Execute / 多 agent / ReAct）可直接对比；同时具备预算化上下文工程、安全隔离与可回放证据链等工程深度——并照 Claude Code 源码复刻了核心循环。」

---

## 6. 子 agent / 并行探索（已设计 · 对应 Phase 5）

**价值**：一套设计同时拿到三个价值——上下文隔离 / 并行加速 / 专精分工。其中**上下文隔离**接上 xhx 预算化主线，形成"**多轴上下文管理**"叙事：①每轮 context pack 预算化（纵向）②`loop` 内历史压缩（纵向）③跨 agent 委派子 agent（横向）④长期记忆（时间轴，见 §7）。

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

## 8. 待讨论功能（停车场 · Parking Lot）

> 下面是后续要继续讨论、尚未定型的候选功能。讨论清楚后会移入上方正式规划。

- 更多工具（git 操作、web 检索、扩展 MCP）
- 三范式可量化对比 benchmark（成功率 / token / 轮数）
- 多模型路由（探索用便宜模型、改代码用强模型）
- _（继续讨论中……）_
