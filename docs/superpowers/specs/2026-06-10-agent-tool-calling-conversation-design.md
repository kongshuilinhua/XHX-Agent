# 设计：`loop`（ReAct）范式 —— 对话 + 原生 tool-calling 统一循环（Phase 1）

- 日期：2026-06-10（已刷新，对齐最终决策）
- 状态：草案（待用户审阅）
- 上位文档：[ROADMAP](../../../ROADMAP.md)（最终架构、命名、分阶段）
- 借鉴：Claude Code 还原源码 `src/query.ts` 的统一 agent 循环

> 本版已对齐最终决策：**统一 tool-calling 协议**、三范式 **`plan` / `graph` / `loop`**、**声明式工具接口**、以及**命名撞车的落地顺序**（§2.1）。早期版本把本范式叫 `agent`、并误称"最终 3 范式含 agent"，均已更正。

---

## 1. 背景与定位

xhx-agent 当前模型层是**规划器**：每轮无状态重编上下文包，强制模型只返回计划 JSON `{summary,status,steps}`，协议里没有"直接回答"分支 → **不支持对话**。

本 Phase 新建 **`loop`（ReAct tool-use 统一循环）**：补齐对话 + 用 tool-calling 真实干活。它是最终三范式（`plan`/`graph`/`loop`）里的 `loop`。

**核心机制（Claude Code）**：`query.ts` 一个 `while(true)` 生成器循环——assistant 消息里**有 `tool_use` 才继续**（执行工具→结果追加进 messages→再循环）；**只回纯文本就结束本轮，那段文本即回答**。无"聊天 vs 任务"分类器。证据：`query.ts:826-835`（有 tool_use 才 `needsFollowUp=true`）、`query.ts:1062`（`if(!needsFollowUp)` 结束）。

---

## 2. 目标与非目标

### 2.1 命名撞车的落地顺序（开工第 0 步 · 需确认）

新范式名为 `loop`，但**现有 `loop`（plan-execute）占着这名字**。决定：

- **Step 0：先把旧 `loop` 改名为 `plan`（仅改名、行为不变）**
  - `orchestrators/loop.py` → `plan.py`；`LoopOrchestrator` → `PlanOrchestrator`。
  - registry key `"loop"` → `"plan"`（`"loop"` 暂留作 `plan` 的**别名**一两个版本，防外部脚本/会话突然失效）。
  - 更新 CLI/REPL 的模式校验集合 `{"loop","graph","linear","dag"}` → 加 `"plan"`。
  - 同步 docs / 现有测试里的 `loop` 引用。
  - **只动名字**；`plan` 迁 tool-calling 仍在 Phase 3。
- **Step 1+：新建 `orchestrators/loop.py`（新 `LoopOrchestrator` = ReAct）。**
- **DEFAULT_MODE**：开发期保持 `plan`（= 旧默认行为，不破坏现有 demo/CI）；新 `loop` 验证稳定后再把默认切到 `loop`（最佳对话体验）——切换点列入 Phase 1 收尾。
- **为何不用临时名 `agent`**：避免 `--mode agent` 上线后又改名的二次混乱。

### 2.2 Phase 1 目标
1. 新建 `loop` 范式（`--mode loop` / `/mode loop`），与 `plan`(原 loop)/`graph`/`linear`/`dag` 并存。
2. **正常对话**：问问题 → 模型直接回文本 → REPL/CLI 打印回答。
3. **tool-calling 干活**：模型可调 `read_file` / `search` / `apply_patch`，读代码回答或真实改代码。
4. 复用 `SafeExecutionKernel`（risk 三档 + confirm）、worktree 隔离、evidence/trace。
5. 离线可跑：`mock` provider 提供确定性 tool-calling 模拟，CI/无 key 跑通。
6. **声明式工具接口（基础）**：工具 = `name + description + JSON schema + 风险档 + readonly/destructive + runner` 一处声明；schema 自动喂模型，参数校验从 schema 推导（替代散在 `_validate_arguments` 的硬编码）。

