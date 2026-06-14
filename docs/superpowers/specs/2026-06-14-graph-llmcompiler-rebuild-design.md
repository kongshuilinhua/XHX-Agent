# graph 重铸为 LLMCompiler 式并行 DAG —— 设计 Spec

**状态**：草案，待用户评审（2026-06-14）。这是设计文档，不是实施计划；评审通过后再按切片写 `docs/superpowers/plans/`。

**一句话**：把 `graph` 从"coordinator 硬拆 → 顺序写 worker → LLM 嘴上复审"重铸成 **LLMCompiler 式**：LLM planner 产出**带依赖 + 变量引用**的任务 DAG，调度器拓扑并行执行（节点=隔离子 agent），节点输出经**变量替换**喂给下游，最后 joiner 判定收尾或重规划。

---

## 1. 目标 / 非目标

**目标**
- 给 `graph` 一个不可替代的身份：**显式并行多 agent DAG 编排**（区别于 `loop` 的扁平 fan-out、`plan` 的批量执行+验证）。
- 复用现有零件：`DAGScheduler`/`topological_sort`（拓扑+读写隔离并行）、`subagent.py`（worktree 隔离 + 串行合并 + 冲突检测）、`modes.DAGNode/DAGPlan`、LangGraph 状态机。
- 让真模型能用：planner 输出必须可靠解析（结构化），节点间能传数据（变量替换）。

**非目标**
- 不动 `loop`（ReAct）/`plan`（plan-execute+验证）的定位。
- 不追求 LLMCompiler 论文里的"流式 planner 边出边调度"（v1 用一次性结构化 DAG，足够且更稳）。
- 不在本 spec 写实现代码（留给后续 plan）。

## 2. 背景与依据

- LLMCompiler（arXiv 2312.04511）是"LLM 生成 DAG + 并行执行"的 SOTA：Planner → Task Fetching Unit（调度+变量替换）→ 并行 Executor → Joiner；对可并行任务相比 ReAct 延迟 ↓~3.6x、成本 ↓最多 6x。它本就是 LangGraph 实现，而 `graph` 已用 LangGraph，迁移成本低。详见 [[roadmap-direction]]。
- 现状 `graph`（[graph.py](../../../src/xhx_agent/orchestrators/graph.py)）问题：coordinator 不带工具、靠解析散文；execute 顺序跑、worker 仅 3 工具、不能 dispatch；review 只是 LLM 主观 PASS/FAIL，无真实验证；**且节点间无数据流**。
- 现 `dag` 已退役（commit `ea74e3f`），但 `DAGScheduler` 保留——本 spec 让它"第一次接上真实输入"，成为 graph 的执行层。

## 3. 架构：四组件 → 现有代码映射

| LLMCompiler 组件 | 职责 | 落到 xhx |
|---|---|---|
| **Planner**（LLM） | 出带依赖 + `$id` 变量引用的任务 DAG | 新 `_plan_dag(ctx, client) -> DAGPlan`，结构化 JSON（见 §4），复用 `DAGNode/DAGPlan` |
| **Task Fetching + 变量替换** | 依赖就绪即派；把 `$id` 替换成前序节点真实输出 | 复用 `DAGScheduler.execute`（拓扑+并行+读写隔离）+ 新增**变量替换层**（现 `dag_runner` 缺，是灵魂） |
| **Executor**（节点执行体） | 每个节点跑一个**隔离子 agent** | 复用 `subagent.run_subagent`(explore) / `run_write_subagent`(edit，worktree+合并) |
| **Joiner**（LLM） | 看结果判：收尾 / 重规划 | 新 joiner 节点，替代现 `_review` 的"嘴上 PASS/FAIL" |

**保留**：现 coordinator 的**量级匹配出口**（闲聊/简单请求 → 直接 `ANSWER:` 不建图）原样保留——planner 之前先判"要不要建 DAG"。

## 4. 节点 schema 与变量替换（核心决策）

**决策 A：planner 用结构化 DAG（JSON），不用 LLMCompiler 的散文格式。** 理由：真模型（DeepSeek）出结构化 JSON 比解析自由文本鲁棒得多（前车之鉴：dispatch 不被采用、apply_patch 格式老错）。

复用并轻扩 `modes.DAGNode`（它已有 `dependencies` + `result`，天然适配）：
```
DAGNode(
    node_id:   str            # 唯一 id，供 $ 引用
    agent_type:str            # "explore"(只读) | "edit"(写)；复用现 tool 字段或新增
    prompt:    str            # 给子 agent 的指令，可含 $<node_id> 引用前序输出
    dependencies: list[str]   # 前序 node_id（已存在）
    result:    str | None     # 子 agent 浓缩结论（已存在，作变量替换的数据源）
    status:    str            # pending/running/success/failed/blocked（已存在）
)
```

