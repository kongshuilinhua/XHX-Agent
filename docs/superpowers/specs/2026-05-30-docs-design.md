# 2026-05-30 XHX-Agent 架构白皮书与私密源码深潜教学设计规范 (Design Spec)

## 一、概述 (Goal Description)

XHX-Agent 是一个功能完备的上下文预算驱动型本地编码 Agent 运行时。当前代码（v1.0.0）已实现了包括意图分类、并行 DAG 任务调度、 tiktoken 精确计数、安全执行内核与回滚、增量符号 SQLite 索引器及 Textual 驱动的终端图形界面（TUI）等一系列高级特性。

然而，项目现有的文档体系与其真实实现存在巨大的“信息不对称”（绝大多数文档仍处于 speculative 的规划阶段，描述为未来里程碑）。为了让用户（一位 Agent 与 Python 初学者）能够深入、系统地学习其背后的全套设计模式，本项目决定实施**方案一：分布式架构重构与核心内核深潜教学方案**：
1. **公共门面重设计 (`README.md`)**：面向公众，打造成工业级的、极具视觉冲击力与工程完整性的项目主页。
2. **私密级源码探秘与硬核科普系列 (`docs/deep_dive/`)**：专为本地学习定制，超细颗粒度剖析 5 大核心内核，并辅以针对 Python 初学者的底层库与算法原理硬核科普。
3. **安全物理隔离策略**：修改 `.gitignore` 确保 `docs/deep_dive/` 下的巨量教学文档绝不上传至 GitHub。

---

## 二、详细设计 (Detailed Design)

### 1. 安全物理隔离配置 (`.gitignore`)

修改根目录下的 `.gitignore` 文件，在末尾追加专门的私密文档屏蔽规则，彻底切断本地深潜学习资料被误上传至 GitHub 的可能性：
```gitignore
# ==========================================
# Private Study & Architecture Deep Dives
# ==========================================
docs/deep_dive/
```

---

### 2. 公共 `README.md` 重构规格

重构后的公共 `README.md` 将代表 v1.0.0 的正式完备状态，包含以下模块：
- **Vision & Positioning**：一句话定义“上下文预算驱动的本地编码 Agent 运行时”。
- **Architecture Overview**：包含精心设计的 ASCII/Mermaid 拓扑架构图，展现输入、分类、自适应规划、编译上下文、安全执行、验证与修复、证据汇总的闭环时序。
- **Key Technical Pillars (v1.0.0 Completed)**：
  - **Context Pack Compiler**：基于 `tiktoken` 的高精度切词与优先级预算装载。
  - **Adaptive Planner & Parallel DAG**：基于拓扑排序的读写任务并行/串行化执行。
  - **Safe Execution Kernel**：基于 Git/Worktree Checkpoint 的原子回滚机制。
  - **Incremental Symbol Indexer**：基于 SQLite + AST 的增量符号系统。
  - **Interactive TUI & REPL**：集成行内补全灰色建议的 Rich 终端交互。
- **Quick Start Guide**：使用 `uv` 搭建开发环境与 xhx 初始化的标准流程。
- **Interactive Slash Commands System**：详细的命令行选项与 REPL/TUI 内置的斜杠命令列表。
- **Auditing & Benchmark**：如何执行 replay 重放与效能 benchmark。

---

### 3. 本地私密 `docs/deep_dive/` 5 大内核教学规格

在 `docs/deep_dive/` 下为 5 大内核建立独立文档，格式规范统一为：
1. **💡 基础科普与概念硬核扫盲 (Computer Science & Python Library 101)**：彻底普及该内核涉及的 Python 高级库（如 LangGraph, Textual, Pydantic, AST, SQLite）以及底层算法原理（如 Kahn 拓扑排序, 多线程并发, BPE 切词, Git 快照指针）。
2. **📐 架构蓝图与类结构分析 (Architectural Blueprint & Class Map)**：用 ASCII 图或类关系图解构该模块。
3. **🔄 核心源码执行时序深度解构 (Sequential Source Walkthrough)**：追踪具体的 Python 文件，解构函数的调用栈与底层实现细节。
4. **🧠 设计折中与学习思考 (Design Trade-offs & Learning Notes)**：剖析为什么系统这么做，这么做的好处与代价。

