# 设计：`agent` 范式——对话 + 原生 tool-calling 统一循环（Phase 1）

- 日期：2026-06-10
- 状态：草案（待用户审阅）
- 关联背景：从 Claude Code 还原源码（`src/query.ts` 的统一 agent 循环）借鉴而来

---

## 1. 背景与定位

xhx-agent 当前的模型层是一个**规划器**：每轮无状态地重编上下文包（context pack），强制模型只返回计划 JSON `{summary, status, steps}`。这导致它**不支持正常对话**——问它"你是什么模型 / 解释架构"只会返回空计划，因为协议里没有"直接回答"这个分支。

项目定位（本次讨论确认）：**学习 + 找工作的作品集项目，目标是深度展示 agent 架构功底**。因此：

- **保留**现有独特设计（上下文预算化、双范式 loop/graph、安全内核、repo 索引）——它们是区分度/卖点。
- **新增** Claude-Code 式的"消息历史 + 原生 tool-calling 统一循环"，作为**收敛后的第三种「真范式」`agent`**（最终 3 范式：`loop` / `graph` / `agent`），补齐对话与"好用度"，并提供"我能复刻真实 agent 核心循环"的面试叙事。
- **配套范式收敛**（独立工作流，见 ROADMAP）：`linear` 降为 `loop` 的停止策略开关、`dag` 降为 `graph` 的并发执行层——去掉 loop/linear、dag/graph 的重叠，让每个范式对应一个公认概念（Plan-Execute / 多 agent 工作流 / Tool-use loop）。

### 核心机制（借鉴 Claude Code）

Claude Code 的 `query.ts` 是一个 `while(true)` 生成器循环：每轮调一次模型，**assistant 消息里有 `tool_use` 块才继续循环**（执行工具→把结果追加进 messages→再循环）；**只回纯文本就结束本轮，那段文本即是给用户的回答**。没有任何"聊天 vs 任务"分类器——对话只是"模型这一轮选择回文本而非调工具"的自然结果。

证据：`query.ts:826-835`（有 tool_use 才 `needsFollowUp=true`）、`query.ts:1062`（`if(!needsFollowUp)` → 结束本轮）。

---

## 2. 目标与非目标

### Phase 1 目标
1. 新增 `agent` 范式（`--mode agent` / `/mode agent`），与现有 `loop`/`graph` 并存，旧模式零改动。（`linear`/`dag` 的降级收敛是独立工作流，不在本 Phase。）
2. 支持**正常对话**：问问题→模型直接回文本→REPL/CLI 打印回答。
3. 支持 **tool-calling 干活**：模型可调用 `read_file`、`search`、`apply_patch` 三个工具，读代码回答、或真实改代码。
4. 复用现有 `SafeExecutionKernel`（risk 三档 + confirm）、worktree 隔离、evidence/trace。
5. 保留离线可跑：`mock` provider 提供确定性的 tool-calling 模拟，CI/无 key 仍能跑通。

### Phase 1 非目标（留待后续）
- 流式增量渲染（Phase 3）。
- 消息历史压缩 / microcompact（Phase 3）。
- 只读工具并发（Phase 2）。
- 终端/验证工具（terminal/verify）暴露给模型（Phase 2）。
- repo-intel 作为工具或注入上下文（Phase 3）。
- TUI 全屏仪表盘适配（Phase 4）。

---

## 3. 架构

新增 `src/xhx_agent/orchestrators/agent.py`，实现 `Orchestrator` 协议。复用 `OrchestratorContext` 里已经建好的 worktree / evidence / kernel / tool_context / scan。

六个部件：

| 部件 | 位置（建议） | 说明 |
|:--|:--|:--|
| 1. tool-calling 客户端 | `models/openai_compatible.py` 新增 `chat(messages, tools)` 方法（或新文件 `openai_tools.py`） | 发 `/chat/completions` 带 `tools` 参数，解析 `choices[0].message`：返回 `content` 文本或 `tool_calls` |
| 2. 工具 schema | `tools/registry.py` 增加 `tool_schemas()` | 把 search/read_file/apply_patch 导出为 OpenAI function 格式 |
| 3. 消息历史 | `agent.py` 内部 `messages: list[dict]` | system / user / assistant(含 tool_calls) / tool(tool_call_id) |
| 4. 统一循环 | `agent.py` | 见 §4 算法 |
| 5. 安全集成 | 复用 `kernel.execute_tool` | 每个 tool_call 包成 `ToolStep` 丢进去，risk/confirm/evidence 全复用 |
| 6. 渲染 | `cli/console.py`、`runtime/app.py` 的 RunResult | RunResult 增加 `answer: str \| None` 字段；REPL 打印 assistant 文本 |

### 模型协议适配
- DeepSeek 的 OpenAI 兼容 API **支持 function calling**（`tools` / `tool_calls`）。**实现前先用一个最小请求验证返回格式**（开放问题，见 §9）。
- `ModelProfile` 无需改 schema：agent 模式复用 `provider="openai-compatible"` 的 base_url/api_key_env/model；是否走 agent 由 orchestrator 决定，而非 profile。

---

## 4. 统一循环算法（Phase 1）

