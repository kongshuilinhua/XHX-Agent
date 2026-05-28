# 版本路线图

路线图按增量方式推进。每个版本都要产出可用系统，不要在前一个闭环稳定前提前堆基础设施。

## v0.1 最小 Agent Runtime

目标：完成最小读改测总结闭环。

v0.1 按三个固定子阶段推进：

- **v0.1-A 真实模型接入**：真实模型能返回结构化工具计划，mock 只作为测试线保留。
- **v0.1-B Tool-call loop**：Runtime 能执行“模型 -> 工具调用 -> 工具结果 -> 再调用模型”的最小循环。
- **v0.1-C 验证闭环**：完成“读 -> 改 -> 测 -> 报告”的真实 v0.1 闭环。

子阶段名称、当前完成度和路线变更规则以 [版本实施基线](implementation/07-implementation-baseline.md) 为准。

必须实现：

- CLI/REPL。
- 多模型 profile。
- OpenAI-compatible 的 `base_url`、`api_key`、`model` 配置。
- 工具：`read_file`、`search`、`terminal`、`apply_patch`。
- 项目扫描并生成 `XHX.md`。
- Python 和 JavaScript/TypeScript 验证命令推断。
- 每次任务结束后生成 Markdown 总结。

验收标准：

- 小型 Python 项目可以被检查、打补丁并用 pytest 验证。
- 小型 JavaScript/TypeScript 项目可以被检查、打补丁并用 `npm test` 或 `npm run build` 验证。
- 所有仓库写入都经过 `apply_patch`。
- 每次任务总结列出变更文件、执行命令、验证结果和风险。

## v0.2 Safe Execution Kernel

目标：让工具执行、代码修改和验证流程可控、可恢复。

必须实现：

- 命令风险等级：safe、confirm、deny。
- 工具调用统一经过 policy check。
- 修改前 Git checkpoint。
- changed files 追踪。
- 验证失败检测。
- 最多两轮 repair loop。
- 失败时停止并输出明确失败报告。

验收标准：

- 删除文件、Git reset、全局安装、系统配置修改不会自动执行。
- 验证失败时能生成修复计划。
- 修复失败时保留证据并停止，不继续猜测。
- 最终报告区分已验证事实和未解决风险。

## v0.3 Context Pack Compiler

目标：控制上下文质量和大小，避免 token 爆炸和历史污染。

必须实现：

- 固定 token budget。
- Context Pack 结构。
- 项目地图与规则注入。
- 当前任务计划注入。
- 相关代码片段选择。
- 最近失败点选择。
- Evidence top-k 摘要选择。
- 历史压缩摘要。

验收标准：

- 完整日志不会直接进入 Prompt。
- 每轮 Prompt 上下文来源可解释。
- 超过预算时按优先级裁剪。
- 调试、重构、文档任务可以使用不同上下文配比。

## v0.4 Evidence Runtime

目标：保留完整审计轨迹，同时只把少量证据摘要交给模型。

必须实现：

- Raw Trace。
- Evidence Index。
- Context Evidence。
- TrailGraph 内部表示。
- 原始证据按需展开。
- Markdown 审计报告。

验收标准：

- 每次工具调用都能写入 Raw Trace。
- 每次重要观察都有 Evidence Index 摘要。
- 每次代码修改至少关联一个证据。
- LLM 上下文只接收摘要或被请求的片段。

## v0.5 TUI / Command Console

目标：实现接近 Claude Code 的终端交互体验，让 Runtime 状态可以被用户直接观察和控制。

必须实现：

- 交互式终端窗口。
- 流式模型输出。
- 工具调用状态展示。
- 权限确认 UI。
- 当前计划展示。
- 验证状态展示。
- repair loop 状态展示。
- `/` 命令系统。
- 会话状态可视化。
- diff / evidence / context 摘要查看。

验收标准：

- 能在 TUI 中完成一次读改测任务。
- 用户能看到工具执行状态。
- 用户能在 TUI 中确认或拒绝命令。
- 用户能用 `/context` 查看当前上下文摘要。
- 用户能用 `/evidence` 查看证据摘要。
- 用户能用 `/diff` 查看本轮变更摘要。

基础 `/` 命令：

```text
/help
/model
/status
/plan
/evidence
/context
/verify
/repair
/diff
/skills
/mode
/clear
/exit
```

## v0.6 Repo Intelligence Graph

目标：增强仓库理解能力，但不强行一次性实现完整 IDE 栈。

当前实现：

