# xhx-agent

<div align="center">

[![Version](https://img.shields.io/badge/version-1.0.0-blueviolet?style=flat-square)](https://github.com/kongshuilinhua/XHX-Agent)
[![Python](https://img.shields.io/badge/python-3.13-blue?style=flat-square&logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![CI](https://github.com/kongshuilinhua/XHX-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/kongshuilinhua/XHX-Agent/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-85%25-brightgreen?style=flat-square)](https://github.com/kongshuilinhua/XHX-Agent/actions/workflows/ci.yml)

**English** · [简体中文](README.zh-CN.md)

</div>

> A **context-budgeted local coding agent runtime** with a **pluggable, tri-paradigm orchestrator**: run the same task as a single autonomous **`loop`** (ReAct, Claude-Code-style), as a batch-planned **`plan`** (Plan-Execute), or as a multi-agent **`graph`** (LangGraph) — all three speaking the **same native tool-calling protocol** over one shared safety / context / code-intelligence base.

`xhx-agent` operates directly inside a local repository. It compiles a token-budgeted context pack before every model turn, classifies and gates shell commands through a safe execution kernel, edits inside an isolated git worktree, runs targeted tests, and records a replayable evidence trail. The same task can be driven by three interchangeable control-flow paradigms, selectable at runtime — so the loop-vs-plan-vs-graph design trade-off is concrete, comparable, and **benchmarked with real numbers**.

---

## Why this project is interesting

- **One protocol, three paradigms.** A single `Orchestrator` abstraction with three real implementations that all drive the model through **native tool-calling** (no bespoke "model plan" DSL): an autonomous **`loop`** (read → edit → verify, iterating until done), a **`plan`** (batch-plan → execute → verify with bounded self-repair), and a **`graph`** built on a LangGraph `StateGraph` (coordinator → worker → reviewer, with a conditional retry loop). They share the exact same tool, safety, context, and code-intelligence layers — only the top-level control flow differs. All three are **verified end-to-end against a real model** (DeepSeek), not just the offline mock.
- **Quantified, not hand-waved.** A built-in [benchmark harness](#benchmark-quantifying-the-paradigms) runs a fixture task-set across all three paradigms and emits a comparison report (turns / tokens / wall-clock / success / files changed) as Markdown + JSON. The token meter makes the multi-agent overhead a number: on a real model (DeepSeek), `graph` spends **~4× the tokens** of single-agent `loop`/`plan` for the same work — and isn't automatically more reliable.
- **Cross-session memory — the fourth axis of context management.** Beyond per-turn budgeting, in-loop history compaction, and sub-agent delegation, `xhx-agent` keeps a `.xhx/memory/` of durable facts (`user` / `feedback` / `project` / `reference`). Recall is **deterministic** (keyword/token overlap on each fact's description — no extra LLM call) and injected into the system prompt under the token budget; a freshness check skips memories that name files no longer on disk. Writes are explicit (`/remember`) or **suggest-confirm** (the agent proposes after a run, you approve with one keypress). Verified end-to-end: a fact that exists *only* in memory is recalled and used by the real model.
- **Multi-model routing + graceful fallback.** A run can route different roles to different profiles — cheap models for exploration/summarization, a strong model for edits — and **falls back down a profile chain** when the primary errors or rate-limits (à la Claude Code's `fallbackModel`). Routing and streaming are orthogonal: the fallback wrapper forwards the streaming callback to whichever client serves.
- **Streaming, with the budget intact.** The tool-calling loop streams the model's output **token-by-token** to a thin live status line (reassembling fragmented `tool_calls` as they arrive over SSE), while long histories are kept in budget by **microcompact** — summarizing the older middle of the conversation into one note *without ever orphaning a tool result from its call*.
- **Token-budgeted Context Pack.** Each model turn is fed a deterministically-budgeted context pack (project map / task / source / evidence / errors), measured with `tiktoken` (`cl100k_base`) and pruned by priority when it overflows. Long autonomous histories are compacted rather than dropped.
- **Safe Execution Kernel.** Shell commands are tokenized (`shlex`) and classified into `safe` / `confirm` / `deny` tiers, with a denylisted-executable set, shell-metacharacter blocking, and inline-interpreter detection as defense-in-depth. Edits run in an isolated git worktree and are synced back only on success.
- **Repo intelligence.** A symbol / import / reference / call index built from Python's `ast` and tree-sitter (for JS/TS), persisted as JSON with a SQLite mirror, refreshed incrementally on file changes.
- **Honest implementation status.** The [implementation status](#implementation-status) section states plainly what is fully implemented vs. simplified — and the [engineering notes](#engineering-notes-what-building-this-taught-me) record what broke against a real model and what a prompt alone could *not* fix.

---

## Architecture

```mermaid
graph TD
    classDef entry fill:#1e2330,stroke:#5b8def,stroke-width:1px,color:#dce6ff;
    classDef orch fill:#2a2433,stroke:#a06bd6,stroke-width:1px,color:#ede0fb;
    classDef base fill:#14302a,stroke:#2e8b57,stroke-width:1px,color:#e0eee0;

    E["Entry points<br/>CLI run · REPL · TUI · JSON-RPC"]:::entry
    S["Orchestrator selection<br/>--mode loop / plan / graph<br/>(default: loop · linear/dag = legacy, explicit only)"]:::orch
    L["loop paradigm<br/>single autonomous agent (ReAct)<br/>read → edit → verify, until done"]:::orch
    P["plan paradigm<br/>batch plan → execute → verify<br/>bounded self-repair (≤2 rounds)"]:::orch
    G["graph paradigm (LangGraph)<br/>coordinator → worker → reviewer<br/>conditional retry loop"]:::orch

    subgraph base_box["Shared base — native tool-calling"]
        M["Long-term Memory<br/>(.xhx/memory · deterministic recall)"]:::base
        B["Context Pack Compiler<br/>(tiktoken budget + compaction)"]:::base
        R["Repo Intelligence<br/>(ast + tree-sitter, JSON + SQLite)"]:::base
        K["Safe Execution Kernel<br/>(risk tiers · worktree isolation)"]:::base
        V["Verification + bounded Auto-Repair<br/>(targeted pytest, max 2 rounds)"]:::base
        EV["Evidence Trail<br/>(replayable traces + reports)"]:::base
        M --> B --> R --> K --> V --> EV
    end

    E --> S
    S -->|loop| L
    S -->|plan| P
    S -->|graph| G
    L --> M
    P --> M
    G --> M
```

All three paradigms issue the same tool calls (`search`, `read_file`, `apply_patch`, `repo_query`, `verify`, `terminal`, `dispatch`, …) through the same kernel — the difference is purely *who decides what to call next*: one agent (`loop`), a plan-then-execute controller (`plan`), or a coordinator/worker/reviewer team (`graph`). Orthogonal to the paradigm, each run **routes roles to model profiles with a fallback chain**, **streams** output token-by-token, and keeps long histories in budget via **microcompact**.

---

## Quick Start

`xhx-agent` ships with a built-in **`mock`** profile, so the full pipeline runs **offline with no API key** — ideal for trying it out, CI, and reproducible demos.

```bash
git clone https://github.com/kongshuilinhua/XHX-Agent.git
cd XHX-Agent
uv sync
```

Initialize the workspace and build the repo intelligence index in your target codebase:

```bash
uv run xhx init          # creates .xhx/, XHX.md, and the repo index
uv run xhx repo-index    # prints index diagnostics
```

Real output from this repository:

```text
repo index: current
schema: 1
files: 165
symbols: 860
import edges: 388
call edges: 2000
references: 2000
```

Run a task headlessly. `--dry-run` previews the plan and token budget without editing files:

```bash
uv run xhx run "explain the orchestrator architecture" --profile mock --dry-run
```

```text
status: success
summary: Read-only mock plan.
steps: 1
context: 5068/6000 estimated tokens
trace: .xhx/traces/dry-run-...jsonl
```

Pick the orchestrator paradigm explicitly with `--mode`:

```bash
uv run xhx run "refactor the math helpers" --profile mock --mode loop    # autonomous ReAct loop
uv run xhx run "refactor the math helpers" --profile mock --mode plan    # plan → execute → verify
uv run xhx run "refactor the math helpers" --profile mock --mode graph   # LangGraph multi-agent
```

Open the interactive REPL or the full-screen dashboard:

```bash
uv run xhx chat              # prompt-toolkit REPL with slash commands
uv run xhx tui --fullscreen  # Textual dashboard
```

In the REPL the model's answer **streams token-by-token** into a thin status line (`state · mode · turn · tokens · streaming`). Teach it durable facts with `/remember <fact>`, list them with `/memory`, and toggle the post-run **suggest-confirm** auto-extraction with `/automem on|off`. To route roles to cheaper/stronger models and add a fallback chain, edit the `routing` block in `.xhx/config.json` (`roles: {explore: cheap, …}`, `fallback: [strong, …]`).

---

## Three execution paradigms

All three run over the identical tool / safety / context / code-intelligence base and the same native tool-calling protocol — only the control flow differs.

| | `loop` (default) | `plan` | `graph` |
|:--|:--|:--|:--|
| **Style** | Single autonomous agent (ReAct) | Plan-Execute controller | Multi-agent workflow (LangGraph `StateGraph`) |
| **Control flow** | One model iterates read → edit → verify across up to `max_loop_turns` until it reports done | Plans the whole task up front, executes the steps, verifies, and runs bounded self-repair on failure | Explicit roles: coordinator splits the task → a write-capable worker executes each sub-task → reviewer judges PASS/FAIL, with a conditional re-execute loop |
| **Decomposition** | Implicit, per-turn | Batch, up front | Coordinator-driven, into sub-tasks |
| **Best for** | Open-ended edits, exploratory work | Tasks that benefit from an explicit plan + verification gate | Tasks where plan / execute / review separation across roles is valuable |
| **Real-model overhead** | Lowest (1 agent) | Low (1 agent + verify) | Highest (~4× tokens, ~3× time — multi-agent chatter) |
| **Select via** | `--mode loop` / `/mode loop` | `--mode plan` / `/mode plan` | `--mode graph` / `/mode graph` |

When `--mode` is omitted, the task runs on the default **`loop`** — the same native tool-calling path as the explicit paradigms. The legacy `linear` / `dag` orchestrators (the older ModelPlan path) are **retained but no longer the default**: reach them with an explicit `--mode linear` / `--mode dag`, or via the `--dry-run` preview.

---

## Benchmark: quantifying the paradigms

The core thesis — *one base, three interchangeable paradigms* — is only convincing with numbers. `xhx benchmark` runs a fixture task-set across the paradigms and writes a comparison report (`.xhx/benchmark/report.md` + `report.json`):

```bash
uv run xhx benchmark --modes loop,plan,graph --profile default  # real model (DeepSeek)
uv run xhx benchmark --modes loop,plan,graph                    # offline, deterministic (mock)
```

**Real model** (DeepSeek `deepseek-chat`) — three read-only research fixtures, per-paradigm means:

| Paradigm | Success | Mean turns | Mean tokens | Mean wall-clock (s) |
|:--|:--:|:--:|:--:|:--:|
| `loop` | 3/3 | 4.7 (tool iterations) | ~14.3K | 14.1 |
| `plan` | 3/3 | 4.0 (tool iterations) | ~15.0K | 13.0 |
| `graph` | 2/3 | 1.7 (review rounds) | **~58.9K** | 44.6 |

Two things jump out:

- **The multi-agent `graph` costs ~4× the tokens and ~3× the wall-clock** of the single-agent `loop`/`plan` — coordinator, worker(s), and reviewer each carry their own full context. (Its lower "turn" count is a *different unit* — review rounds, not tool iterations.)
- **More agents did not mean a better outcome here:** `graph` succeeded on only 2 of 3 fixtures (one reviewer returned FAIL), while `loop` and `plan` completed all three. Role separation buys explicit plan/review structure — it does not come for free, and it is not automatically more reliable.

The offline `mock` profile reproduces the *same shape* deterministically (`graph` ~3× the tokens of `loop`/`plan`) for CI and zero-key demos, though it doesn't exercise the LLM coordination `graph` depends on — so success rate there is only meaningful under a real model. Reproduce either table with the commands above (`--profile default` requires a `DEEPSEEK_API_KEY`).

<details>
<summary>Offline <code>mock</code> table (deterministic, reproducible)</summary>

| Paradigm | Tasks | Mean turns | Mean tokens | Mean wall-clock (s) |
|:--|:--:|:--:|:--:|:--:|
| `loop` | 3 | 1.0 | ~978 | 0.40 |
| `plan` | 3 | 1.0 | ~986 | 0.38 |
| `graph` | 3 | 2.0 | ~2919 | 0.77 |

</details>

---

## Engineering notes: what building this taught me

Three findings worth more than a green test suite — each is a place where a *real* model diverged from the comfortable offline mock.

**1 · `apply_patch` met the real model.** The patch tool was first built around a custom `*** Begin Patch … *** End Patch` envelope, and the offline mock dutifully produced it. Switched to real DeepSeek, *every* edit failed: `Patch must start with *** Begin Patch`. The real model emits **unified diffs** — often wrapped in a ```` ```diff ```` fence — not the bespoke envelope. The fix was to make the parser dispatch by *format*: envelope, unified diff (`---` / `+++` / `@@`, with `/dev/null` meaning a new file), and a fence-stripping pre-pass. **Lesson:** mock parity is not real parity. The real model's output distribution *is* the spec you have to parse.

**2 · A prompt is not a silver bullet.** I added a `dispatch` tool so the agent could hand a focused, multi-file investigation to an isolated read-only sub-agent — keeping the parent's context clean. The capability is wired, gated, and correct. But even with explicit prompt guidance, the real model overwhelmingly prefers to just read the files itself and rarely reaches for `dispatch`. Rather than dress that up, I'm recording it plainly: **changing model behavior often needs a stronger mechanism than a paragraph in the system prompt** — and knowing the difference is part of the job.

**3 · Putting a number on coordination.** A small token meter wraps every model call, accumulating a `tiktoken` estimate of the outgoing context into the run metrics. That is what turns "graph has more overhead" into "graph costs ~4× the tokens" in the real-model table above. Cheap to build, and it converts an architectural intuition into something a reviewer can check.

**4 · An injection feature isn't real until a memory-only fact moves the output.** Cross-session recall is easy to *wire* and easy to fool yourself about. So the test wasn't "does the recall function return rows" — it was: write a fact that exists **only** in `.xhx/memory/` (the project mascot is a blue axolotl named Pacha), ask the real model an otherwise-unanswerable question, and confirm the recalled fact both reached the system prompt and shaped the answer. **Lesson:** for anything that silently injects context, verify end-to-end with a fact the model could not otherwise know — not with a unit test of the retriever.

---

## Commands

### CLI

```bash
uv run xhx run "<task>" [options]
```

| Option | Description |
|:--|:--|
| `--profile <name>` | LLM profile from `.xhx/profiles.json` (`mock` runs offline). |
| `--mode <loop\|plan\|graph\|linear\|dag>` | Pick the orchestrator paradigm (default: `loop`). |
| `--auto-repair` | Enable up to 2 self-repair rounds when targeted verification fails. |
| `--dry-run` | Preview plan, token budget, and risks, then exit. |
| `-y`, `--yes` | Pre-approve `confirm`-tier commands (non-interactive). |
| `--json` | Emit the run result as structured JSON. |
| `--continue` | Resume from the most recent session, injecting its summary as context. |
| `--resume <run-id>` | Resume from a specific past session (`xhx sessions` lists them). |

Other commands: `init`, `repo-index`, `sessions`, `chat`, `tui`, `rpc` (JSON-RPC 2.0 over stdio), `replay <run-id>`, `benchmark`, `memory`.

### REPL slash commands

`/help` · `/model` · `/mode` · `/status` · `/plan` · `/evidence` · `/context` · `/verify` · `/repair` · `/diff` · `/skills` · `/remember` · `/memory` · `/automem` · `/dashboard` · `/live` · `/cancel` · `/clear` · `/exit`

---

## Implementation status

Stated plainly so capability is never confused with roadmap.

**Fully implemented**
- Tri-paradigm orchestrator on one native tool-calling protocol: `loop` (autonomous ReAct), `plan` (Plan-Execute with bounded self-repair), and `graph` (LangGraph coordinator → worker → reviewer) — all wired into every entry point (CLI `--mode`, REPL/TUI `/mode`) and all verified end-to-end against a real model.
- Sub-agents via the `dispatch` tool: a read-only `explore` agent (its own message history + restricted toolset) and a **write-capable `edit` agent** that edits inside its own git worktree and **merges back serially with first-wins conflict detection**.
- Three-paradigm benchmark harness (`xhx benchmark --modes …`) emitting a Markdown + JSON comparison report, with per-call token metering.
- Long-term memory: `.xhx/memory/` of 4-type facts with deterministic recall injected into the system prompt under budget, a freshness check against current files, explicit `/remember` writes, and post-run **suggest-confirm** auto-extraction (`/automem`) — verified end-to-end against a real model.
- Multi-model routing: per-role `role → profile` mapping plus an ordered **fallback chain** that degrades gracefully on a primary error/rate-limit; orthogonal to streaming.
- Streaming tool-calling output to a thin live status line (with fragmented `tool_calls` reassembled over SSE), plus validity-preserving **microcompact** of long loop histories.
- `repo_query` read-only tool exposing the symbol / reference index to the model through the same risk-gated kernel.
- Context Pack compiler with `tiktoken` budgeting, priority pruning, and history compaction (heuristic, or LLM summary in autonomous mode with heuristic fallback).
- Safe Execution Kernel: risk tiering, denylist + metacharacter + inline-interpreter blocking, git-worktree isolation, in-place Restore Plan fallback.
- Repo intelligence: symbol / import / reference / call index — Python via `ast`, JS/TS symbols via tree-sitter — persisted as JSON with a SQLite mirror and incremental refresh on file change.
- Verification router + bounded (≤2-round) auto-repair; replayable evidence traces; session recovery (`--continue` / `--resume` / `sessions`).
- REPL (prompt-toolkit) and full-screen TUI (Textual); JSON-RPC 2.0 stdio interface; offline `mock` profile; benchmark + replay.

**Simplified / partial (by design)**
- `linear` / `dag` (the older ModelPlan path) are retained but **no longer the default** — reachable only via explicit `--mode linear/dag` and the `--dry-run` preview; the headline decomposition work happens in `plan` and `graph`, which are LLM-driven via tool-calling.
- The `graph` paradigm is a deliberately lean coordinator → worker → reviewer workflow, kept minimal for a clean contrast against `loop`/`plan`.
- Write `edit` sub-agents run sequentially, each isolated in its own worktree and merged back with conflict detection; truly *concurrent* sub-agent execution is a future optimization.
- The reference index is text-level symbol-name matching, not semantic resolution.
- JS/TS import and call extraction uses regex (only JS/TS *symbols* use tree-sitter); Python uses full `ast`.

See [`docs/implementation/20-implementation-baseline.md`](docs/implementation/20-implementation-baseline.md) and [`docs/01-architecture.md`](docs/01-architecture.md) for details.

---

## Project layout

```text
src/xhx_agent/
  orchestrators/   loop · plan · graph (primary) + linear · dag (fallback) · sub-agent · microcompact
  memory/          long-term facts: store + deterministic recall + suggest-confirm extraction
  context/         Context Pack compiler + token budgeting + compaction
  repo_intel/      symbol / import / reference / call index (ast + tree-sitter, JSON + SQLite)
  safety/          risk classification · policy · worktree · checkpoints · repair
  planner/         intent classifier · execution modes · reviewer · agents
  verification/    targeted test router
  evals/           benchmark harness + RunMetrics
  evidence/        trace store + report generation
  runtime/         app loop · sessions · config (incl. routing)
  models/          mock + OpenAI-compatible (streaming) + multi-model routing & fallback
  cli/ · tui/      REPL, full-screen dashboard, JSON-RPC
```

---

## Development

```bash
uv run pytest          # test suite
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy src        # type-check
```

---

<div align="center">
Built by <a href="https://github.com/kongshuilinhua/XHX-Agent">kongshuilinhua</a> · MIT License
</div>
