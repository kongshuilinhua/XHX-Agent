# 计划（精简）：apply_patch 兼容真实模型的 patch 格式

> 这是一份**精简计划**（Gemini 交接工作流，见 `docs/superpowers/gemini-handoff-workflow.md`）。只给目标 + 关键 check 点，**实现细节由执行模型自行决定**。

## Goal
让 `apply_patch` 工具稳健接受真实 LLM 实际产出的 patch 格式（**标准 unified diff**、被 ` ```diff `/` ```patch ` 围栏包裹的 patch），在**保留**现有 `*** Begin Patch … *** End Patch` 信封的同时，让 `loop`/`plan` 能用真实 DeepSeek **真正改代码**。

**为什么**：真实联调发现真模型每次 `apply_patch` 都因信封不匹配失败、编辑任务空转到顶（背景见仓库根 `ROADMAP.md` 与开发者记忆 `apply-patch-real-model-bug`）。这是 portfolio 主线能力（真能改代码）的潜伏 bug，优先修。

## 先读（理解现状）
- `src/xhx_agent/tools/patch.py` —— 当前严格信封解析（入口 `_parse_patch`）。
- `src/xhx_agent/tools/registry.py` —— `apply_patch` 的 `ToolDefinition` 与 description。
- `tests/test_patch.py` —— 现有信封格式的测试（必须继续全绿）。
- `src/xhx_agent/orchestrators/_toolturn.py` —— patch 失败如何回喂模型（`[apply_patch failed] ...`）。

## 边界（不许动）
- 现有 `*** Begin Patch … *** End Patch` 信封**必须继续可用**（`tests/test_patch.py` 全绿）。
- 不改 `apply_patch` 的工具名 / runner 契约；不改 kernel / 安全策略 / 验证流程；不改 `loop.py` 行为。
- 路径安全校验（patch 不得逃逸 workspace）保持不变。
- 不引新的第三方依赖（除非已在 `pyproject` 依赖里）。

## 关键 check 点（每个都要有测试/可断言）
1. **向后兼容**：`tests/test_patch.py` 现有用例全部仍绿。
2. **unified diff**：标准 unified diff（`--- a/x`、`+++ b/x`、`@@ ... @@` + `-`/`+`/前导空格 行）能成功 apply，**覆盖"改已有文件"与"新增文件"两种**。
3. **围栏剥离**：被 ` ```diff ` 或 ` ```patch ` 或裸 ``` ``` 包裹的 patch 文本能被正确剥离后 apply。
4. **工具描述**：`apply_patch` 的 `ToolDefinition.description` 给出**清晰、可照抄的格式范例**（至少含一个 unified diff 小例），降低模型产出不可解析格式的概率。
5. **端到端**：用 fake chat client 让 `plan`（或 `loop`）发一个 **unified diff 形式**的 `apply_patch`，跑 `run_task` 后 `changed_files` 非空（证明编排器主链路在新格式下能改成）。
6. **全绿**：`PYTHONUTF8=1 uv run pytest -q` 通过（≥318）、`ruff` 全绿、`loop.py` 行为不变。

## 建议方向（轻，不强制）
解析入口先**识别并归一化**：剥围栏 → 判断是"信封"还是"unified diff" → 分派到各自解析器；unified diff 只需支持安全子集（按 `@@` 切 hunk，应用 `-`/`+`/上下文行），上下文/文件找不到时仍回**结构化失败**（不抛裸异常）。**实现前可先复现真模型失败、抓一次它发来的原始 patch 入参**确认实际格式，再动手。

## 纪律 / 明确排除
- TDD（红→绿→回归→提交）；命令前置 `PYTHONUTF8=1`；每步全量零回归 + ruff 绿；只在分支提交，不 push main，commit 只 add 相关文件。
- **不做**：交互式 hunk 合并、模糊行匹配/偏移容错（精确匹配即可）、其他工具改动、`mock.py` 改动。
