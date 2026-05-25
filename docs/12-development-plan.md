# xhx-agent 完整开发文档

本文档是 xhx-agent 的实现总纲。它把项目从规划文档推进到可执行开发路线，说明目标形态、技术选型、模块边界、版本顺序和验收标准。

更细的实施级规格见 [实施文档索引](implementation/00-implementation-index.md)。总纲用于判断方向，实施文档用于拆任务、定接口和写测试。

## 1. 项目目标

`xhx-agent` 的目标是实现一个完整的本地编码 Agent。它不是简单的问答工具，而是一个可以在真实代码仓库中完成任务的 Agent Runtime。

最终形态应具备：

- 类似 Claude Code 的终端交互窗口。
- 支持 `/` 命令的 CLI/TUI。
- 能读取、搜索、理解本地代码仓库。
- 能规划任务、生成补丁、执行验证、修复失败。
- 能控制上下文预算，避免 token 爆炸。
- 能保留完整运行轨迹，但只把必要摘要放进模型上下文。
- 能支持多模型 profile、Skill、MCP、Headless、Replay 和评测。

项目主线是：

```text
Context-Budgeted Agent Runtime
= Context Pack Compiler
+ Evidence Runtime
+ Safe Execution Kernel
+ Verification Router
+ Adaptive Planner
+ TUI Command Console
+ Repo Intelligence Graph
+ Skills / MCP
+ Evaluation / Replay
```

## 2. 技术选型

默认技术栈：

- 语言：Python。
- Agent 编排：LangGraph。
- 包管理：uv + `pyproject.toml`。
- 模型协议：OpenAI-compatible。
- 交互入口：CLI / REPL / TUI。
- 写入方式：`apply_patch`。
- 本地状态：`.xhx/`。
- 轨迹格式：JSONL + Markdown。
- 第一批支持项目：Python、JavaScript、TypeScript。

第一版不做：

- Web UI。
- 完整 IDE 插件。
- 完整 LSP。
- 向量 RAG。
- 插件市场。
- 自动 commit。
- 自动 push。
- 自动执行危险命令。

## 3. 目标目录结构

```text
xhx-agent/
  pyproject.toml
  README.md
  XHX.md

  src/
    xhx_agent/
      __init__.py

      cli/
        main.py
        repl.py
        tui.py
        commands.py

      runtime/
        config.py
        profiles.py
        session.py
        state.py
        app.py

      models/
        openai_compatible.py
        mock.py
        types.py

      graph/
        linear.py
        dag.py
        nodes.py

      tools/
        read_file.py
        search.py
        terminal.py
        patch.py
        registry.py

      safety/
        policy.py
        risk.py
        checkpoint.py
        permissions.py
        repair.py

      context/
        compiler.py
        budget.py
        selectors.py
        pack.py

      evidence/
        trace.py
        index.py
        trailgraph.py
        report.py

      verification/
        router.py
        python.py
        node.py
        docs.py
        config.py

      planner/
        modes.py
        classifier.py
        planner.py
        reviewer.py

      repo_intel/
        scanner.py
        xhx_md.py
        repo_map.py
        tree_sitter.py
        symbols.py
        impact.py

      skills/
        loader.py
        metadata.py
        hooks.py
        mcp.py

      evals/
        runner.py
        replay.py
        metrics.py

  tests/
    unit/
    integration/
    e2e/
    fixtures/

  docs/
    00-overview.md
    01-architecture.md
    02-version-roadmap.md
    03-trailgraph.md
    04-tools-and-safety.md
    05-skills-and-extensions.md
    06-testing-and-evaluation.md
    07-reference-agents.md
    08-context-pack-compiler.md
    09-safe-execution-kernel.md
    10-adaptive-planner.md
    11-verification-router.md
    12-development-plan.md
    implementation/
      00-implementation-index.md
      01-module-boundaries.md
      02-runtime-contracts.md
      03-v0.1-implementation-spec.md
      04-version-breakdown.md
      05-testing-fixtures.md
      06-tui-command-console-spec.md
```

## 4. 本地状态目录

每个被管理的目标仓库下创建 `.xhx/`：

```text
.xhx/
  config.json
  profiles.json
  sessions/
  traces/
  evidence/
  logbook/
  checkpoints/
  skills/
```

用途：

- `config.json`：项目级配置。
- `profiles.json`：模型 profile。
- `sessions/`：会话状态。
- `traces/`：Raw Trace JSONL。
- `evidence/`：Evidence Index JSONL。
- `logbook/`：Markdown 审计报告。
- `checkpoints/`：修改前状态摘要。
- `skills/`：本地 Skill。

## 5. 核心模块

### 5.1 CLI / REPL / TUI

CLI 是第一入口，TUI 是最终主要体验。