### 2.3 非目标（Phase 编号对齐最终 ROADMAP）
- 流式增量渲染（Phase 7）· 消息历史压缩 microcompact（Phase 7）· repo-intel 作为工具（Phase 7）
- 只读工具并发 · `terminal`/`verify` 暴露给模型 · 会话完整历史持久化（均 Phase 2）
- 子 agent / 并行（Phase 5）· 长期记忆（Phase 6）

---

## 3. 架构

新建 `src/xhx_agent/orchestrators/loop.py`，实现 `Orchestrator` 协议，复用 `OrchestratorContext` 已建好的 worktree / evidence / kernel / tool_context / scan。

| 部件 | 位置（建议） | 说明 |
|:--|:--|:--|
| 1. tool-calling 客户端 | `models/openai_compatible.py` 新增 `chat(messages, tools)`（或新文件 `openai_tools.py`） | 发 `/chat/completions` 带 `tools`，解析 `choices[0].message`：返回 `content` 文本或 `tool_calls` |
| 2. 声明式工具接口 | `tools/registry.py` 增 `ToolDefinition`（schema + 风险档 + readonly/destructive + runner）+ `tool_schemas()` 导出 OpenAI function 格式 | 取代硬编码校验；schema 单一来源 |
| 3. 消息历史 | `loop.py` 内部 `messages: list[dict]` | system / user / assistant(含 tool_calls) / tool(tool_call_id) |
| 4. 统一循环 | `loop.py` | 见 §4 |
| 5. 安全集成 | 复用 `kernel.execute_tool` | 每个 tool_call 包成 `ToolStep` 丢进去，risk/confirm/evidence 全复用 |
| 6. 渲染 | `runtime/app.py` 的 `RunResult` 加 `answer: str \| None`；`cli/console.py` 打印 assistant 文本 | 向后兼容，默认 None |

**模型协议适配**：DeepSeek OpenAI 兼容 API 支持 function calling（`tools`/`tool_calls`）——**实现前先发最小请求验证返回字段**（§10）。`ModelProfile` 无需改：`loop` 复用 `provider="openai-compatible"`，走哪个范式由 orchestrator 决定，不由 profile。

---

## 4. 统一循环算法

```
seed:
  messages = [
    {role:"system", content: LOOP_SYSTEM_PROMPT + 精简项目地图(scan)},
    {role:"user",   content: task},
  ]
  changed_files = []

for turn in range(max_turns):                  # 复用 config.max_loop_turns 作硬上限
  if cancelled: return cancelled
  resp = client.chat(messages, tool_schemas)   # 一次模型调用

  if resp.tool_calls:                          # —— 干活分支 ——
    messages.append(assistant_message_with(resp.tool_calls))
    for tc in resp.tool_calls:
      step = ToolStep(tool=tc.name, arguments=tc.arguments)
      result, trace, policy = kernel.execute_tool(ctx.tool_context, step, turn, cb)
      content = render_tool_result(result, policy)   # 拒/失败→错误文本；成功→trace_payload 内容(截断预算)
      messages.append({role:"tool", tool_call_id: tc.id, content})
      if result and result.changed_files: changed_files += result.changed_files
      写 evidence/trace（复用现有）
    continue                                   # 结果回喂，再进一轮
  else:                                        # —— 对话分支 ——
    return RunResult(status="success", answer=resp.content, changed_files=changed_files, mode="loop")

return RunResult(status="failed", risk_summary=[f"loop 在 {max_turns} 轮内未结束"])
```

要点：**纯文本=对话回答即结束**（对应 `!needsFollowUp`）；**tool_calls=执行后追加结果再循环**。与现有 `_run_model_tool_loop` 的本质区别：**消息历史追加式增长**，模型看到真实工具输出（截断后），而非每轮重编的摘要包。

---

## 5. 声明式工具接口 + 三件套 schema

**`ToolDefinition`（建议形状）**：
```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict          # JSON Schema
    runner: ToolRunner        # (ToolContext, args) -> ToolExecutionResult
    read_only: bool = False
    destructive: bool = False
    # 风险档由 read_only/destructive + decide_tool 现有策略推导
```
注册表按 `ToolDefinition` 存；`tool_schemas()` 导出给模型的 `tools` 参数；参数校验用 jsonschema 跑 `parameters`（替代 `_validate_arguments` 硬编码）。

