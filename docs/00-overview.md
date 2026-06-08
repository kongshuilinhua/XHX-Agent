# 项目概览

XHX-Agent 是一款完全实现的、工业级的**上下文预算驱动的本地编码 Agent 运行时 (Local Coding Agent Runtime in v1.0.0)**。它以完全独立且高度安全的方式运行在用户本地机器上，通过 CLI/TUI 交互，具备精准读取项目文件、基于 AST 的符号级检索代码、多线程并发只读任务调度、安全执行终端验证指令、生成结构化补丁以及自适应故障修复的核心能力，并输出 100% 可追溯的结构化任务审计报告。

本系统彻底攻克了传统编码智能体面临的几大工程痛点：上下文爆炸（Token Explosion）、工具执行越权不安全（Unsafe Tool Execution）、验证机制缺失、历史对话污染以及修复失败后无法复盘回滚的问题。

## 定位

XHX-Agent 是一款上下文预算驱动、证据 100% 可审计追溯、执行过程绝对安全的工业级本地编码 Agent 运行时。

这包含三大底层工程支柱：

- **自适应上下文预算驱动**：对每轮 LLM 调用的 Token 进行严格的动态规划与滑动窗口裁剪，保证核心上下文的极高相关度与极低冗余度。
- **证据链级可追溯可审计**：运行轨迹（Raw Trace）与证据索引（Evidence Index）完全落盘。所有代码更改必须绑定对应的证据，实现可信推演。
- **运行期确定性安全执行**：工具调用完全经过安全内核策略过滤器（Policy Gate）的风险等级（Safe / Confirm / Deny）检测，内置自动 Git 快照和 2-Turn 自适应故障修复及回滚能力。

## 五大核心内核 (Core Kernels)

XHX-Agent 的核心由五大成熟的底层内核驱动，它们协同工作以保障 Runtime 的健壮性与稳定性：

1. **Context Pack Compiler (上下文编译内核)**：
   在 Token 预算限制下，动态编译每轮 Prompt 的上下文包。通过项目地图、当前任务计划、符号关联上下文、最近失败点和 Evidence Top-K 摘要等优先级裁剪策略，防止 Token 爆炸与无关历史污染。
   - 📖 详细设计请参阅私密导读：[01-Context Pack Compiler 源码深潜](deep_dive/01-context-pack-compiler.md)

2. **Evidence Runtime (证据追踪与审计内核)**：
   维护 100% 落盘的 Raw Trace 与证据索引，支持 TrailGraph 内部拓扑关系。所有决策、工具调用与代码 Patch 均与明确的证据挂钩，并可一键渲染为人类可读的 Markdown 审计报告。
   - 📖 详细设计请参阅私密导读：[02-Evidence Runtime 源码深潜](deep_dive/02-evidence-runtime.md)

3. **Safe Execution Kernel (安全执行内核)**：
   统一拦截与管理所有的工具调用，进行策略检查（Policy Check）与命令风险评级。在 git 仓库内通过隔离 worktree 执行编辑（成功才同步回主工作区、失败即丢弃）；非 git 场景下就地执行并生成只读 Restore Plan 记录变更点供人工恢复。
   - 📖 详细设计请参阅私密导读：[03-Safe Execution Kernel 源码深潜](deep_dive/03-safe-execution-kernel.md)

4. **Adaptive Planner & Parallel DAG (自适应规划与并行拓扑调度内核)**：
   基于意图识别与任务复杂度自适应选择执行模式（如 Direct Q&A, Research Only, DAG Execute 等）。复杂任务会被建模为有向无环图（DAG），利用 Kahn 算法进行拓扑排序与死锁检测，在执行期采用“只读任务多线程并发，写入/终端任务单线程串行”的线程安全混合并发调度，并内置 2-Turn 故障自修复回路。
   - 📖 详细设计请参阅私密导读：[04-DAG Planner 源码深潜](deep_dive/04-dag-planner.md)

5. **Verification Router (自动化测试与验证路由内核)**：
   结合代码智能图谱（Repository Intelligence Graph）计算受影响的测试集，为改动靶向推断并执行验证命令（支持 Pytest, Vitest, Jest 等测试框架），规避盲目的全量测试，大幅缩短验证周期。

