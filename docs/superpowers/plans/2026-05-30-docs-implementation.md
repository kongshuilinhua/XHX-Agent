# XHX-Agent 架构白皮书与私密源码深潜教学实施计划 (Docs Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重设计高颜值的公共 `README.md` 并构建 5 大私密级源码深潜教学文档，帮助初学者彻底吃透 XHX-Agent 核心架构及 Python 核心高级库与并发机制。

**Architecture:** 通过重构 `.gitignore` 确保极细颗粒度的教学文档 (`docs/deep_dive/`) 保持本地私密，利用一门式硬核科普板块将 LangGraph, Textual, Pydantic, AST, ThreadPoolExecutor 等底层理论解构，并使用精确的 markdown 时序图与源码路径映射对齐 v1.0.0 的正式开发闭环。

**Tech Stack:** Markdown, Git, Mermaid, Python 101, LangGraph, Textual, tiktoken, Pydantic, AST parsing.

---

## 任务列表 (Tasks)

### 任务 1：升级 `.gitignore` 物理隔离与验证测试

**Files:**
- Modify: `.gitignore`

- [ ] **步骤 1：在 `.gitignore` 末尾安全追加 `docs/deep_dive/` 隔离规则**

修改 `.gitignore` 文件：
```gitignore
# ==========================================
# Private Study & Architecture Deep Dives
# ==========================================
docs/deep_dive/
```

- [ ] **步骤 2：创建私密文件夹并放入测试占位文件**

在终端运行以下命令，创建 `docs/deep_dive/` 目录并写入临时测试文件：
运行：
```powershell
New-Item -ItemType Directory -Force -Path "docs/deep_dive"
New-Item -ItemType File -Force -Path "docs/deep_dive/test.md" -Value "Private test content"
```

- [ ] **步骤 3：运行 `git status` 验证隔离规则是否生效**

运行：`git status`
预期输出：`docs/deep_dive/` 目录以及 `test.md` **不应该**出现在 `Untracked files` 列表中，这证明隔离规则已经完美阻断了 Git 追踪。

- [ ] **步骤 4：提交本次隔离配置变更**

运行：
```powershell
git add .gitignore
git commit -m "chore: gitignore docs/deep_dive directory to secure local-only private study guides"
```

---

### 任务 2：重写公共 `README.md` 面向开发者与用户

**Files:**
- Modify: `README.md`

- [ ] **步骤 1：全量写入崭新的、高度工程化的 `README.md` 文本**

内容覆盖：
- v1.0.0 稳定闭环定位；
- 系统的自适应规划器 + 并行 DAG 数据流 Mermaid 拓扑时序图；
- 5 大技术内核的技术亮点与参数机制；
- 极速上手开发环境搭建指南；
- 命令行参数与 REPL/TUI 终端内置斜杠命令大列表；
- 无损 Trace 重放与基准测试评测导读。

- [ ] **步骤 2：校验外部超链接和排版**