```
seed:
  messages = [
    {role: "system", content: AGENT_SYSTEM_PROMPT + 精简项目地图(scan)},
    {role: "user",   content: task},
  ]
  changed_files = []

for turn in range(max_agent_turns):            # 复用 config.max_loop_turns 作硬上限
  if cancelled: return cancelled
  resp = client.chat(messages, tool_schemas)   # 一次模型调用

  if resp.tool_calls:                          # —— 干活分支 ——
    messages.append(assistant_message_with(resp.tool_calls))
    for tc in resp.tool_calls:
      step = ToolStep(tool=tc.name, arguments=tc.arguments)
      result, trace, policy = kernel.execute_tool(ctx.tool_context, step, turn, cb)
      content = render_tool_result(result, policy)   # 被拒/失败→错误文本；成功→trace_payload 内容(截断预算)
      messages.append({role: "tool", tool_call_id: tc.id, content: content})
      if result and result.changed_files: changed_files += result.changed_files
      记录 evidence/trace（复用现有写入）
    continue                                   # 把工具结果回喂，再进一轮

  else:                                        # —— 对话分支 ——
    return RunResult(status="success", answer=resp.content, changed_files=changed_files, mode="agent")

# 超出轮数
return RunResult(status="failed", risk_summary=["agent 在 N 轮内未结束"])
```

要点：
- **对话 = 模型回纯文本**（无 tool_calls）→ 直接结束，`answer` 即回答。对应 Claude Code 的 `!needsFollowUp`。
- **改代码 = 模型回 tool_calls** → 执行、把结果作为 `role:tool` 消息追加、再循环。
- 与现有 `_run_model_tool_loop` 的本质区别：**消息历史追加式增长**，模型看到的是真实工具输出（截断后），而不是每轮重编的摘要包。

---

## 5. 工具 JSON Schema（Phase 1 三件套）

从 `registry.py:_validate_arguments` 直接推导：

```json
[
  {"type":"function","function":{
    "name":"search",
    "description":"在仓库内按文本搜索，返回匹配的文件/行。只读。",
    "parameters":{"type":"object","properties":{
      "query":{"type":"string","description":"搜索文本"},
      "glob":{"type":"string","description":"可选，文件名 glob，如 *.py"},
      "max_results":{"type":"integer","default":50}
    },"required":["query"]}}},
  {"type":"function","function":{
    "name":"read_file",
    "description":"读取仓库内某个文件的内容（按行）。只读。",
    "parameters":{"type":"object","properties":{
      "path":{"type":"string","description":"相对路径"},
      "start_line":{"type":"integer","default":1},
      "max_lines":{"type":"integer","default":200}
    },"required":["path"]}}},
  {"type":"function","function":{
    "name":"apply_patch",
    "description":"用 *** Begin Patch / *** End Patch 格式对仓库文件做增量修改。会改文件。",
    "parameters":{"type":"object","properties":{
      "patch":{"type":"string","description":"完整的 patch 文本"}
    },"required":["patch"]}}}
]
```

---

## 6. System Prompt（要点，非最终文案）
- 身份：你是 xhx-agent，运行在本地仓库里的编码 agent。
- 行为：用户**提问就直接用自然语言回答**；需要改代码时**才**调用工具。
- 约束：只用相对路径；所有写操作走 `apply_patch`；证据不足时先 `read_file`/`search` 再改；不要凭空假设没读过的文件。
- 注入：把 `ProjectScan` 的精简项目地图（语言/结构/关键文件）放进 system，给模型基本上下文。

---

## 7. 安全 / 错误处理
- 每个 tool_call 经 `kernel.execute_tool` → `decide_tool` 做 risk 分档；`deny` 直接返回错误内容给模型（不执行），`confirm` 触发现有 `confirm_callback`（`-y`/assume_yes 可免确认）。
- apply_patch 落在 OrchestratorContext 已建好的隔离 worktree，成功才同步回主工作区（复用现有逻辑）。
- 模型返回非法/未知工具名 → 作为 `tool` 错误消息回喂，让模型自我纠正（不直接崩）。
- 工具结果内容**截断到预算**（Phase 1 用简单字节上限，如 `max_file_bytes`）；完整压缩留 Phase 3。
- 网络/HTTP/解析错误 → 复用 `ModelClientError`，写 trace、`status=failed`。

## 8. 离线 mock provider
- `mock` provider 增加确定性的 tool-calling 模拟：对"改代码类"任务返回固定 `tool_call`（read_file→apply_patch）序列后给最终文本；对"问答类"直接返回文本。保证 CI / 无 key 离线可跑通 agent 模式，也用于单测断言循环行为。

## 9. 测试策略
- 单测：循环在"模型回文本"时立即结束并返回 answer；"模型回 tool_calls"时执行并把结果追加后继续；deny/失败工具被作为错误消息回喂。
- 用 mock provider 驱动，不打真实网络。
- 真实联调（手动）：DeepSeek profile 下 `--mode agent` 跑一次对话 + 一次小编辑。

## 10. 开放问题 / 实现前验证项
1. **DeepSeek tool-calling 返回格式**：先发一个最小 `tools` 请求，确认 `tool_calls` 的字段结构（`id`/`function.name`/`function.arguments` 是否为 JSON 字符串）与流式行为。
2. `--mode agent` 需要把 `agent` 加进 console.py / base.py 里的模式校验集合 `{"loop","graph","linear","dag"}`。
3. RunResult 增加 `answer` 字段对现有渲染/JSON 输出的影响（向后兼容，默认 None）。

---

## 11. 后续阶段（概览）
- **Phase 0**：先写《读 Claude Code 源码学到的：统一 agent 循环》经验文档（服务"学习"目标，并作实现图纸）。
- **Phase 2**：只读工具并发、terminal/verify 工具、安全细节对齐。
- **Phase 3**：流式增量、消息历史压缩（对标 microcompact / reactive compact）、repo-intel 作为工具或注入。
- **Phase 4**：README 三范式对比叙事、经验文档收尾、测试覆盖与打磨。
