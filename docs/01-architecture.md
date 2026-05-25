# 架构设计

xhx-agent 使用 Python + LangGraph 构建。架构中心是 Agent Runtime，而不是单个图结构或单个 Prompt。Runtime 负责上下文编译、证据管理、安全执行、规划模式选择和验证路由。

## 运行流程

```text
User Input
  -> CLI / REPL
  -> Session Loader
  -> Navigator
  -> Context Pack Compiler
  -> Adaptive Planner
  -> Safe Execution Kernel
  -> Verification Router
  -> Evidence Runtime
  -> Reviewer / Summarizer
  -> Final Response
```

## 五个核心内核

### Context Pack Compiler

每轮 LLM 调用前编译上下文包。它负责在 token 预算内选择：

- 用户目标。
- 当前计划。
- 项目规则和 `XHX.md`。
- 相关代码片段。
- 最近失败点。
- 少量高价值证据摘要。

完整日志不会直接进入上下文。

### Evidence Runtime

保存完整轨迹和证据索引。它负责：

- Raw Trace 落盘。
- Evidence Index 摘要。
- Context Evidence 按需进入 Prompt。
- TrailGraph 任务、证据和决策关系。

### Safe Execution Kernel

统一管理所有工具调用。流程是：

```text
tool request
  -> policy check
  -> risk classify
  -> confirm / deny / execute
  -> capture result
  -> write evidence
  -> route verification
```

### Adaptive Planner

根据任务复杂度选择执行模式：

- `direct`
- `research-only`
- `linear-edit`
- `plan-review-act`
- `dag-execute`
- `repair-loop`

DAG 只在复杂任务中使用。

### Verification Router

根据项目结构和 changed files 选择验证命令。它不能只依赖模型猜测，必须优先使用项目文件和既有脚本。

## 分层职责

- **CLI**：解析命令、渲染输出、处理交互确认、暴露 REPL 命令。
- **Runtime**：加载配置、模型 profile、会话、项目状态和工具注册表。
- **Agent Graph**：维护 LangGraph 状态流转和不同执行模式。
- **Tools**：提供受控的文件读取、搜索、终端和补丁能力。
- **Storage**：写入 `.xhx/` 下的 JSONL 状态、证据索引和 Markdown 报告。

## v0.1 图结构

v0.1 使用近似线性的图结构，优先保证第一版容易调试。

```text
classify -> build_context -> plan -> gather -> patch -> verify -> summarize
```

这一版仍要写入结构化状态，方便后续迁移到 DAG 调度。

## v0.7 图结构

v0.7 升级为基于依赖关系的多 Agent 调度。

```text
classify
  -> choose_mode
  -> plan DAG
  -> execute read-only tasks in parallel
  -> serialize write tasks by file
  -> verify changed files
  -> review
  -> synthesize
```

## Agent 角色

- **Navigator**：识别任务类型，加载相关项目上下文。
- **Planner**：生成任务步骤和依赖关系。
- **Researcher**：执行只读探索并收集证据。
- **Coder**：生成并应用结构化补丁。
- **Verifier**：推断并执行验证命令。
- **Reviewer**：判断任务结果是否满足目标。
- **Archivist**：写入 Evidence Runtime、TrailGraph 和 Markdown 日志。

## 状态边界

实现时要分离以下状态：

- 用户可见的对话状态。
- Agent 图运行状态。
- 工具执行日志。
- Evidence Runtime 状态。
- Context Pack 临时上下文。
- 持久化会话状态。

当可以使用结构化结果时，不应依赖原始 LLM 文本。这能让后续回放、评测和调试更可靠。

## 存储布局

```text
.xhx/
  config.json
  profiles.json
  sessions/
  traces/
  evidence/
  logbook/
  skills/
XHX.md
```

`XHX.md` 是项目级知识摘要。`.xhx/traces/` 保存原始事件。`.xhx/evidence/` 保存证据索引。`.xhx/logbook/` 保存人类可读报告。
