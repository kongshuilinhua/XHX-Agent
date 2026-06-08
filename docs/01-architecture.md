# 架构设计

XHX-Agent 采用高内聚、低耦合的分层软件架构构建，核心基于 Python、Pydantic 和 LangGraph 的 `StateGraph` 工作流控制流框架。系统的架构中心是 **Agent Runtime (智能体运行时)**，由五个协同运作的核心内核以及一套精密的代码智能图谱（Repository Intelligence Graph）驱动，能够自适应地根据任务复杂度进行决策，并以极速、安全、100% 可审计的方式完成本地代码的“读-改-测-自愈-审计”闭环。

---

## 运行流程与决策树

当用户输入任务时，运行时的工作流如下：

```text
       User Request
            │
            ▼
    [ Intent Classifier ] (意图识别与任务分类)
            │
      ┌─────┼──────────────────────┐
      │     │                      │
      ▼     ▼                      ▼
  [Direct] [Research Only]  [DAG Parallel] (自适应选择规划模式)
                                   │
                                   ▼
                            [Kahn DAG Planner] (拓扑编译与死锁检测)
                                   │
                                   ▼
                        [Hybrid Parallel Scheduler]
                        ├── 只读任务：多线程并发
                        └── 写入/测试任务：单线程串行
                                   │
                                   ▼
                         [Safe Execution Kernel] ──(修改前 Git Checkpoint)
                                   │
                                   ▼
                         [Verification Router] ──(受影响测试靶向运行)
                                   │
                                   ┌┴──────────────┐
                                   ▼               ▼
                               [测试通过]      [测试失败]
                                   │               │
                                   │               ▼
                                   │       [2-Turn Auto-Repair Loop] (自愈自修复)
                                   │               ├── 成功 ──> [测试通过]
                                   │               └── 失败 ──> [git worktree 丢弃 / 就地还原计划]
                                   │
                                   ▼
                         [Evidence Runtime & Summarizer]
                                   │
                                   ▼
                          Final Report & Audit
```

---

## 五大核心内核

### 1. Context Pack Compiler (上下文编译内核)

每轮 LLM 调用前，该内核负责在严格的 Token 预算限制内选择高价值信息，编译为上下文包，以杜绝上下文爆炸与历史污染。
- **动态优先级裁剪**：按照“用户目标 > 当前任务计划 > 项目规则 (`XHX.md`) > AST 符号关联代码上下文 > 最近失败点/测试堆栈 > Evidence Top-K 摘要 > 滑动历史压缩摘要”的优先级在 Token 满溢时进行自动截断。
- **历史压缩 (compaction)**：自主 `loop` 跑多轮时，溢出最近窗口的旧工具摘要会被启发式聚合为一条统计摘要（工具调用次数 + 失败计数）保留，而非直接丢弃，帮助长 loop 维持长程一致性（`_compact_tool_summaries`，纯启发式、无额外 LLM 调用）。
- **冷启动 Repo Map**：提取项目的轻量级物理地图，让模型在宏观上理解仓库目录。
- 📖 深入源码分析请阅读：[01-Context Pack Compiler 源码深潜](deep_dive/01-context-pack-compiler.md)

### 2. Evidence Runtime (证据追踪与审计内核)

维护一套完全脱离大模型上下文、在本地 100% 结构化落盘的 Raw Trace 与证据索引系统。
- **Raw Trace 记录**：完整保存智能体与宿主机环境交互的所有原始细节。
- **Evidence Index**：自动生成每次工具调用和观察的高浓度摘要，并采用 Top-K 检索算法按需将少量相关证据送入模型上下文。
- **TrailGraph 拓扑图**：维护决策、命令、文件变动与证据的有向依赖网络，在任务结束时，一键自动渲染为可审计的人类可读 Markdown 日志（Logbook）。
- 📖 深入源码分析请阅读：[02-Evidence Runtime 源码深潜](deep_dive/02-evidence-runtime.md)

### 3. Safe Execution Kernel (安全执行内核)