**决策 B：变量替换语法 = `$<node_id>`**（比 `$1/$2` 可读、不依赖顺序）。执行某节点前，把 `prompt` 里所有 `$<dep_id>` 替换成对应 dep 的 `result`。例：
```
n1: explore  prompt="找出 auth 模块所有公开函数"
n2: explore  prompt="找出 db 模块所有公开函数"
n3: edit     prompt="基于 $n1 和 $n2，给两模块加类型注解"   deps=[n1,n2]
```
n1/n2 并行 → 各自 result 回填 → n3 的 `$n1/$n2` 替换为真实结论 → 执行。

## 5. 控制流（LangGraph）

```
entry → planner ──(chat 直答)──────────────→ END
            │
            └─(产出 DAG)→ schedule(DAGScheduler) → joiner ──(PASS)→ END
                              ▲                        │
                              └────(replan: 回 planner，≤N 轮)
```
- `schedule` 节点内：`DAGScheduler.execute(plan, execute_node)`；`execute_node` = 变量替换 + 调子 agent + 写回 `node.result`。
- 并行：`DAGScheduler` 已对只读节点并发、写节点隔离调度；edit 子 agent 的 worktree/合并并发硬化见 §6。
- joiner：把各节点 result + 原任务交 LLM，判 finish/replan；replan 有界（≤2）。

## 6. 并发正确性（吸收原 "Slice 1b"）

edit 子 agent 并行执行时必须硬化（否则竞态）：
1. `OrchestratorContext` 加 `subagent_lock: threading.Lock`。
2. `subagent.py` 的 `sub_run_id` 改 `uuid4`（现用 `len(claims)`，并行会撞 → worktree 路径冲突）。
3. `_merge_into_parent`（读写 `ctx.subagent_claims` + 拷文件进父区）包进 `subagent_lock` 临界区。
4. `git worktree add` 并行创建可能撞 git 锁 → 串行化 worktree 创建（或验证 git 自带锁足够，加重试）。
5. `max_parallel` 上限，防嵌套子 agent LLM 调用烧 token/撞限流。
> 注：explore 并发已在 loop 落地（commit `1fc731a`），无写隐患；这里是 **edit 并发** 的硬化。

## 7. 分阶段（每片可独立交付 + 测试 + 真模型联调）

- **P1 — planner + 串行执行 + 变量替换**：planner 出结构化 DAG；`DAGScheduler` 串行跑（max_workers=1）；实现 `$id` 替换；executor=explore 子 agent。先把"LLM 出图 + 数据流"跑通（最不确定的一环优先验证）。
- **P2 — 并行 + edit 节点 + 并发硬化**：放开并行；edit 子 agent + §6 全套锁/uuid/worktree。
- **P3 — joiner / 有界 replan**：替换嘴上 review，加真 joiner + 回边。
- **P4（可选）— 真实验证集成**：DAG 末尾自动插 verify 节点，joiner 看真实测试结果（补齐"比 plan 弱"的短板）。

## 8. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 真模型不产出可解析 DAG / 不爱建图 | 高 | 结构化 JSON + 严格 schema 校验 + 解析失败兜底成单节点；few-shot 示例；先在 P1 用真模型验证可行性再往下 |
| 变量替换错位 / 悬空 `$id` | 中 | 校验所有 `$id` ∈ deps；缺失则报错回 planner |
| edit 并行竞态 | 中 | §6 锁 + uuid + worktree 串行 + 并发竞态测试 |
| 复杂度膨胀、维护负担 | 中 | 分阶段；每片独立可回退；作品集项目可接受更高复杂度换"展示深度" |

## 9. 测试策略

- planner：给定任务，断言产出的 DAG 结构（节点/依赖/变量引用）；解析失败兜底。
- 变量替换：单测 `$id`→result 替换、悬空引用报错。
- 调度：复用现有 `DAGScheduler` 测试；新增"节点=子 agent"的 e2e（fake client）。
- 并发：`Barrier` 证明并行（同 loop 1a）；edit 并发的 claim 冲突仍正确检测、无文件串改。
- joiner：PASS 收尾 / FAIL 回 planner（≤2 轮）。
- 真模型联调：DeepSeek 跑一个"并行查 2 模块 → 汇总改 1 处"的任务，端到端。

## 10. 已拍板的决定（2026-06-14）

1. **v1 范围 → P1 先行**：先只落地 P1（planner 出 DAG + 变量替换 + 串行/只读并行 + explore 节点），作为"真模型能否稳定产出 DAG"的验证 gate；过了再依次 P2/P3/P4。
2. **节点执行体 → 子 agent**：每个 DAG 节点 = 一个隔离子 agent（自带 LLM 循环、`run_subagent`/`run_write_subagent`），而非单个工具调用。这正是旧 dag 走不通的根因（旧节点是写死参数的单次工具调用，planner 须预知实现细节）。
3. **P4 真实验证 → 纳入**：joiner 基于真实测试结果判定，补齐 graph 比 plan 弱的短板。
4. **执行通道 → Gemini**：每片我出自包含计划 + 关键 check 点，Gemini 实现，我两段式审查 + 全量回归 + 真模型联调 + 合并。

---

相关：[[roadmap-direction]]、[[gemini-handoff-lean-plans]]、loop 并行 dispatch 计划 `docs/superpowers/plans/2026-06-14-loop-parallel-dispatch.md`。