## 目标用户

目标用户是追求高效、注重代码隐私与环境安全的本地开发者。这个用户需要一个可控、可审计、能深度检查仓库、修改代码、运行靶向验证并解释理论依据的高精度编码 Agent。

## 产品形态

XHX-Agent v1.0.0 提供了一个成熟、流畅的 CLI/TUI 命令控制台，同时在架构上完全分离了 Runtime、RPC 协议层与控制台前端，使得后续接入 TUI 交互式窗口、Headless CI 工作流和 RPC 远程调用成为现实。

## 设计原则

- **Token 刚性约束**：完整轨迹可以无限增长，但进入 Prompt 的上下文包必须经过优先级裁剪，保持在限定预算内。
- **安全不依赖声明**：安全边界由代码内核强行实现，不依赖提示词里微弱的“请谨慎行事”声明。
- **自适应策略调度**：DAG 拓扑调度是应对复杂依赖开发任务的高效武器，简单任务会自动降级到 linear 或 direct 模式以节省 Token。
- **验证驱动闭环**：每次代码修改都必须获得自动化测试的绿灯；无法跑通验证时，必须触发自愈修复或回滚保护。

## 设计非目标与安全边界 (Security Non-goals)

为了确保本地开发环境的绝对安全与数据的可控度，XHX-Agent 在设计上坚决排斥了以下高风险与臃肿行为：

- **不做 Web UI**：坚持 CLI/TUI 第一优先级，避免引入庞大的 Web 渲染堆栈和潜在的浏览器端远程命令执行（RCE）越权风险。
- **不自动 Commit、Push 或发起远程 PR**：所有的代码提交与远程推送权力必须严格交还给开发者本人，Runtime 仅在本地暂存区进行分支级别的 Checkpoint 管理。
- **不自动执行危险系统指令**：涉及敏感目录删除、系统全局配置修改或网络对外发送等指令一律进入 `Deny` 策略，或必须通过交互式 UI 得到用户的显式物理确认。
- **不做低精度的向量 RAG**：在理解代码库时，XHX-Agent 摒弃了模糊、易幻觉的基于 Embedding 的向量检索，完全依托于静态 AST 语法树解析与 SQLite 符号索引构建的 `Repository Intelligence Graph`。
- **不自动接入公网网络拉取依赖**：避免任意下载未经审计的第三方脚本或组件，维持纯本地运行的干净边界。

## 🔒 本地私密学习指南

欢迎进入 XHX-Agent 的学术深潜世界！为了让研究者和开发者能够深入理解系统的底层设计与源码级实现，我们在本地准备了极其详尽的私密导读与源码分析文档（位于 `docs/deep_dive/`）。

您可以在本地文本编辑器或 IDE 中，直接点击并阅读以下专属学术深潜文档：

- 📂 [Context Pack Compiler (上下文编译内核)](deep_dive/01-context-pack-compiler.md)：探索如何在有限 Token 预算下极致裁剪上下文，防止大模型幻觉与信息溢出。
- 📂 [Evidence Runtime (证据追踪与审计内核)](deep_dive/02-evidence-runtime.md)：剖析 Raw Trace 记录落盘、TrailGraph 拓扑推理与结构化审计报告渲染。
- 📂 [Safe Execution Kernel (安全执行内核)](deep_dive/03-safe-execution-kernel.md)：深入子进程沙箱隔离、命令风险拦截、Git 轻量备份与自愈回滚物理机制。
- 📂 [Adaptive Planner & Parallel DAG (自适应规划与并行拓扑调度内核)](deep_dive/04-dag-planner.md)：剖析基于 Kahn 算法的拓扑排序、读写分离多线程并发调度及 2-Turn 自愈修复环路。
- 📂 [Repo Intelligence Graph (代码智能与 SQLite 符号索引内核)](deep_dive/05-repo-intelligence.md)：解构抽象语法树（AST）提取、TS/JS 路径别名猜测解析、SQLite 索引 B-Tree 加速及测试影响分析。