是智能体与宿主机操作系统进行物理交互的唯一安全关口。
- **命令风险三级评级机制**：
  - `Safe`（只读操作，如读文件、静态搜索）：自动执行。
  - `Confirm`（具有副作用的指令，如 pytest、应用补丁）：进入命令交互，请求用户授权。
  - `Deny`（越权或极其危险的指令，如 rm -rf、系统级配置重置）：一律强制拦截。
- **Worktree 隔离回滚**：在 git 仓库内运行时，编辑在隔离的 git worktree 中进行，仅在成功时同步回主工作区；任务失败或中止时直接丢弃该 worktree，从而实现到基线状态的回滚。
- **就地执行的还原计划**：若目标不是 git 仓库（或 worktree 创建失败），改动将就地写入工作区；此时运行时会显式发出 `isolation_degraded` 告警，并生成只读的 Restore Plan 记录变更点以供人工恢复——**此路径下不执行自动回滚**。
- 📖 深入源码分析请阅读：[03-Safe Execution Kernel 源码深潜](deep_dive/03-safe-execution-kernel.md)

### 4. Adaptive Planner & Parallel DAG (自适应规划与并行拓扑调度内核)

本内核是 XHX-Agent 维持极佳吞吐效率与确定性的任务调度大脑。
- **意图识别器 (Intent Classifier)**：根据用户需求和任务复杂度，自适应选择执行模式：
  - `Direct`：简单问答，无需探索。
  - `Research Only`：仅收集分析信息，不执行代码修改。
  - `DAG Execute`：复杂多节点依赖开发任务的路线。

> **实现现状（v1.0）**：DAG 的节点生成目前是基于意图关键词的启发式基线（拓扑排序、环检测、读写隔离调度均为真实实现），尚未接入 LLM 对任意需求的自动拆解。开放式编辑任务建议走 `linear-edit` 模式，由模型工具循环逐步完成。
- **Kahn 拓扑排序算法**：在编译期对 DAG 任务进行零入度排序与有向环检测，100% 杜绝多任务间的死锁；采用 `queue.sort()` 消除物理哈希种子带来的调度顺序随机性扰动，实现多平台上 100% 的调度可测性与可复现性。
- **读写隔离混合并发调度模型**：
  - **多线程只读并发**：所有的只读节点（如读文件、静态搜索）打包提交至 `ThreadPoolExecutor` 并发执行（默认最大 8 线程），压榨极致吞吐。
  - **单线程写入串行**：写节点（如打 Patch、运行 terminal 验证）被严格退化为单线程同步串行，从底层杜绝了并发脏写（Dirty Writes）和文件版本冲突。
- **2-Turn 自适应故障修复回路**：如果自动化验证失败，系统捕获堆栈并进入最大 2 次尝试的自动 Repair 循环，若 2 次均失败则优雅触发 Git 回滚保护退出。
- 📖 深入源码分析请阅读：[04-DAG Planner 源码深潜](deep_dive/04-dag-planner.md)

### 5. Verification Router (自动化测试与验证路由内核)

代替大模型猜测，以极高的确定性自动推断并触发靶向的测试执行指令。
- **多语言验证命令推断**：支持自动识别项目技术栈（Python 识别 pytest，Node.js 识别 vitest/jest 等）。
- **精准影响面分析**：依托 Repo 智能图谱，快速匹配改动点受影响的测试用例并精细化拼接指令，从而规避全量验证，将智能体的验证等待时间缩短 90% 以上。

---

## 顶层执行范式：可插拔 Orchestrator

在五大内核与代码智能底座之上，XHX-Agent 把"顶层控制流大脑"抽象为可插拔的 `Orchestrator`（`src/xhx_agent/orchestrators/`）。同一套工具、安全执行内核、Context Pack 与 Evidence 底座被不同范式共享，仅顶层控制流可替换：

- **`loop`（统一自主 agent loop，类 Claude Code）**：单一循环，模型每一步自主决定下一个工具调用，配合人在回路的确认 / 打断 / steering。**默认主力范式。**
- **`graph`（多 agent 工作流编排，类 HPD）**：评估 → 分解 → 并行 → 评审 → 综合的状态图，带条件分支与循环回边；计划基于 LangGraph 实现。

`run_task(mode=...)` 显式选择范式（`loop` / `graph`），未指定时由 `ModeClassifier` 兜底，`select_orchestrator` 统一分派；`OrchestratorContext` 携带共享底座句柄与运行参数。