基础命令：

```bash
xhx init
xhx chat
xhx run "fix failing tests"
xhx run "analyze this repo" --json
xhx config list
xhx config set-profile default
```

TUI 在 v0.5 引入，目标接近 Claude Code 的终端交互体验。

TUI 必须支持：

- 输入框。
- 流式模型输出。
- 工具调用状态。
- 权限确认。
- 当前计划展示。
- 验证状态展示。
- repair loop 状态展示。
- 最终摘要展示。
- `/` 命令系统。

第一批 `/` 命令：

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

### 5.2 Model Adapter

v0.1 只实现 OpenAI-compatible。

配置字段：

```json
{
  "name": "default",
  "base_url": "https://api.example.com/v1",
  "api_key_env": "XHX_API_KEY",
  "model": "qwen-plus",
  "temperature": 0.2
}
```

要求：

- API key 从环境变量读取。
- 支持流式输出。
- 支持 mock model 用于测试。
- 上层 Runtime 不绑定具体模型厂商。

### 5.3 Tool Layer

v0.1 工具：

- `read_file`
- `search`
- `terminal`
- `apply_patch`

要求：

- 工具必须注册到 Tool Registry。
- 工具执行必须经过 Safe Execution Kernel。
- 工具结果必须写入 Evidence Runtime。
- 写入仓库只能通过 `apply_patch`。

禁止：

- terminal 重定向写文件。
- `sed -i`。
- 直接覆盖文件。
- 自动 commit。
- 自动 push。

### 5.4 Safe Execution Kernel

负责工具权限、安全策略和失败停止条件。

执行流程：

```text
tool request
  -> policy check
  -> risk classify
  -> confirm / deny / execute
  -> capture result
  -> write Raw Trace
  -> write Evidence Index
  -> route verification
  -> continue / repair / stop
```

命令风险等级：

```text
safe:
  pwd, ls, dir, rg, cat, type, git status, git diff

confirm:
  pytest, python -m pytest, npm test, npm run build,
  npm run typecheck, local scripts, dependency install

deny:
  rm -rf, git reset --hard, git checkout -- .,
  global install, system config edit, deleting user directories
```

### 5.5 Context Pack Compiler

负责控制每轮进入模型的上下文。

输入：

- 用户当前任务。
- 当前执行模式。
- `XHX.md` 摘要。
- 当前计划。
- changed files。
- 相关代码片段。
- Evidence Index。
- 最近错误摘要。
- 历史会话摘要。

输出：

```text
目标
当前模式
项目规则
当前计划
相关代码片段
关键证据摘要
最近失败点
约束与禁止事项
```

核心原则：

- 完整日志不进 Prompt。
- Raw Trace 默认只落盘。
- Evidence Index 可检索。
- Context Evidence 少量进入 Prompt。
- 超预算时按优先级裁剪。

### 5.6 Evidence Runtime

负责完整审计和证据追踪。

三层结构：

```text
Raw Trace:
  保存完整事件。

Evidence Index:
  保存可检索摘要。

Context Evidence:
  每轮进入 Prompt 的少量证据。
```

TrailGraph 是 Evidence Runtime 的内部表示之一。

节点类型：

```text
TaskNode
EvidenceNode
DecisionNode
```

规则：

- 每个 patch 至少关联一个 evidence id。
- 每个验证结果记录命令、退出码、摘要。
- 用户要求也可以作为 evidence。
- 不保存密钥、完整环境变量和无关绝对路径。
- 低证据置信度不能触发高风险修改。

### 5.7 Verification Router

根据变更类型选择最小验证命令。

Python：

```text
python -m pytest <test-file>
python -m pytest
```

JavaScript / TypeScript：

```text
npm test
npm run typecheck
npm run build
```

文档：

```text
markdown link check
docs build
manual check suggestion
```

配置：

```text
config parse
smoke test
user specified command
```

跳过验证必须记录原因：

- 没有可推断命令。
- 用户拒绝执行。
- 命令风险过高。
- 当前任务只读。
- 依赖缺失。

### 5.8 Adaptive Planner

执行模式：

```text
direct
research-only
linear-edit
plan-review-act
dag-execute
repair-loop
```

选择依据：

- 是否需要读文件。
- 是否需要写文件。
- 是否涉及多个模块。
- 是否涉及公开接口或配置。
- 是否已有失败日志。
- 是否能推断验证命令。
- 用户是否要求只读分析。

规则：

- 简单任务不进入 DAG。
- 复杂任务才使用 DAG。
- 只读任务可以并行。
- 同一文件写入必须串行。
- Reviewer 可以要求补证据、重跑验证、重新计划。

### 5.9 Repo Intelligence Graph

用于增强代码定位能力。