打开 [README.md](file:///d:/pycharmprojects/XHX-Agent/README.md)，确保无排版破裂或错别字，内部引用和符号图解析正确。

- [ ] **步骤 3：运行全量单元测试确保系统未受任何影响**

运行：`uv run pytest`
预期输出：214 passed.

- [ ] **步骤 4：提交 `README.md` 的重构改动**

运行：
```powershell
git add README.md
git commit -m "docs: complete rewrite of README.md into premium public guide"
```

---

### 任务 3：撰写私密导读 01：`01-context-pack-compiler.md` (上下文与 Token 预算)

**Files:**
- Create: `docs/deep_dive/01-context-pack-compiler.md`

- [ ] **步骤 1：编写私密深潜文档 01 的保姆级教学内容**

详细要求包含：
- **硬核科普 101**：全面解释 Token、切词器和 BPE (Byte-Pair Encoding) 算法；通俗解读 OpenAI 的 `tiktoken` 库和 `"cl100k_base"` 机制；图解中文 Token 膨胀的起因，并手把手教学“如何使用 Python 纯码点字符运算（ASCII 为 0.25 tokens，非 ASCII 为 1.5 tokens）实现 99% 精度的优雅退避估算”。
- **源码逐行解构**：对照 `src/xhx_agent/context/compiler.py` 的实际实现，深潜解构 `compile_context_pack` 与 `_estimate_tokens` 方法的参数以及加权排序的优先级装载链。
- **架构时序流程**：用 Mermaid 画出上下文编译的数据装载时序图。

- [ ] **步骤 2：在本地打开文档确认 Mermaid 时序图渲染无语法错误**

打开 [01-context-pack-compiler.md](file:///d:/pycharmprojects/XHX-Agent/docs/deep_dive/01-context-pack-compiler.md)，确认时序图正常，排版完美。

---

### 任务 4：撰写私密导读 02：`02-evidence-runtime.md` (证据流与全局记忆)

**Files:**
- Create: `docs/deep_dive/02-evidence-runtime.md`

- [ ] **步骤 1：编写私密深潜文档 02 的保姆级教学内容**

详细要求包含：
- **硬核科普 101**：零基础普及 Python 的 Pydantic 库（解构 `BaseModel`、参数校验与 `model_dump()` 的底层机制）；剖析为什么 Agent 高频采用 JSONL (JSON Lines) 作为流式落盘与追加写的核心格式；图解图论基础 (Graph Theory)，解释 `TrailGraph` 决策树与审计追踪的联系。
- **源码逐行解构**：深潜解构 `src/xhx_agent/evidence/store.py` 和 `report.py`。详细讲解 Cross-Run 全局记忆合并逻辑下，如何对多个历史 Run 的 `.jsonl` 记录使用 Key `(kind, source, summary)` 去重，并结合时间戳（Freshness）进行热度遗忘与最优装载的排序逻辑。

- [ ] **步骤 2：打开文档确认类与函数调用栈图示正确**

打开 [02-evidence-runtime.md](file:///d:/pycharmprojects/XHX-Agent/docs/deep_dive/02-evidence-runtime.md)，确认超详细的结构体说明无误。

---

### 任务 5：撰写私密导读 03：`03-safe-execution-kernel.md` (安全快照与回滚内核)

**Files:**
- Create: `docs/deep_dive/03-safe-execution-kernel.md`

- [ ] **步骤 1：编写私密深潜文档 03 的保姆级教学内容**

详细要求包含：
- **硬核科普 101**：拆解 Git 底层快照模型，解释暂存区、Working Tree 指针在 `git status` / `git diff` 下的演变；手把手教学 Python 如何使用 `subprocess` 模块安全、防阻塞地调用系统底层 Shell 命令，处理 `stdout`/`stderr` 管道，以及 Windows vs Posix 的平台差异适配；全面解密 Python 上下文管理器 (`with` 语句) 的 `__enter__` 与 `__exit__` 魔法函数在捕获异常并确保 100% 自动安全回滚中的工业实践。
- **源码逐行解构**：深潜剖析 `src/xhx_agent/safety/worktree.py` 和 `checkpoint.py` 中原子 checkpoint 的底层快照和恢复机制。讲解安全策略 (`policy.py`) 对 safe, confirm, deny 指令的判定与用户实时确认交互过滤机制。

- [ ] **步骤 2：本地确认文档链接的跳转性**

打开 [03-safe-execution-kernel.md](file:///d:/pycharmprojects/XHX-Agent/docs/deep_dive/03-safe-execution-kernel.md)，验证无排版纰漏。

---

### 任务 6：撰写私密导读 04：`04-dag-planner.md` (LangGraph 自适应规划与 Kahn 并行调度)

**Files:**
- Create: `docs/deep_dive/04-dag-planner.md`

- [ ] **步骤 1：编写私密深潜文档 04 的保姆级教学内容**

详细要求包含：
- **硬核科普 101**：极速扫盲 LangGraph 框架核心概念（状态图 StateGraph、节点 Node、边 Edges、状态持久化、以及利用 Conditional Edges 驱动 conditional routes 的状态流转机制）；解密多线程与并发控制机制，通俗讲解 Python 的 `ThreadPoolExecutor` 的底层线程分配与生命周期；手把手普及图算法中著名的 **Kahn 拓扑排序算法** 原理，通俗图解它如何解决有向无环图 (DAG) 的依赖排序，如何把“只读节点并行化分配到线程池”而对“写冲突的修改节点强制进行同步串行锁处理”的底层逻辑。
- **源码逐行解构**：深潜追踪 `src/xhx_agent/planner/planner.py` 的意图分类、DAG 生成与 `src/xhx_agent/runtime/dag_runner.py` 的拓扑并行驱动流，并剖析两轮自动修复环 (`verify_loop.py` & `repair.py`) 的自愈机制。

- [ ] **步骤 2：验证 Mermaid DAG 拓扑排序图正常显示**

打开 [04-dag-planner.md](file:///d:/pycharmprojects/XHX-Agent/docs/deep_dive/04-dag-planner.md)，校验所有时序与排序算法伪代码。

---

### 任务 7：撰写私密导读 05：`05-repo-intelligence.md` (仓库智能 SQLite 符号索引)

**Files:**
- Create: `docs/deep_dive/05-repo-intelligence.md`

- [ ] **步骤 1：编写私密深潜文档 05 的保姆级教学内容**

详细要求包含：
- **硬核科普 101**：极速带你走进编译原理的大门，彻底科普什么是抽象语法树 (AST)；演示 Python 标准库 `ast` 模块如何遍历和提取代码中的 class、def 定义与属性关系；详细科普 SQLite 本地嵌入式关系型数据库对百万级符号检索的底层优势，并教学 Python 的 `sqlite3` 的连接、更新、事务控制；深入讲解 JS/TS extensionless 相对导入（寻找 `.js`, `.ts` 或 `index` 后缀）与别名 alias 匹配（如 `tsconfig` 的 paths 通配符解析）的路径猜测机制。
- **源码逐行解构**：剖析 `src/xhx_agent/repo_intel/index.py` 全仓增量刷新机制，详细讲解系统如何根据 changed files 推导出 impacted tests，帮助 Verification Router 自动判定 Targeted pytest 的底层算法逻辑。

- [ ] **步骤 2：在本地阅读确认文档完整无缺**

打开 [05-repo-intelligence.md](file:///d:/pycharmprojects/XHX-Agent/docs/deep_dive/05-repo-intelligence.md)，核对符号数据库表设计细节。

---

### 任务 8：重构优化现有标准文档与引用关联

**Files:**
- Modify: `docs/00-overview.md`
- Modify: `docs/01-architecture.md`
- Modify: `docs/02-version-roadmap.md`

- [ ] **步骤 1：将标准公共文档升格至 v1.0.0 正式状态**

- 修改 [docs/00-overview.md](file:///d:/pycharmprojects/XHX-Agent/docs/00-overview.md) 和 [docs/01-architecture.md](file:///d:/pycharmprojects/XHX-Agent/docs/01-architecture.md)，移除所有 speculative 规划性措辞，将其改为对已实现的 v1.0.0 先进内核与 RPC / Benchmark 工具链的正式说明，并注明本地有私密极详尽导读 `docs/deep_dive/` 可随时学习。
- 修改 [docs/02-version-roadmap.md](file:///d:/pycharmprojects/XHX-Agent/docs/02-version-roadmap.md)，将其重写定位为“XHX-Agent 架构演进与版本迭代历史史实”，说明各个历史里程碑的工程挑战与折中考量，作为你学习其开发脉络的珍贵文献。

- [ ] **步骤 2：执行全套自动化验证流程**

在终端运行：`uv run pytest`
预期输出：214 passed (全部测试在完成文档工程后仍然完美通过)。

- [ ] **步骤 3：提交本次公共文档修正案**

在终端运行：
```powershell
git add docs/00-overview.md docs/01-architecture.md docs/02-version-roadmap.md
git commit -m "docs: align core public documentation with v1.0.0 stable architecture and historical archives"
```
