# Gemini 交接工作流（省 token 版）

> 目的：把实现交给 Gemini，Claude 只出**精简计划 + 关键 check 点**并做**验收**。Claude 的成本压在"审查"（跑测试、看关键 diff），而非"写详尽逐行规格"。

## 角色分工
- **Claude**：① 写精简计划（目标 + 关键 check 点）；② **亲自写核心/易错代码**（见下）；③ 建隔离分支；④ 验收（核对 check 点 + 关键 diff + 全量回归 + 真实联调）→ 合并推送。
- **Gemini**：写**简单代码 + 全部测试 + e2e 接线**（按计划/check 点 TDD），在隔离分支提交、push。
- **用户**：本地跑 Gemini、回传"完成"。

## 核心 vs 简单：谁写什么
省 token 的关键洞察：对**核心/易错逻辑**，写"零猜测详尽计划"≈ 直接写代码，还多一层出错风险——所以**核心由 Claude 直接写**；量大、机械、低风险的部分外包 Gemini。
- **Claude 写（核心）**：编排器控制流、安全/策略判定、解析/归一化等"写错会静默损坏"的逻辑、跨模块契约改动。Claude 把核心写好并自测冒烟后提交到分支。
- **Gemini 写（简单 + 测试）**：工具/配置/wiring 的简单改动、`ToolDefinition` 描述、fixture、**全套单测 + e2e**（含为 Claude 写的核心补足边界用例）。
- 计划里用"**核心已由 Claude 提交（commit X）**"标注，并给 Gemini 关键断言（check 点），让其测试真正钉住预期行为。

## 精简计划格式（模板）
每份计划只含：
1. **Goal**：一句话目标 + 为什么。
2. **先读**：实现前必读的 3–6 个文件（帮 Gemini 定位现状，不替它读）。
3. **边界（不许动）**：明确 don't-touch 清单（如 `loop.py` 行为、某工具契约、安全/kernel 流程）。
4. **关键 check 点（3–6 个，可验证）**：每个是**具体、可跑、可断言**的验收门——如"现有 test_X 全绿"、"新增 Y 格式能成功"、"`loop.py` 零 diff"、"318+ passed / ruff 绿"。怎么实现去满足，Gemini 自己定。
5. **建议方向（可选、轻）**：一两句思路，不展开代码。
6. **纪律 + 明确排除**：TDD、命令前置 `PYTHONUTF8=1`、每步零回归、只在分支提交；列出本切片**不做**什么。

> 核心：计划给"**做到什么**（check 点）"，不给"**逐行怎么做**"。

## 执行流程
1. **Claude**：写精简计划 → 提交 main → push。
2. **Claude**：建隔离分支 `<slice>` ←main，push。
3. **用户**：在该分支跑 Gemini（用下方提示词，填入计划路径与分支名）。
4. **Gemini**：TDD 实现 → 分支提交 → `git push origin <slice>`。
5. **用户**：回传"完成"。
6. **Claude 验收**：`git fetch` 分支 → 逐个核对 check 点 + 看关键文件 diff + `PYTHONUTF8=1 uv run pytest -q` + `ruff` +（涉及真实行为时）DeepSeek 联调 → 过则 ff-merge main + push + 删分支；不过则回**精简**修正点（仍只给 check 点）。

## Gemini 提示词（复用模板）
> 用时把 `<BRANCH>` / `<PLAN_PATH>` 替换掉。
```
你是严格遵循 TDD 的实现工程师，在 git 分支 <BRANCH> 上工作，项目是 Python 包 xhx_agent。

第一步：读 <PLAN_PATH> —— 这是一份精简计划，只给目标 + 关键 check 点，实现细节由你决定。

要求：
- 先读计划里"先读"列出的文件，理解现状再动手；改任何文件前先读它的真实当前内容。
- TDD：为每个 check 点先写测试 → 跑确认红 → 最小实现转绿 → 全量回归 → 提交。
- 严守计划里的"边界（不许动）"与"明确排除"。
- 命令前置 PYTHONUTF8=1。测试 PYTHONUTF8=1 uv run pytest -q；lint PYTHONUTF8=1 uv run ruff check .。
- 绿色基线 = 318 passed, 1 skipped + ruff All checks passed!；每步零回归、ruff 全绿。
  （ruff B023：闭包引用 for 循环变量时用默认参数绑定，如 def f(tc, turn=turn): ...）
- 只在本分支提交（commit message 自拟、清晰），不要 push 到 main，不切别的分支，commit 只 add 相关文件（别带 .idea/ .gemini/ __pycache__ .xhx/）。
- 全部 check 点满足、全量绿后：git push origin <BRANCH>。

完成后报告：新增 commit 的 git log --oneline、最终 pytest 统计行、ruff 结果，以及对每个 check 点"如何满足"的一句话说明。
```

## 历史
- 首例 **3b-1（证据 parity）** 用此流程的"详细版"跑通（Gemini 干净落地、零回归）。本工作流把计划改为"精简版"以省 Claude token。