能力：

- repo map。
- Tree-sitter 符号提取。
- symbol search。
- context builder。
- 文件、模块、函数、测试、构建脚本关系。
- 可选 SQLite 索引。
- 影响面分析。

第一批语言：

- Python。
- JavaScript。
- TypeScript。

### 5.10 Skills / Extensions / MCP

Skill 目录：

```text
.xhx/skills/<skill-name>/SKILL.md
```

Skill metadata 示例：

```yaml
name: python-debugger
description: Debug Python test failures and propose minimal patches.
triggers:
  - pytest
  - traceback
  - assertion failed
permissions:
  terminal: confirm
  write: apply_patch
```

规则：

- 默认只加载 metadata。
- 命中 trigger 后加载完整 Skill。
- Skill 不能提升权限。
- Skill 不能绕过 Safe Execution Kernel。
- Skill 不能绕过 `apply_patch`。
- MCP 是可选扩展，必须经过同样的权限策略。

## 6. 版本开发计划

### v0.1 最小 Agent Runtime

目标：完成最小读改测总结闭环。

实现：

- `uv + pyproject.toml`。
- `xhx` CLI 入口。
- `xhx init`。
- `xhx chat`。
- `xhx run "<task>"`。
- OpenAI-compatible model client。
- mock model。
- 基础工具：`read_file`、`search`、`terminal`、`apply_patch`。
- 项目扫描并生成 `XHX.md`。
- Python / JS / TS 验证命令推断。
- 线性 LangGraph。
- Markdown 总结。

验收：

- Python fixture 可读、可改、可 pytest。
- JS/TS fixture 可读、可改、可 npm test/build。
- 所有写入经过 `apply_patch`。
- 每次任务有 Markdown 总结。

### v0.2 Safe Execution Kernel

目标：让执行可控。

实现：

- policy check。
- 命令风险分级。
- 用户确认机制。
- Git checkpoint。
- changed files 追踪。
- 验证失败检测。
- 最多两轮 repair loop。
- 失败停止报告。

验收：

- 危险命令默认拒绝。
- confirm 命令必须确认。
- repair 不会无限循环。
- 报告区分已验证事实和未解决风险。

### v0.3 Context Pack Compiler

目标：控制上下文预算。

实现：

- Context Pack 数据结构。
- token budget。
- top-k evidence selection。
- changed files selection。
- recent failure selection。
- history summary。
- context debug report。

验收：

- 大日志不会完整进入 Prompt。
- 每轮上下文来源可解释。
- 超预算时按优先级裁剪。
- 不同任务类型有不同预算模板。

### v0.4 Evidence Runtime

目标：完整轨迹落盘，摘要进入上下文。

实现：

- Raw Trace JSONL。
- Evidence Index JSONL。
- TrailGraph 内部模型。
- Evidence id 绑定 patch。
- Markdown 审计报告。
- `artifact_ref` 按需展开。

验收：

- 每次工具调用写入 Raw Trace。
- 每次重要观察写入 Evidence Index。
- 每次修改关联证据。
- 报告能解释修改依据。

### v0.5 TUI / Command Console

目标：实现接近 Claude Code 的终端交互体验。

实现：

- 交互式终端窗口。
- 流式输出。
- 工具调用状态展示。
- 权限确认 UI。
- 当前计划展示。
- 验证状态展示。
- repair loop 状态展示。
- `/` 命令系统。
- 会话状态可视化。
- diff / evidence / context 摘要查看。

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

验收：

- 能在 TUI 中完成一次读改测任务。
- 用户能看到工具执行状态。
- 用户能在 TUI 中确认或拒绝命令。
- 用户能用 `/context` 查看当前上下文摘要。
- 用户能用 `/evidence` 查看证据摘要。
- 用户能用 `/diff` 查看本轮变更摘要。

### v0.6 Repo Intelligence Graph

目标：增强大项目代码理解。

实现：

- repo map。
- Tree-sitter parser。
- symbol extraction。
- symbol search。
- context builder。
- optional SQLite index。
- impact analysis。

验收：

- 能按函数、类、文件定位上下文。
- 大项目中优先读取相关片段。
- 变更影响面能辅助 Verification Router。

### v0.7 Adaptive Planner + DAG

目标：支持复杂任务调度。

实现：

- mode classifier。
- DAG planner。
- topological scheduler。
- read-only parallel execution。
- write serialization by file。
- Reviewer quality gate。
- re-plan / re-execute。

验收：

- 简单任务不进入 DAG。
- 只读任务可以并行。
- 同一文件写入不并行。
- Reviewer 能阻止低证据或未验证结果。

### v0.8 Skills / Extensions / MCP

目标：扩展专项能力。

实现：

