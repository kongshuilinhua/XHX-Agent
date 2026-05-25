# Evidence Runtime 与 TrailGraph

Evidence Runtime 是 xhx-agent 的审计与证据层。它完整记录 Agent 的工具调用、观察、补丁、验证和决策，但不会把这些内容全部塞进 LLM 上下文。

TrailGraph 保留为 Evidence Runtime 的内部表示之一，用于组织任务、证据和决策之间的关系。

## 设计目标

- 完整记录 Agent 运行轨迹。
- 为每个重要结论提供证据来源。
- 让报告可审计、可回放、可排查。
- 避免日志和历史污染 LLM 上下文。
- 支持按需展开原始证据。

## 三层结构

### Raw Trace

Raw Trace 保存完整事件。

示例内容：

- 工具调用参数。
- 命令 stdout/stderr。
- patch 文本。
- 验证命令退出码。
- LLM 结构化输出。
- 用户确认结果。

Raw Trace 默认写入磁盘，不直接进入 Prompt。

建议路径：

```text
.xhx/traces/<session-id>.jsonl
```

### Evidence Index

Evidence Index 保存可检索摘要。

字段：

- `id`
- `kind`: `file`、`command`、`test`、`patch`、`user`、`error`、`decision`
- `source`
- `summary`
- `artifact_ref`
- `hash`
- `confidence`
- `task_id`
- `created_at`

Evidence Index 是 Context Pack Compiler 的主要输入。

建议路径：

```text
.xhx/evidence/<session-id>.jsonl
```

### Context Evidence

Context Evidence 是每轮允许进入 Prompt 的少量证据摘要。

选择规则：

- 与当前任务强相关。
- 与最近失败点相关。
- 与 changed files 相关。
- 置信度高。
- 不超过上下文预算。

## TrailGraph 内部模型

TrailGraph 由三类节点组成。

### TaskNode

表示一个计划步骤。

字段：

- `id`
- `title`
- `status`
- `depends_on`
- `assigned_role`
- `started_at`
- `finished_at`

### EvidenceNode

表示一个观察结果或来源。

字段：

- `id`
- `kind`
- `source`
- `summary`
- `artifact_ref`
- `confidence`
- `created_at`

### DecisionNode

表示 Agent 做出的一次决策。

字段：

- `id`
- `question`
- `chosen`
- `rejected`
- `rationale`
- `evidence_ids`
- `risk`

## 上下文规则

- 完整命令输出不直接进入 Prompt。
- 大文件片段不直接进入 Prompt。
- Raw Trace 默认只落盘。
- Evidence Index 可以被检索。
- Context Evidence 必须短、相关、可追溯。
- 需要原始内容时，通过 `artifact_ref` 按需展开。

## 证据规则

- 文件修改必须引用至少一个 EvidenceNode。
- 测试结果必须记录命令、退出码和输出摘要。
- 用户要求在相关时也算证据，必须保留。
- 不保存密钥、完整环境变量和无关绝对路径。
- 低置信度证据不能直接触发高影响修改。

## Markdown 审计报告

报告应包含：

- 任务目标。
- 执行模式。
- 关键计划步骤。
- 关键证据摘要。
- 修改过的文件。
- 验证命令和结果。
- 决策理由。
- 剩余风险。

## 反模式

- 把完整 pytest 输出塞进 Prompt。
- 把所有历史消息长期常驻上下文。
- 没有 evidence id 就生成重要结论。
- 把 Evidence Runtime 当作图数据库在 v0.1 强行实现。
