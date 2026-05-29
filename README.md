# xhx-agent (v1.0.0)

> **Context-Budgeted Local Coding Agent Runtime** - A premium, secure, and state-of-the-art developer assistant designed to operate autonomously and securely in your local repository.

---

## 🚀 核心架构与功能

`xhx-agent` 在 **v1.0.0** 中已完成全部关键能力的稳定开发与深度闭环，是为现代大规模仓储设计的下一代 AI 协作编程运行时：

```
+-----------------------------------------------------------------------+
|                         User / RPC Command                            |
+-----------------------------------------------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                         Intent Classifier                             |
|    Categorizes requests into DIRECT, RESEARCH, LINEAR or DAG modes    |
+-----------------------------------------------------------------------+
        /                           |                           \
       /                            |                            \
      v                             v                             v
+-------------+             +---------------+             +-------------+
| Direct Q&A  |             | Research Only |             | DAG Planner |
| (Help/Info) |             | (Read/Search) |             | (Kahn Sort) |
+-------------+             +---------------+             +-------------+
      |                             |                             |
      |                             v                             v
      |                     +---------------+             +-------------+
      |                     | Context Pack  |             | Parallel    |
      |                     | Compiler      |             | Scheduler   |
      |                     +---------------+             +-------------+
      |                             |                             |
      \                             v                             /
       \--------------------> Tool Sandboxing <------------------/
                                    |
                                    v
                            +---------------+
                            | Safe Kernel   |
                            | checkpoint    |
                            +---------------+
                                    |
                                    v
                            +---------------+
                            | verification  |
                            | (pytest/npm)  |
                            +---------------+
                                    |
                                    v
                            +---------------+
                            | Auto Repair   |
                            | (2 turns max) |
                            +---------------+
                                    v
                            +---------------+
                            | Evidence/Rep  |
                            | (Walkthrough) |
                            +---------------+
```

- **Context Pack Compiler**：在每一轮大语言模型调用前，编译高度精炼、上下文预算受限的信息包。使用高精度的非 ASCII/CJK token 评估算法，仅将最关键的文件、最匹配的符号定义与最近的历史反馈装入上下文，彻底杜绝 token 爆炸。
- **Evidence Runtime**：自动追踪所有工具执行序列。通过 Evidence Index 绑定开发决策、补丁片段和权限断言，不将冗长日志塞进 LLM 运行上下文，保障审计可追溯。
- **Safe Execution Kernel**：安全执行内核管理着所有的外部命令与 patch 应用。具备严密的权限分级（如对 terminal/apply_patch 实行用户交互确认机制），在运行前创建原子状态快照（Checkpoint），若验证失败，则依靠还原计划（Restore Plan）实现 100% 安全回滚。
- **Adaptive Planner + Parallel DAG**：智能分类意图分类。复杂的多文件变更任务自动转换为依赖拓扑图，按拓扑序并行运行无依赖的只读节点（如 search、read_file），对修改节点保持串行，支持依赖故障的阻塞传递。
- **Skills & Hooks 扩展系统**：支持动态扫描本地 Skill 配置（触发器精准匹配）。允许在 `before_plan`、`before_patch`、`after_verify` 和 `before_summary` 挂接扩展回调。支持接入 Model Context Protocol (MCP) 服务进行工具沙箱安全扩充。
- **Headless RPC, Replay & Benchmarking**：支持基于 stdin/stdout 的 JSON-RPC 2.0 交互流（带事件实时通知）；支持从 trace 日志一键回放任务输出（Replay）而不调用模型/运行工具；提供完整的 Benchmark 评测对比套件。

---

## 🛠️ 安装与使用指南

### 1. 快速安装
```bash
# 确保使用 uv 包管理器安装开发环境
uv sync
```

### 2. 初始化项目智能索引
在目标代码仓库的根目录下运行：
```bash
xhx init
```
这将在本地创建 `.xhx/` 目录并自动构建项目代码智能索引 `.xhx/repo/index.json`，同时自动同步至 SQLite 数据库中提供毫秒级符号检索能力。

### 3. 交互式聊天 (CLI / TUI)
打开 Rich 命令控制台或者终端图形界面：
```bash
xhx chat
# 或运行具有丰富仪表盘和事件流显示的 TUI 界面
xhx tui
```
在控制台中支持使用以下斜杠命令：
- `/help`：显示帮助信息。
- `/status`：查看当前运行配置与安全策略状态。
- `/model <profile>`：切换模型配置。
- `/repair <on|off>`：开启或关闭自动修复模式。
- `/live <on|off>`：开启或关闭 TUI 动态事件广播。
- `/diff`：查看当前修改的 git diff 树。
- `/verify`：手动运行关联的自动化测试。
- `/replay <run_id>`：重新回放某次历史执行结果。
- `/exit`：退出当前交互会话。

### 4. 运行单个编码任务
```bash
xhx run "为 xhx_agent.skills 添加一个测试用例" --profile mock --auto-repair
```
- `--profile`：选择加载的模型配置名称。
- `--yes` 或 `-y`：自动同意所有中风险安全验证指令。
- `--auto-repair`：验证失败时自动展开最多 2 轮自主修复循环。
- `--json`：直接以机器可读的结构化 JSON 格式输出最终执行报告与运行统计指标。

### 5. Trail Replay (无损回放)
如果需要重新生成某次运行（例如 `run-1748293`）的 markdown 总结报告而不触发任何真实 LLM 开销和工具改写：
```bash
xhx replay run-1748293
```

### 6. Benchmark (基准评测套件)
在包含的标准任务上测试并对比当前模型配置的效能与时间指标：
```bash
xhx benchmark --profile mock
```

---

## ❓ FAQ & 故障排查

#### Q: 如何清理或重置代码仓储索引？
直接删除项目根目录下的 `.xhx/` 缓存目录并重新运行 `xhx init` 即可完成全新编译。

#### Q: 提示中文字符 Token 溢出如何解决？
`v1.0.0` 支持基于 CJK 字符集 1.5 token/char 的精确非 ASCII 评估算法，已将 Token 精度偏差降至最低，建议在大项目中通过配置调节整体载入限制。

#### Q: 并行 DAG 调度器如何确保读写安全？
并行 DAG 调度器只将只读工具（如 `read_file`，`search`）分配至 `ThreadPoolExecutor` 线程池中并发执行；任何涉及改写的写工具（如 `apply_patch`，`terminal`）都会在当前层级进行同步阻塞串行执行，并由 Safe Execution Kernel 进行强一致性审计。
