# 运行时契约

本文档定义 xhx-agent 各模块之间的最小公共协议。实现可以使用 Python dataclass 或 Pydantic model，但字段语义必须保持一致。

## 设计原则

- 底层模块只依赖公开类型，不依赖对方内部实现。
- Runtime 负责装配和持久化。
- UI 只消费事件，不直接调用模型或工具。
- 完整记录落盘，模型上下文只接收摘要。

## ModelProfile

模型 profile 描述一次模型调用需要的配置。

```json
{
  "name": "default",
  "provider": "openai-compatible",
  "base_url": "https://api.example.com/v1",
  "api_key_env": "XHX_API_KEY",
  "model": "qwen-plus",
  "temperature": 0.2,
  "stream": true
}
```

规则：

- `api_key_env` 必须是环境变量名，不直接保存密钥。
- v0.1 提供 `openai-compatible` 和 `mock` 两种 provider。
- 新项目默认使用 `mock`，真实模型通过显式 profile 启用。
- `stream=false` 只作为测试 fallback；v0.1 真实模型客户端先实现非流式计划请求。

## ModelMessage

模型层使用统一消息结构。

```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "请修复 failing tests"
    }
  ]
}
```

角色：

- `system`
- `user`
- `assistant`
- `tool_result`

内容块：

- `text`
- `tool_call`
- `tool_result`

v0.1 不要求图片、音频和 thinking block。

## ModelEvent

模型流式输出统一成事件。

```json
{
  "type": "text_delta",
  "text": "正在分析",
  "message_id": "msg_001"
}
```

事件类型：

- `message_start`
- `text_delta`
- `tool_call_start`
- `tool_call_delta`
- `tool_call_end`
- `message_end`
- `error`

模型层只产出这些事件，不执行工具。

## ToolSpec

工具注册时暴露给 Agent loop 的定义。

```json
{
  "name": "read_file",
  "description": "Read a UTF-8 text file from the workspace.",
  "risk": "safe",
  "execution_mode": "parallel",
  "parameters_schema": {
    "type": "object",
    "required": ["path"]
  }
}
```

字段：

- `name`
- `description`
- `risk`
- `execution_mode`: `parallel` 或 `sequential`
- `parameters_schema`

规则：

- 写入类工具必须是 `sequential`。
- `terminal` 的风险由 safety 根据命令二次判断。

## ToolCall

模型请求执行工具时使用。

```json
{
  "id": "call_001",
  "name": "read_file",
  "arguments": {
    "path": "README.md"
  }
}
```

规则：

- `id` 在一次 run 内唯一。
- `arguments` 必须通过工具 schema 校验。
- Runtime 必须在执行前调用 policy check。

## ToolResult

工具执行结果必须结构化。

```json
{
  "tool_call_id": "call_001",
  "tool_name": "read_file",
  "status": "success",
  "summary": "Read README.md, 120 lines.",
  "content_ref": "trace://session-1/tool-call-001",
  "stdout": null,
  "stderr": null,
  "exit_code": null,
  "changed_files": [],
  "error": null
}
```

字段：

- `status`: `success`、`failed`、`denied`、`requires_confirmation`
- `summary`
- `content_ref`
- `stdout`
- `stderr`
- `exit_code`
- `changed_files`
- `error`

规则：

- 大输出写入 Raw Trace，`summary` 进入上下文候选。
- 工具不直接写 Evidence Index，由 Runtime 包装写入。

## PolicyDecision

Safety 输出的权限决策。

```json
{
  "decision": "confirm",
  "risk": "confirm",
  "reason": "npm test may execute project scripts.",
  "requires_user": true
}
```

`decision`：

- `allow`
- `confirm`
- `deny`

`risk`：

- `safe`
- `confirm`
- `deny`

规则：

- `deny` 不允许 Runtime 继续执行该工具。
- 用户拒绝后不能换等价命令绕过。

## AgentEvent

Agent loop 向 Runtime、CLI、TUI 发事件。

```json
{
  "type": "tool_start",
  "run_id": "run_001",
  "tool_call_id": "call_001",
  "tool_name": "search",
  "summary": "Searching for failing function."
}
```

事件类型：

- `run_start`
- `turn_start`
- `model_message_start`
- `model_delta`
- `model_message_end`
- `tool_start`
- `tool_end`
- `policy_decision`
- `verification_start`
- `verification_end`
- `repair_start`
- `repair_end`
- `run_end`
- `error`

规则：

- TUI 只根据事件更新显示状态。
- Evidence Runtime 可以从事件生成 trace entry。

## SessionEntry

session 使用 JSONL，一行一个事件。

```json
{
  "type": "message",
  "id": "entry_001",
  "run_id": "run_001",
  "created_at": "2026-05-24T12:00:00Z",
  "payload": {}
}
```

v0.1 最小类型：

- `session_start`
- `user_message`
- `assistant_message`
- `tool_result`
- `verification_result`
- `summary`

后续类型：

- `model_change`
- `compaction`
- `checkpoint`
- `skill_load`
- `replay_marker`

## RawTraceEntry

Raw Trace 记录完整运行事实。

```json
{
  "type": "tool_call",
  "id": "trace_001",
  "run_id": "run_001",
  "created_at": "2026-05-24T12:00:00Z",
  "payload": {
    "tool_name": "terminal",
    "args_summary": "python -m pytest",
    "exit_code": 0,
    "stdout_ref": "artifact://trace_001_stdout"
  }
}
```

规则：

- 可以存大输出，但不能保存密钥和完整环境变量。
- 绝对临时路径要脱敏。
- Raw Trace 默认不进入 Prompt。

## EvidenceEntry

Evidence Index 保存可检索摘要。

```json
{
  "id": "ev_001",
  "kind": "test",
  "source": "python -m pytest",
  "summary": "12 tests passed.",
  "artifact_ref": "trace://trace_001",
  "confidence": 0.95,
  "task_id": "task_001",
  "created_at": "2026-05-24T12:00:00Z"
}
```

`kind`：

- `file`
- `command`
- `test`
- `patch`
- `user`
- `error`
- `decision`

规则：

- patch 必须关联至少一个 EvidenceEntry。
- Context Pack 只读取 EvidenceEntry 摘要，不直接读取全部 Raw Trace。

## ContextPack

每轮发给模型的上下文包。

```json
{
  "goal": "Fix failing tests",
  "mode": "linear-edit",
  "project_summary": "...",
  "plan": [],
  "code_snippets": [],
  "evidence": [],
  "recent_failures": [],
  "constraints": []
}
```

规则：

- 必须记录来源。
- 必须可裁剪。
- 超预算时先裁剪历史摘要，再裁剪低置信度 evidence。

## VerificationPlan

验证路由输出。

```json
{
  "commands": [
    {
      "command": "python -m pytest",
      "reason": "Python project with tests directory.",
      "risk": "confirm"
    }
  ],
  "skip_reason": null
}
```

规则：

- command 不直接执行。
- Runtime 仍需经过 Safety。

## RunResult

一次任务的最终结构化结果。

```json
{
  "run_id": "run_001",
  "status": "success",
  "changed_files": ["src/foo.py"],
  "commands": ["python -m pytest"],
  "verification": "passed",
  "summary_path": ".xhx/logbook/run_001.md",
  "risk_summary": []
}
```

`status`：

- `success`
- `failed`
- `partial`
- `cancelled`

规则：

- CLI、TUI、JSON 输出都从 `RunResult` 派生。
- 不从自由文本总结中反解析状态。
