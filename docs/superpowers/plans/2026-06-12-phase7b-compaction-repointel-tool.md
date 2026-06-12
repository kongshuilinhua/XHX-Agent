# Phase 7b — 消息历史压缩（microcompact）+ repo-intel 作为工具

> 两件独立的事：① 长 loop 的消息历史压缩；② 把 repo 索引暴露成模型可调用的只读工具。对应 ROADMAP Phase 7 的后两块。
>
> **工作流**：压缩**核心 Claude 已写好并真模型验证**；**压缩接线 + repo-intel 工具 + 全部测试交 Gemini**。

## 已完成（Claude，本分支提交，勿改）
- `orchestrators/compaction.py`：`compact_messages(messages, summarize, *, max_tokens=12000, keep_recent=6)`——估算 token 超阈值时把中间旧历史压成一条摘要消息，否则**原样返回且不调用 summarize**（零额外成本）。**保消息有效性**：前导 system 原样 + 一条 `[Earlier turns compacted...]` 摘要 + 从**非 tool 消息**起的近期尾部（切点落在 tool 上会后移，杜绝孤儿 tool）。
- **真 DeepSeek 验证**：14→6 消息、system 保留、真实摘要、尾首非 tool、无孤儿 tool。

## 边界（不许动）
- 不改 `compaction.py` 的逻辑/签名。
- **零行为变更铁律**：阈值默认 12000，现有 loop 消息都远低于→压缩是 no-op；现有 366 passed 必须保持。

## Gemini 要做

### A) 压缩接线进 `orchestrators/loop.py`
- 进 turn 循环**前**建一个便宜 summarizer（单 client，**不要用 build_routed_client**，因为 FallbackChatClient 没有 summarize）：
  ```python
  from xhx_agent.models.routing import resolve_profile_for_role
  from xhx_agent.orchestrators.compaction import compact_messages
  summarizer = build_chat_client(resolve_profile_for_role(ctx.original_workspace, "summarize", ctx.profile.name))
  summarize_fn = getattr(summarizer, "summarize", None)
  ```
- 每轮 `chat_and_count` **之前**：`if summarize_fn: messages = compact_messages(messages, summarize_fn)`。
- 用 `getattr` 守卫：测试里 FakeClient 没有 summarize → `summarize_fn` 为 None → 跳过压缩 → 现有 loop 测试零影响（且小消息本就 no-op）。可选 emit 一个 `compaction` 事件（turn、before/after 条数）。

### B) repo-intel 作为只读工具（`tools/` + `tools/registry.py`）
- 新增一个只读工具 `repo_query`（`read_only=True`）：参数 `query`(str, required) + `kind`(enum `symbol`|`reference`, 默认 `symbol`) + `limit`(int, 默认 20)。
- runner：`load_repo_intel_index(workspace)` → `kind=="symbol"` 用 `search_symbols(index.symbol_index, query, limit=...)`、`reference` 用 `search_references(index.reference_index, query, limit=...)` → 把结果格式化成 `path:line  name` 之类的文本返回。**镜像现有 `_run_search`/`_run_read_file` 的 runner 契约**（取 workspace 自 ToolContext，返回同形 ToolExecutionResult）；索引缺失/空结果要优雅返回提示而非抛。
- 在 `default_tool_registry()` 注册（`register_definition`）。确认它进 `tool_schemas()` 且 `read_only=True`（自动走只读门控、可并发）。

### C) 全部测试（`tests/test_compaction.py` + `tests/test_repo_query_tool.py` 或并入现有）

## Checkpoints
1. **压缩 no-op**：token 低于阈值时 `compact_messages` 返回原列表且 summarize **从未被调用**（用计数 fake 断言 0 次）。
2. **压缩有效性**：超阈值时——system 原样在前、第二条是 `[Earlier turns compacted...]` 摘要、尾首不是 `tool` 消息；**遍历结果无孤儿 tool**（每个 tool 消息的 `tool_call_id` 都能在它之前的 assistant `tool_calls` 里找到）。
3. **keep_recent ≥ body**：即便超阈值也 no-op（没有可压缩的旧历史）。
4. **loop 接线零回归**：现有 loop mock 测试全绿（小消息→no-op；FakeClient 无 summarize→守卫跳过）；可加一条「人为塞大历史触发压缩、summarizer 用 mock」的测试断言消息条数下降。
5. **repo_query 工具**：注册后出现在 `default_tool_registry().tool_schemas()` 且 `definition("repo_query").read_only is True`；在一个已建索引的 tmp 仓库里 `kind="symbol"` 能查到一个已知符号、`reference` 能查到引用；无索引时优雅提示。
6. **全绿**：`PYTHONUTF8=1 uv run pytest -q`（366→更多 passed）+ `ruff check .` 全绿，零回归。

## 纪律
TDD；命令前置 `PYTHONUTF8=1`；ruff B023 默认参数绑定循环变量；只在分支 `phase7b-compaction-repointel-tool` 提交；全绿后 `git push origin phase7b-compaction-repointel-tool`。报告：新增 commit `git log --oneline`、pytest 统计行、ruff 结果、每个 check 点一句话。
