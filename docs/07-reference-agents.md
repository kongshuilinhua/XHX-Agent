# 参考 Agent 项目

xhx-agent 会参考多个已有编码 Agent 项目，但不直接复刻任何一个。

## HPD-Agent

参考：

- DAG 任务拆解。
- Reviewer 回路。
- `apply_patch` 写入边界。
- 代码智能方向。

用于 xhx-agent：

- 作为复杂任务调度参考。
- DAG 放到 Adaptive Planner 的复杂任务路径里。

v0.1 不照搬：

- 完整 LSP provider 栈。
- SQLite 符号索引。
- 大范围 code intelligence。

## pi

参考：

- Runtime 与 package 分层。
- JSONL session history。
- Skill progressive disclosure。
- 事件流和未来 RPC 思路。

用于 xhx-agent：

- 作为工程结构参考。
- 支撑 Evidence Runtime、Skill 和后续 RPC 设计。

v0.1 不照搬：

- TypeScript monorepo 架构。
- 完整 TUI/RPC 栈。
- 大范围 provider matrix。

## aider

参考：

- repo map。
- 精确补丁。
- lint/test 修复回路。
- Git-aware 工作流。

用于 xhx-agent：

- 作为 Repo Intelligence Graph 和验证修复参考。
- 不把整个仓库塞进上下文，而是使用压缩地图和相关片段。

v0.1 不照搬：

- 自动 commit flow。
- 强 Git 工作流假设。

## Cline 和 Roo Code

参考：

- Plan/Act 分离。
- 权限确认。
- 自定义模式。
- IDE-Agent 产品模式。

用于 xhx-agent：

- 作为 Adaptive Planner 和执行模式参考。
- 将 mode 定义为 prompt、tools、budget、policy 的组合。

v0.1 不照搬：

- IDE 插件架构。
- 浏览器自动化能力。
- marketplace 行为。

## OpenHands 和 SWE-agent

参考：

- 真实仓库任务闭环。
- sandbox 和 headless 执行思路。
- benchmark 驱动评测。

用于 xhx-agent：

- 作为 headless、benchmark、任务回放和失败分类参考。

v0.1 不照搬：

- 云平台假设。
- 完整 sandbox 编排。
- 企业工作流范围。

## Continue

参考：

- context providers。
- Markdown check 报告。
- 面向 CI 的状态输出。
- IDE 生态思路。

用于 xhx-agent：

- 作为 Context Pack Compiler 和后续 CI 报告参考。

v0.1 不照搬：

- PR 平台集成。
- IDE-first 架构。

## 设计规则

用 HPD-Agent 参考智能调度，用 pi 参考工程结构，用 aider / Continue / OpenHands / SWE-agent 参考上下文、验证和评测。xhx-agent 的核心差异是 Context-Budgeted Agent Runtime。