- 基础 repo map。
- Python AST 符号提取。
- JavaScript / TypeScript 轻量符号提取。
- symbol search。
- symbol context builder。
- Context Pack 任务关键词驱动的 symbol context 注入。
- Python / JavaScript / TypeScript source -> direct test 的基础 impact mapping。
- 轻量 import graph 辅助 impact mapping，并支持有限深度的反向递归依赖测试映射。
- `.xhx/repo/index.json` 结构化仓库索引，当前包含 repo map、symbol index 和 import graph。
- Context Pack 和 Verification Router 优先复用 `.xhx/repo/index.json`，缺失、损坏或文件指纹过期时再即时构建。
- Context Pack 可以从 changed files 和 recent error 中的文件路径出发，选择 import graph 邻接文件里的少量符号上下文。
- 成功 `apply_patch` 后刷新 `.xhx/repo/index.json`，再推断验证命令。
- `XHX.md` Repo Map / Symbols 摘要。
- Verification Router targeted pytest 接入。
- Verification Router 可在识别到 `vitest`、`jest` 或 `node --test` 时，为 JS/TS impacted tests 生成 targeted `npm test -- <test-file>`；未知 test script 仍回退到 `npm test`。

仍未完成：

- Tree-sitter。
- SQLite 索引；当前只有 JSON 格式的 `.xhx/repo/index.json`。
- 完整调用图和引用图。
- 真正增量更新索引；当前过期时会重建整个 JSON 索引。
- 更完整的 test runner 参数推断、跨语言影响面分析和更强的大项目 context 选择；当前 import context 只是直接邻接符号补充，不是调用图、引用图或语义检索。

必须实现：

- repo map。
- Python、JavaScript、TypeScript 的 Tree-sitter 符号提取。
- symbol search。
- 围绕符号的 context builder。
- 文件、模块、函数、测试、构建命令之间的关系。
- 可选 SQLite 索引。

验收标准：

- Agent 能按函数、类、文件定位上下文。
- 大项目中优先读取相关上下文，而不是盲目读文件。
- 变更影响面可以辅助 Verification Router 选择命令。

## v0.7 Adaptive Planner + DAG

目标：复杂任务接近 HPD-Agent 的处理能力，简单任务保持低成本。

必须实现：

- 执行模式选择：direct、research-only、linear-edit、plan-review-act、dag-execute、repair-loop。
- DAG planner。
- 拓扑调度器。
- 并行只读任务执行。
- 同一目标文件写入串行化。
- Reviewer quality gate。
- re-plan 和 re-execute 分支。

验收标准：

- 简单任务不进入 DAG。
- 互不依赖的分析任务可以并行执行。
- 写入同一文件的任务永不并行。
- Reviewer 可以要求补充证据或重新运行验证。

## v0.8 Skills / Extensions / MCP

目标：在不膨胀核心 Prompt 的前提下扩展能力。

必须实现：

- 本地 Skill 目录：`.xhx/skills/<name>/SKILL.md`。
- Skill metadata，包含触发词和权限预期。
- Progressive disclosure：只有需要时加载完整 Skill 内容。
- Extension hooks：`before_plan`、`before_patch`、`after_verify`、`before_summary`。
- 可选 MCP client 集成。

验收标准：

- 可以列出 Skill 描述，而不加载完整正文。
- 命中的 Skill 可以影响计划和上下文选择，但不能绕过权限。
- Extension 不能绕过授权工具写文件。

## v0.9 Evaluation / Headless / Replay

目标：支持自动化、回放和模型对比。

必须实现：

- `xhx run "<task>" --json`。
- JSONL RPC 模式。
- Trail replay。
- benchmark 任务集。
- Markdown check 报告。
- 单次运行指标记录。

验收标准：

- 只读分析任务可以在 CI 中运行。
- 每次运行返回结构化结果。
- benchmark 可以对比不同模型或策略 profile。
- 回放 Trail 时可以复现任务总结，不重新运行工具。

## v1.0 完整 Agent 项目

目标：形成完整、稳定的编码 Agent 项目。

必须具备：

- 稳定 CLI/REPL。
- 稳定模型 profile 管理。
- 可靠读改测总结闭环。
- Context Pack Compiler。
- Evidence Runtime。
- Safe Execution Kernel。
- TUI / Command Console。
- Repo Intelligence Graph。
- Adaptive Planner + DAG。
- 本地 Skill 系统。
- 文档、示例、测试和安装指南。

验收标准：

- 项目可以从干净 checkout 安装。
- 示例 Python 和 JavaScript/TypeScript 任务通过。
- 每次修改型运行都产出 Evidence Runtime 记录。
- 已知风险和不支持行为都有文档说明。
