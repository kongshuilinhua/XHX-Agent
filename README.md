# 🌌 xhx-agent (v1.0.0)

<div align="center">

[![Version](https://img.shields.io/badge/version-v1.0.0-blueviolet?style=for-the-badge&logo=git)](https://github.com/kongshuilinhua/XHX-Agent)
[![Python](https://img.shields.io/badge/python-3.13-blue?style=for-the-badge&logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)
[![CI](https://github.com/kongshuilinhua/XHX-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/kongshuilinhua/XHX-Agent/actions/workflows/ci.yml)
[![TUI](https://img.shields.io/badge/UI-TUI%20%7C%20REPL-orange?style=for-the-badge&logo=gnubash)](src/xhx_agent/tui)

</div>

> **A premium, secure, and state-of-the-art context-budgeted local coding agent runtime** designed to operate autonomously, securely, and with pinpoint precision directly inside your local repositories. 

`xhx-agent` represents the definitive stable **v1.0.0** release, engineering an industrial-grade coding runtime that combines precise context token budgeting, adaptive planning, a sandboxed secure execution kernel, targeted testing, and lossless trail replaying. It is crafted for modern software engineering workflows in large, complex codebases.

---

## 🏛️ System Architecture

`xhx-agent` operates on a structured execution loop that minimizes token pollution while maximizing code intelligence and safety:

### 1. The v1.0.0 Execution Loop
```text
+-------------------------------------------------------------------------------+
|                            User / RPC Input Request                           |
+-------------------------------------------------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                            Intent Classifier Engine                           |
|     Categorizes requests into DIRECT, RESEARCH-ONLY, LINEAR, or DAG modes     |
+-------------------------------------------------------------------------------+
         /                              |                              \
        /                               |                               \
       v                                v                                v
+--------------+                +---------------+                +--------------+
|  Direct Q&A  |                | Research-Only |                |  DAG Planner |
| (Help/Info)  |                | (Read/Search) |                | (Kahn Sort)  |
+--------------+                +---------------+                +--------------+
       |                                |                                |
       |                                v                                v
       |                        +---------------+                +--------------+
       |                        | Context Pack  |                |  Parallel    |
       |                        |   Compiler    |                |  Scheduler   |
       |                        +---------------+                +--------------+
       |                                |                                |
       \                                v                                /
        \---------------------> Tool Sandboxing <-----------------------/
                                        |
                                        v
                                +---------------+
                                |  Safe Kernel  |
                                | (Checkpoints) |
                                +---------------+
                                        |
                                        v
                                +---------------+
                                | Verification  |
                                | (pytest/npm)  |
                                +---------------+
                                        |
                                        v
                                +---------------+
                                |  Auto-Repair  |
                                | (Max 2 turns) |
                                +---------------+
                                        |
                                        v
                                +---------------+
                                |  Evidence/Rep |
                                | (Walkthrough) |
                                +---------------+
```

### 2. High-Fidelity Flow Diagram (Mermaid)

The following diagram illustrates the complete topology of the `xhx-agent` v1.0.0 runtime:

```mermaid
graph TD
    %% Styling and classes
    classDef main fill:#2a2b36,stroke:#8a2be2,stroke-width:2px,color:#fff;
    classDef process fill:#1e1e24,stroke:#4b0082,stroke-width:1px,color:#d8bfd8;
    classDef safe fill:#143d2c,stroke:#2e8b57,stroke-width:2px,color:#e0eee0;
    classDef router fill:#3d141a,stroke:#b22222,stroke-width:2px,color:#fee;

    %% Nodes
    A[/"User / RPC Input Request"/] ::: main
    B["Intent Classifier<br/>(Categorization Engine)"] ::: process
    
    %% Intent branches
    B -->|Direct Mode| C1["Direct Q&A<br/>(Immediate Help / Info)"] ::: process
    B -->|Research Mode| C2["Research-Only<br/>(Autonomous Search & Read)"] ::: process
    B -->|Edit Mode| C3["Adaptive DAG Planner<br/>(Kahn's Topological Scheduler)"] ::: process

    %% Context Pack Compilation
    C2 --> D["Context Pack Compiler<br/>(cl100k_base precise token budget)"] ::: process
    C3 --> E["Parallel / Serial Executor<br/>(Reads concurrent | Writes locked)"] ::: process
    E --> D

    %% Safe execution
    D --> F["Safe Execution Kernel<br/>(Permission Check & Sandbox)"] ::: safe
    F --> G["Worktree / Git Checkpoint<br/>(Atomic Rollback Plan)"] ::: safe
    G --> H["Tool Execution Engine<br/>(apply_patch / terminal / custom skills)"] ::: safe

    %% Verification & Auto-Repair Loop
    H --> I["Verification Router<br/>(Targeted pytest / npm scripts)"] ::: router
    I -->|Passed| J["Evidence Runtime<br/>(Trace Logbook & GFM Reports)"] ::: main
    I -->|Failed| K["Auto-Repair Loop<br/>(Self-Correction - Max 2 turns)"] ::: router
    K -->|Retry| D
    K -->|Exceeded / Aborted| L["Discard Worktree (git) /<br/>Restore Plan (in-place)"] ::: safe
    L --> J

    C1 --> J
```

---

## ⚡ Core "Black-Tech" Highlights

### 🧠 1. Context Pack Compiler
To prevent context inflation and token-drift, `xhx-agent` compiles a highly concentrated **Context Pack** prior to every LLM turn.
* **Precise Tokenization**: Implements precise `cl100k_base` tiktoken-based count validation.
* **Priority Budgeting**: Allocates context tokens deterministically (e.g., 20% Project Maps, 25% Task Specs, 35% Source Code Snippets, 15% Evidence, 5% Error logs). When budgets overflow, lower-priority context is dynamically pruned.
* **Double-Speed Estimator**: Features a robust fallback algorithm for complex locales with a standard ratio of **0.25 tokens/char for ASCII** and **1.5 tokens/char for Non-ASCII/CJK**.
* **History Compaction**: In long autonomous loops, tool summaries that overflow the recent window are compacted into a single line instead of being dropped — a heuristic tally (tool counts + failures) by default, or a semantic LLM summary (via the active profile) in autonomous mode, with automatic fallback to the heuristic on error.

### 📅 2. Adaptive Planner + Parallel DAG
Routes each request to an execution mode based on its intent and complexity.
* **Intent Routing**: Classifies inputs into `direct`, `research-only`, `linear-edit`, `plan-review-act`, or `dag-execute`.
* **Kahn Parallel DAG**: The `dag-execute` scheduler builds a topological graph using Kahn's algorithm with deterministic ordering and cycle detection. *Note (v1.0):* the DAG node generator is currently a heuristic baseline; LLM-driven DAG decomposition of arbitrary requests is on the roadmap. Open-ended edit tasks are best run in `linear-edit` mode.
* **Concurrent Execution**: Spawns multiple threads for concurrent execution of read-only dependencies (e.g., searches, file readings), while enforcing a strict **serialization lock** on edit tasks targeting the same file paths.
* **Pluggable Orchestrator**: A unified `Orchestrator` abstraction drives a task with either a single autonomous **`loop`** (Claude-Code-style) or a multi-agent **`graph`**, selectable via the `run_task(mode=...)` API over one shared tool/safety/context base. The **`loop`** mode is **implemented and autonomous** — the model keeps iterating (read → edit → verify) across up to `max_loop_turns` (default 20) until it reports done, instead of stopping after the first change; read-only steps within a turn run concurrently (subagent-style), and long histories are compacted. The auto-classification fallback still uses `linear` (stop-on-first-change) for backward compatibility. The **`graph`** mode is implemented as a LangGraph `StateGraph` (coordinator → execute → review, with a conditional re-execute loop) — the optional multi-agent paradigm, kept lean for contrast. *Roadmap:* `--mode` CLI/TUI wiring (M4). See `docs/implementation/20-implementation-baseline.md`.

### 🛡️ 3. Safe Execution Kernel
Provides comprehensive system isolation and state-recovery guarantees.
* **Multi-Tier Authorization**: Classifies commands into `safe` / `confirm` / `deny` risk tiers (allowlist-driven, with denylisted executables, shell-metacharacter blocking, and inline-interpreter detection as defense-in-depth). Terminal `confirm` commands prompt for approval in the REPL or honor pre-approval (`-y`/`--yes`); `deny` commands are always blocked. Structured `apply_patch` writes are auto-applied within worktree isolation (they do not prompt per-patch).
* **Worktree Isolation**: When run inside a git repository, edits execute in an isolated git worktree; changes are synced back only on success and the worktree is discarded on failure — an effective baseline rollback.
* **Restore Plan**: Outside a git repository (or if worktree creation fails), changes apply in place and the runtime emits an explicit warning plus a read-only Restore Plan that records what changed for manual recovery. There is no automatic in-place revert.
* **2-Turn Auto-Repair**: Upon targeted test failures, the runtime initiates up to 2 self-correction cycles, feeding compiler test failures and evidence traces directly back into the planner.

### 💻 4. Smart Console & Full-Screen TUI
Crafted for an exceptional local development experience.
* **REPL Console**: Powered by `prompt-toolkit` with full multi-line input support, immediate syntax highlighting, command history, and a dynamic **autocompletion drop-down**.
* **Dashboard TUI**: Powered by `Textual` providing a beautiful, state-of-the-art terminal dashboard with real-time token trackers, live event broadcasts, inline **grey suggestion autocompleters**, and a split-screen git diff inspector.

### 🔌 5. Skills & MCP Plugin System
Extensible hooks that dynamically augment the agent's actions.
* **Skill Auto-Discovery**: Automatically scans `.xhx/skills/` and matches active tasks with specialized local instructions using YAML trigger-headers.
* **Custom Hooks**: Exposes lifecycle callbacks (`before_plan`, `before_patch`, `after_verify`, `before_summary`) for seamless custom developer extensions.
* **Model Context Protocol (MCP)**: Directly integrates with MCP Servers to safely import standard MCP tools.

### 📊 6. JSON-RPC 2.0 & Benchmarking
Designed for CI/CD pipelines, evaluation runners, and external editors.
* **Headless JSON-RPC 2.0**: Exposes a stateless stdio-based RPC interface with asynchronous event notifications for IDE plugin integration (VS Code, JetBrains).
* **Trace Replay**: Generates complete trace summaries from past executions offline, bypassing LLM API costs and avoiding tool side-effects.
* **Benchmark Comparison Suite**: Executes standardized fixture benchmarks in isolated sandboxes to rate model accuracy, token consumption, execution times, and self-repair rates.

---

## 🛠️ Quick Start

### 1. Environment Setup
Install dependencies and configure the workspace using the `uv` package manager:
```bash
# Clone the repository and install virtual environment
git clone https://github.com/kongshuilinhua/XHX-Agent.git
cd XHX-Agent
uv sync
```

### 2. Initialize Repo Index
Build the symbols and import-graph database inside your target codebase:
```bash
# Initialize .xhx workspace index
uv run xhx init
```
This initializes the `.xhx/` metadata store, generates your local `XHX.md` project map, and builds an incremental SQLite database mapping symbols, imports, and cross-references.

---

## 🎮 Interactive Console & Command Guide

Run the interactive REPL shell:
```bash
uv run xhx chat
```
Or open the full-screen terminal dashboard:
```bash
uv run xhx tui --fullscreen
```

### 1. Console Slash Commands
Inside the REPL, type `/` to trigger the interactive completion menu. `xhx-agent` supports 12+ primary slash commands:

| Command | Syntax | Description |
|:---|:---|:---|
| 📋 **`/help`** | `/help` | Displays this command guide with descriptions of all REPL slash commands. |
| 🤖 **`/model`** | `/model [profile_name]` | Lists all configured LLM profiles or switches the active model configuration. |
| 📊 **`/status`** | `/status` | Renders a rich summary panel of the active console session and last run metrics. |
| 🔍 **`/plan`** | `/plan <task>` | Generates a dry-run execution plan and estimates token usage without editing files. |
| 📜 **`/evidence`**| `/evidence` | Displays the security policy decisions and lists files inside `.xhx/evidence/`. |
| 🎯 **`/context`** | `/context` | Shows the active context selection and token budget usage statistics. |
| 🧪 **`/verify`** | `/verify` | Manually triggers the verification router to execute tests matching edited files. |
| 🔧 **`/repair`** | `/repair [on\|off\|run]` | Toggles automatic repair loops, or triggers a manual repair cycle. |
| 🌳 **`/diff`** | `/diff` | Displays the git status and shows a colorized read-only diff of uncommitted changes. |
| 💡 **`/skills`** | `/skills` | Scans and lists all active and discovered Skills inside `.xhx/skills/`. |
| 🧹 **`/clear`** | `/clear` | Clears the terminal output buffer and redraws the interactive console panel. |
| 🚪 **`/exit`** | `/exit` | Securely closes the interactive console session and gracefully shuts down. |

### 2. CLI Command Flags
Execute individual tasks headlessly from your terminal:
```bash
uv run xhx run "Fix the typo in main.py and run pytest" --profile mock --auto-repair
```

* `--profile <name>`: Load a specific LLM profile defined in `.xhx/profiles.json`.
* `--auto-repair`: Enable up to 2 rounds of self-repair loops if the verification route fails.
* `--json`: Outputs the final run results as a machine-readable structured JSON.
* `-y`, `--yes`: Automatically bypass confirm-level prompts for medium-risk verification commands.
* `--dry-run`: Previews the initial execution plan, budget analysis, and risks, then exits.
* `--continue`: Resumes from the most recent session, injecting its summary (run id, status, changed files) as context for the new task. Each run is recorded to `.xhx/sessions/history.jsonl`.
* `--resume <run-id>`: Resumes from a specific past session by run id. Use `xhx sessions` to list recorded sessions.

---

## 📈 Benchmarks & Trail Replay

### 1. Run Benchmark Suite
Run isolated benchmark fixtures to evaluate model configurations:
```bash
uv run xhx benchmark --profile mock
```
Outputs a high-fidelity terminal table:
```text
┌────────────────────────────────────── xhx-agent Benchmark Results (mock) ──────────────────────────────────────┐
│ Fixture ID  Name                       Status  Turns   Duration  Est. Tokens  Success                          │
├────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ fix_bug_1   Resolve ZeroDivisionError  success     2      0.82s          420     ✅                            │
│ test_gen    Create CLI unit tests      success     1      0.45s          310     ✅                            │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2. Lossless Trace Replay
To re-evaluate or inspect a previous execution run without calling the LLM API or modifying repository files:
```bash
uv run xhx replay <run_id>
```

---

## 📘 Private Educational Deep-Dives

If you are using `xhx-agent` for deep academic research or private learning, we maintain a complete series of comprehensive **Architecture Deep-Dives** within the local codebase:
* File Location: `docs/deep_dive/` (e.g., `docs/deep_dive/01-compiler-internals.md`, `docs/deep_dive/02-dag-theory.md`).
* **Note**: As these files contain proprietary learning notes and local execution keys, the directory `docs/deep_dive/` is strictly excluded from public Git version control in `.gitignore`.

---

<div align="center">
Designed and built with passion by the xhx-agent author (<a href="https://github.com/kongshuilinhua/XHX-Agent">kongshuilinhua</a>).
</div>