> **实现现状（M2，2026-06-08）**：抽象层、registry 选择、`run_task(mode=...)` API 已就位。`loop` 已实现为**自主统一循环**——模型持续多轮 read→edit→verify，直到自报完成或达 `max_loop_turns`（默认 20），而非改一个文件就停；自动分类兜底仍走 `linear`（改动后即停，向后兼容）。路线：`graph` 的 LangGraph 实现（M3）、`--mode` CLI/TUI 接入（M4），见 `docs/implementation/20-implementation-baseline.md`。

---

## 核心底座：Repository Intelligence Graph (代码智能图谱)

为了让运行时拥有高维度的语义代码认知底座，XHX-Agent 引入了基于 SQLite 物理数据库与抽象语法树（AST）的多维语义大图系统。
- **双轨制索引架构**：使用扁平 JSON (`.xhx/repo/index.json`) 承载冷启动内存流，通过高性能本地 SQLite B-Tree 索引数据库 (`.xhx/repo/index.db`) 承载高频交叉关联联表查询（定义表、模块导入图表、全局交叉文本引用表、函数级调用有向图）。
- **多语言 AST 符号提取**：Python 采用标准库 `ast` 进行深度优先作用域嵌套分析；JS/TS 采用分级降级机制，优先使用精密的 Tree-Sitter 语法提取（支持现代前端箭头函数与别名解析），在环境缺失时优雅降级为 Regex 正则符号提取，兼顾了极致精度与环境鲁棒性。
- **启发式测试匹配与 BFS 逆向追溯**：根据变更文件，提供 `calc.py -> test_calc.py` 物理秒级匹配；若匹配不成功，则以变更源为起点在 SQLite 模块导入图上逆向执行最大 4 层深度的宽度优先搜索 (BFS) 拓扑遍历，直至触碰到测试文件边界，实现高精度的“靶向验证”。
- 📖 深入源码分析请阅读：[05-Repo Intelligence 源码深潜](deep_dive/05-repo-intelligence.md)

---

## 插件机制：Dynamic Skills Loader (动态技能加载内核)

在不膨胀核心系统 Prompt 预算的前提下，XHX-Agent 实现了强大的本地 Skill/Extension 系统。
- **本地扩展加载**：扫描 `.xhx/skills/<name>/SKILL.md`，每个 Skill 配备独立的 YAML metadata 描述。
- **懒加载与按需披露 (Progressive Disclosure)**：日常运行时仅常驻加载轻量级 Skill metadata（触发词、权限限制），只有当用户意图匹配或特定阶段命中时，才将 Skill 详细正文动态喂入 Context Pack Compiler 中。
- **Extension Hooks 挂载**：在生命周期核心节点上，Runtime 暴露了丰富的挂载钩子：
  - `before_plan`：调整 Kahn DAG 生成规划。
  - `before_patch`：在打补丁前微调 patch 策略。
  - `after_verify`：测试完成后介入风险二次评估。
  - `before_summary`：在日志输出前介入格式整理。

---

## 状态边界与存储布局

XHX-Agent 在运行时严格分离用户可见的对话状态、LangGraph 图控制流状态、Tools 日志、Evidence 审计轨迹以及 Repo Index，确保系统处于完美的数据隔离状态：

```text
.xhx/
  config.json          # 宿主机全局模型与策略配置文件
  profiles.json        # 各种模型供应商配置参数
  index.db             # 核心底座: SQLite 高性能 B-Tree 符号索引数据库
  repo/
    index.json         # 双轨底座: 结构化冷启动仓库指纹与索引 JSON
  sessions/            # 对话会话历史
  traces/              # 100% 落盘的智能体物理运行事件 Raw Trace
  evidence/            # 100% 结构化的证据数据与 TrailGraph 拓扑
  logbook/             # 人类可读的可审计 Markdown 报告 (每次运行生成)
  checkpoints/         # 本地轻量级 Git 暂存备份快照
  skills/              # 用户自定义本地动态 Skill/Extension 模块目录
XHX.md                 # 自动维护的项目代码地图与全局指令规范
```