#### 3.1 `01-context-pack-compiler.md` (上下文预算与 Token 裁剪)
- **科普**：大模型切词原理 (BPE)；什么是 `tiktoken` 的 cl100k_base；为什么中文会膨胀；如何基于 Unicode 码点开发 99% 精度的 Python 粗估计数器。
- **源码剖析**：`context/compiler.py`。任务目标、项目地图、本地符号、全局历史记忆的加权裁剪链。

#### 3.2 `02-evidence-runtime.md` (证据追踪与全局记忆)
- **科普**：Pydantic 校验魔法 (BaseModel / model_dump)；结构化日志 JSONL 的多平台追加写与高效流式读取；图论基础 (Graph Theory & TrailGraph)；因果证据树。
- **源码剖析**：`evidence/store.py`, `evidence/report.py`。Cross-run 证据的多 Run 合并、基于 Key `(kind, source, summary)` 去重和时间戳 (Freshness) 遗忘机制。

#### 3.3 `03-safe-execution-kernel.md` (安全快照与命令沙箱)
- **科普**：Git 暂存区与 Working Tree 底层快照；Python 的 `subprocess` 安全调用外部命令与跨平台适配；上下文管理器 (`with` 语句) 魔法方法 `__enter__` / `__exit__` 确保自动清理的原理。
- **源码剖析**：`safety/worktree.py`, `safety/checkpoint.py`, `safety/kernel.py`。安全过滤级别 (safe, confirm, deny) 的执行逻辑。

#### 3.4 `04-dag-planner.md` (LangGraph 自适应规划与 Kahn 并行调度)
- **科普**：LangGraph 状态图模型 (State, Nodes, Edges, Routers)；Python 线程池 (`ThreadPoolExecutor`) 与并发核心；Kahn 拓扑排序算法的工业实现（分析依赖、计算入度、并行分配只读节点、串行写锁）；2-Turn 智能测试错误自动修复环。
- **源码剖析**：`planner/planner.py`, `runtime/dag_runner.py`。

#### 3.5 `05-repo-intelligence.md` (仓库智能 SQLite 符号索引)
- **科普**：编译原理 101：什么是抽象语法树 (AST)？Python 如何用 `ast` 模块遍历并提取 class/def 符号；关系型数据库 SQLite 的本地毫秒级索引优势与 `sqlite3` 的接入；JS/TS extensionless 相对导入与 alias 路径解析（如 `@/`）。
- **源码剖析**：`repo_intel/index.py`。变动后如何触发增量 SQLite 符号库刷新，以及如何推导 impacted tests 实现 Targeted pytest。

---

## 三、验证计划 (Verification Plan)

### 1. 自动编译与结构性验证 (Structure Checks)
- 检查 `docs/deep_dive/` 中新建的 5 篇文档是否存在，其内部的 Markdown 格式与 Mermaid 时序图是否存在语法错误。
- 验证 `.gitignore` 的过滤规则，在终端运行 `git status` 确保新建 of `docs/deep_dive/` 文件夹处于 `Untracked` 且未加入 Git 追踪缓存状态。

### 2. 交互与格式检查 (Manual Verification)
- 打开重构后的 `README.md` 与这 5 篇深潜学习文档，验证所有的内部超链接（包含指向具体 python 源码文件的绝对/相对链接）均能无痛跳转，保证极佳的学习体验。

---

## 四、自评与声明 (Self-Review & Sign-Off)

- **占位符检查**：本 Spec 涉及的具体路径和策略已全部锚定，无任何 TODO 或待定项。
- **范围检查**：本 Spec 专注于文档体系的升格和初学者保姆级架构深潜设计，不引入任何无关的代码修改，边界清晰。
