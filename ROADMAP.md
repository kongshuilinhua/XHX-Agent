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

## 2. 最终架构：一套基座 + 3 个「真范式」+ 支撑机制

```
        ┌──────────── 3 真范式（顶层控制流，可插拔）────────────┐
        │  loop             graph              agent             │
        │  Plan-Execute     多 agent 工作流     Tool-use loop      │
        │  自主规划-执行     coordinator→        原生 tool-calling  │
        │  （计划 JSON）     execute→review      + 对话（ReAct 式） │
        └────────────────────────────────────────────────────────┘
                              ↓ 共用 ↓
   ┌──────────────────── 一套共享基座 ────────────────────┐
   │ Context Pack · Repo Intel · Safe Kernel · Verify · Evidence │
   └─────────────────────────────────────────────────────────────┘
   支撑机制（不是范式）：停止策略（原 linear）· DAG 并发调度（原 dag）
```

**3 范式各对应一个公认概念**，互相不重叠：

| 范式 | 概念 | 控制流 | 适合 | 状态 |
|:--|:--|:--|:--|:--|
| `loop` | Plan-Execute | 模型出计划 JSON → 执行 → 自主多轮迭代到 done/上限 | 开放式编辑、探索 | ✅ 已实现 |
| `graph` | 多 agent 工作流（LangGraph） | coordinator → execute → review，条件重执行（≤2 轮） | 规划/执行/复核分离 | ✅ 已实现 |
| `agent` | Tool-use loop（Claude Code 式） | 消息历史 + 原生 tool-calling；回文本=对话，回 tool_calls=执行再循环 | 对话 + 真实改代码，最好用 | 🚧 设计完成、待实现 |

**收敛说明**：原 `linear`（首改即停）= `loop` 的停止策略开关；原 `dag`（Kahn 并发调度）= `graph` 的执行层。两者从平级"范式"降级为支撑机制，消除 loop/linear、dag/graph 的重叠。

---

## 3. 能力清单（保留 / 新增）

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

**新增（本次方向）**
- 🚧 `agent` 范式：对话 + 原生 tool-calling 统一循环
- 🚧 范式收敛：`linear`→停止策略、`dag`→graph 执行层
- 🚧 《读 Claude Code 源码学到的》经验文档（学习产物 + 实现图纸）

---

## 4. 工作流与分阶段

**工作流 ①｜范式收敛重构（主要做减法，低风险）**
- `linear` → `loop` 的 `stop-on-first-change` 停止策略开关
- `dag` → `graph` 的并发执行层（不再平级）
- 更新 registry / CLI mode 校验 / 文档 / 测试

**工作流 ②｜新增 `agent` 范式**（详见 [Phase 1 设计文档](docs/superpowers/specs/2026-06-10-agent-tool-calling-conversation-design.md)）
- **Phase 0**：《Claude Code 源码经验》文档
- **Phase 1**：agent MVP —— 对话 + `read_file`/`search`/`apply_patch`（读+写）
- **Phase 2**：编辑安全对齐 + 只读并发 + `terminal`/`verify` 工具
- **Phase 3**：流式渲染 + 消息历史压缩（对标 microcompact）+ repo-intel 作为工具
- **Phase 4**：README 三范式对比叙事 + 经验文档收尾 + 测试覆盖打磨

> 落地顺序待定（收敛优先 vs agent 优先），将在继续讨论后确定。

---

## 5. 达成的效果（最终可演示）

1. `xhx chat` 问"这个项目架构怎么设计的？" → agent 读代码后给**真实回答**。
2. "给某函数加类型注解" → agent `read → apply_patch`，`/diff` 看真实改动（worktree 隔离）。
3. 同一任务分别 `--mode loop` / `graph` / `agent`，**对比三种控制流**。
4. `xhx replay <run-id>` 回放证据链；`--dry-run` 看 token 预算。

**面试一句话**：「本地编码 agent，既有预算化上下文工程和多范式编排的工程深度，又照 Claude Code 源码复刻了原生 tool-calling 的统一 agent 循环，三范式可直接对比。」

---

## 6. 待讨论功能（停车场 · Parking Lot）

> 下面是后续要继续讨论、尚未定型的候选功能。讨论清楚后会移入上方正式规划。

- _（待补充——继续讨论中）_