**三件套（OpenAI function 格式，参数从现有校验逻辑推导）**：
```json
[
 {"type":"function","function":{"name":"search","description":"在仓库内按文本搜索，返回匹配文件/行。只读。",
   "parameters":{"type":"object","properties":{
     "query":{"type":"string"},"glob":{"type":"string"},"max_results":{"type":"integer","default":50}},
   "required":["query"]}}},
 {"type":"function","function":{"name":"read_file","description":"按行读取仓库内文件内容。只读。",
   "parameters":{"type":"object","properties":{
     "path":{"type":"string"},"start_line":{"type":"integer","default":1},"max_lines":{"type":"integer","default":200}},
   "required":["path"]}}},
 {"type":"function","function":{"name":"apply_patch","description":"用 *** Begin Patch/*** End Patch 格式对文件做增量修改。会改文件。",
   "parameters":{"type":"object","properties":{"patch":{"type":"string"}},"required":["patch"]}}}
]
```

---

## 6. System Prompt（要点，非最终文案）
- 身份：你是 xhx-agent，运行在本地仓库里的编码 agent。
- 行为：用户**提问就直接用自然语言回答**；**需要改代码时才**调用工具。
- 约束：只用相对路径；写操作走 `apply_patch`；证据不足先 `read_file`/`search` 再改；不臆测没读过的文件。
- 注入：把 `ProjectScan` 精简项目地图（语言/结构/关键文件）放进 system。

## 7. 安全 / 错误处理
- 每个 tool_call 经 `kernel.execute_tool` → `decide_tool` 分档：`deny` 返回错误内容给模型（不执行），`confirm` 触发现有 `confirm_callback`（`-y`/assume_yes 免确认）。
- apply_patch 落在 OrchestratorContext 的隔离 worktree，成功才同步回主工作区（复用现有）。
- 非法/未知工具名 → 作为 `tool` 错误消息回喂，让模型自纠（不崩）。
- 工具结果内容**截断到预算**（Phase 1 用简单字节上限，如 `max_file_bytes`）；完整压缩留 Phase 7。
- 网络/HTTP/解析错误 → 复用 `ModelClientError`，写 trace、`status=failed`。

## 8. 离线 mock provider
`mock` 增加确定性 tool-calling 模拟：对"改代码类"任务返回固定 `tool_call`（read_file→apply_patch）序列后给最终文本；对"问答类"直接返回文本。保证 CI/无 key 离线跑通 `loop`，并用于单测断言循环行为。

## 9. 测试策略
- 单测（mock 驱动，不打真实网络）：① 模型回文本 → 立即结束并返回 `answer`；② 回 tool_calls → 执行、把结果追加后继续；③ deny/失败工具 → 作为错误消息回喂。
- 真实联调（手动）：DeepSeek profile 下 `--mode loop` 跑一次对话 + 一次小编辑。

## 10. 开放问题 / 实现前验证项
1. **DeepSeek tool-calling 返回格式**：先发最小 `tools` 请求，确认 `tool_calls` 字段（`id` / `function.name` / `function.arguments` 是否 JSON 字符串）与流式行为。
2. 模式校验集合更新：`{"loop","graph","linear","dag"}` → 加 `"plan"`（Step 0），`"loop"` 语义改为新范式。
3. `RunResult.answer` 对现有渲染/JSON 输出的影响（向后兼容，默认 None）。
4. Step 0 改名后默认模式切换时机（开发期 `plan`，稳定后 `loop`）。

---

## 11. 与 ROADMAP 的衔接
本 spec = ROADMAP 的 **Phase 1**（tool-calling 基础 + `loop` MVP + 声明式工具接口）。后续：Phase 2（安全对齐 + 受控 terminal + 会话持久化）、Phase 3（`plan` 迁 tool-calling，吸收 `linear`）、Phase 4（`graph` 迁移，吸收 `dag`）、Phase 5 子 agent、Phase 6 记忆、Phase 7 流式+压缩+repo-intel as tool+TUI 重做、Phase 8 benchmark、Phase 9 多模型路由、Phase 10 收尾。