- Skill loader。
- Skill metadata parser。
- trigger matching。
- progressive disclosure。
- hooks。
- optional MCP client。
- MCP permission registration。

验收：

- Skill 默认只加载摘要。
- 命中后加载正文。
- Skill 不能绕过权限。
- MCP 工具结果写入 Evidence Runtime。

### v0.9 Evaluation / Headless / Replay

目标：支持自动化和评测。

实现：

- `xhx run "<task>" --json`。
- JSONL RPC。
- Trail replay。
- benchmark fixture。
- metrics collection。
- Markdown check report。

验收：

- CI 中能跑只读任务。
- benchmark 能比较模型和策略。
- replay 不重新执行工具也能生成报告。
- 失败任务有失败分类。

### v1.0 完整稳定版

目标：形成可发布项目。

必须具备：

- 稳定 CLI/REPL/TUI。
- 稳定 OpenAI-compatible profile。
- 稳定读改测闭环。
- Context Pack Compiler。
- Evidence Runtime。
- Safe Execution Kernel。
- Verification Router。
- Repo Intelligence Graph。
- Adaptive Planner + DAG。
- Skill / MCP。
- Headless / Replay / Evaluation。
- 完整 README、文档、示例、测试。

验收：

- 从干净 checkout 可安装。
- Python fixture 成功率不低于 80%。
- JS/TS fixture 成功率不低于 70%。
- 修改型任务 100% 生成 Evidence Runtime 记录。
- 失败任务 100% 有明确失败原因。

## 7. 测试计划

### 单元测试

覆盖：

- 配置加载。
- 模型 profile。
- OpenAI-compatible 请求构造。
- mock model。
- 命令风险分类。
- policy check。
- `apply_patch` parser。
- 验证命令推断。
- Context Pack 裁剪。
- Evidence Runtime 序列化。
- Skill metadata 解析。

### 集成测试

覆盖：

- Python 项目读改测。
- JS/TS 项目读改建。
- 验证失败 repair loop。
- 用户拒绝命令。
- Skill 触发。
- session resume。
- Markdown 报告渲染。
- TUI 命令解析。

### 端到端测试

场景：

- 修复 Python 函数 bug。
- 修复 JS build error。
- 根据 README 添加小功能。
- 只分析测试失败，不修改文件。
- 生成 `XHX.md`。
- 大日志不进入 Prompt。
- 在 TUI 中完成一次完整任务。

### 评测指标

记录：

```text
task_success
verification_passed
patch_count
repair_rounds
command_count
context_budget_used
context_items_selected
evidence_coverage
policy_decision_count
verification_route
user_confirmation_count
tokens_in
tokens_out
model_name
```

## 8. 开发顺序

推荐实际执行顺序：

1. 建 Python 包骨架和 `uv` 配置。
2. 做 CLI、config、profile、mock model。
3. 做工具层和 `apply_patch`。
4. 做项目扫描和 `XHX.md`。
5. 做线性 LangGraph。
6. 做验证推断和 Markdown 总结。
7. 补 Safe Execution Kernel。
8. 补 Context Pack Compiler。
9. 补 Evidence Runtime。
10. 做 TUI 和 `/` 命令。
11. 做 Repo Intelligence Graph。
12. 做 Adaptive Planner + DAG。
13. 做 Skills / MCP。
14. 做 Headless / Replay / Evaluation。
15. 做 v1.0 文档、示例和发布整理。

编码前先阅读实施文档：

1. [模块边界](implementation/01-module-boundaries.md)。
2. [运行时契约](implementation/02-runtime-contracts.md)。
3. [v0.1 实施规格](implementation/03-v0.1-implementation-spec.md)。
4. [测试 Fixture 与验收](implementation/05-testing-fixtures.md)。

## 9. 明确反模式

- 不把完整日志塞进 Prompt。
- 不让每个任务默认进入 DAG。
- 不让 Skill 绕过权限。
- 不让 terminal 直接写仓库文件。
- 不自动 commit。
- 不自动 push。
- 不在 v0.1 堆完整 LSP/RAG/MCP。
- 不把 TUI 做成只有外观、没有 Runtime 状态的壳。
- 不把 Evidence Runtime 做成第一版必须依赖的复杂图数据库。

## 10. 默认假设

- 第一版使用 Python + LangGraph。
- 包管理使用 uv。
- 模型调用优先 OpenAI-compatible。
- 默认运行环境支持 Windows PowerShell，同时兼容 Linux/macOS。
- CLI/REPL 是 v0.1 入口。
- 类 Claude Code 的 TUI 从 v0.5 开始做。
- 星穹铁道主题和彩蛋暂不进入核心路线。
- 当前阶段先实现完整 Agent 能力，再考虑品牌化体验。
