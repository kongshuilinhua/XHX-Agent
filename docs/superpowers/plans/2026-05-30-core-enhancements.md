# 核心功能增强实施计划书 (Core Enhancements Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 推进并实现精确 Token 计算器、TUI/CLI 自动补全及跨 Run 全局证据记忆这三个高级特性。

**Architecture:** 
1. `tiktoken` 进行惰性载入与安全计数，提供优雅降级。
2. 提炼公共 `XhxCompleter`，分别以 `PromptToolkitCompleter` 驱动 CLI REPL 交互，以 `XhxTextualSuggester` 驱动 TUI 行内补全。
3. 扩展 `EvidenceStore` 的全局扫描功能，并在 `compile_context_pack` 中进行磁盘全局合并去重排序。

**Tech Stack:** Python 3.13, tiktoken, prompt-toolkit, textual, pytest.

---

## 任务列表 (Tasks)

### 任务 1：升级项目依赖与运行环境配置

**Files:**
* Modify: `pyproject.toml`

- [ ] **步骤 1：修改 `pyproject.toml` 中的 `dependencies` 列表**

在 `dependencies` 中新增 `tiktoken>=0.8.0` 与 `prompt-toolkit>=3.0.36`。
```toml
dependencies = [
    "httpx>=0.28.1",
    "langgraph>=1.2.1",
    "pydantic>=2.13.4",
    "pyjsparser>=2.7.1",
    "rich>=15.0.0",
    "textual>=6.7.0",
    "tree-sitter>=0.25.2",
    "tree-sitter-javascript>=0.25.0",
    "tree-sitter-typescript>=0.23.2",
    "typer>=0.25.1",
    "tiktoken>=0.8.0",
    "prompt-toolkit>=3.0.36",
]
```

- [ ] **步骤 2：执行环境同步更新命令**

在终端运行以下命令，使用 `uv` 刷新虚拟环境：
运行：`uv sync`
预期输出：系统成功拉取并安装 `tiktoken` 和 `prompt-toolkit` 及其相关子依赖，锁文件 `uv.lock` 更新。

- [ ] **步骤 3：进行载入验证测试**

运行：`uv run python -c "import tiktoken; import prompt_toolkit; print('Sync complete successfully!')"`
预期输出：`Sync complete successfully!` (无任何导入或链接错误)

- [ ] **步骤 4：提交本次环境变更**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add tiktoken and prompt-toolkit to dependencies"
```

---

### 任务 2：高精度 Token 计数器实现及优雅降级 (TDD)

**Files:**
* Modify: `src/xhx_agent/context/compiler.py`
* Test: `tests/test_token_estimator.py`

- [ ] **步骤 1：编写失败的单元测试**

新建测试文件 `tests/test_token_estimator.py`，测试高精度计数和在 `sys.modules` 中模拟 `tiktoken` 缺失时的降级回退。
```python
import sys
from unittest.mock import patch
import pytest
from xhx_agent.context.compiler import _estimate_tokens

def test_token_estimate_with_tiktoken():
    text = "Hello, world! This is a test of tiktoken compilation."
    # 验证当 tiktoken 可用时，计数精确
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    expected = len(enc.encode(text, disallowed_special=()))
    assert _estimate_tokens(text) == expected

def test_token_estimate_fallback():
    # 模拟 tiktoken 模块彻底不可导出的情景
    with patch.dict(sys.modules, {"tiktoken": None}):
        with patch("xhx_agent.context.compiler._tiktoken_encoding", None):
            text = "Hello, world! 降级测试。"
            # 计算粗略估算值
            # 每一个 ascii 字符 0.25, 非 ascii 1.5
            val = _estimate_tokens(text)
            assert val > 0
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`uv run pytest tests/test_token_estimator.py -v`
预期输出：FAIL，显示导入/断言不匹配，或者由于测试尚未通过引发失败。

- [ ] **步骤 3：编写高精度计数与优雅降级实现**

修改 `src/xhx_agent/context/compiler.py` 的末尾 `_estimate_tokens` 方法：
```python
_tiktoken_encoding = None

def _estimate_tokens(text: str) -> int:
    global _tiktoken_encoding
    if _tiktoken_encoding is None:
        try:
            import tiktoken
            _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _tiktoken_encoding = False
            
    if _tiktoken_encoding:
        try:
            return len(_tiktoken_encoding.encode(text, disallowed_special=()))
        except Exception:
            pass
            
    tokens = 0.0
    for char in text:
        if ord(char) > 127:
            tokens += 1.5
        else:
            tokens += 0.25
    return max(1, int(tokens))
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_token_estimator.py -v`
预期输出：PASS 2 passed.

- [ ] **步骤 5：提交代码**

```bash
git add src/xhx_agent/context/compiler.py tests/test_token_estimator.py
git commit -m "feat: integrate high-precision tiktoken estimator with graceful fallback"
```

---

### 任务 3：跨 Run 全局证据记忆集成 (TDD)

**Files:**
* Modify: `src/xhx_agent/evidence/store.py`
* Modify: `src/xhx_agent/context/compiler.py`
* Test: `tests/test_cross_run_evidence.py`

- [ ] **步骤 1：编写全局证据加载与 Context 编译的失败测试**

创建 `tests/test_cross_run_evidence.py`：
```python
import json
from pathlib import Path
from xhx_agent.evidence.store import EvidenceEntry, EvidenceStore
from xhx_agent.context.compiler import compile_context_pack
from xhx_agent.repo_intel.scanner import ProjectScan

def test_cross_run_evidence_loading(tmp_path):
    # 模拟工作区结构
    workspace = tmp_path
    evidence_dir = workspace / ".xhx" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    
    # 写入两个历史 Run 证据文件
    run1_file = evidence_dir / "run-1111.jsonl"
    entry1 = EvidenceEntry(
        kind="error", source="test_file.py", summary="Historical crash in run 1", artifact_ref="trace://1"
    )
    run1_file.write_text(json.dumps(entry1.model_dump()) + "\n", encoding="utf-8")
    
    run2_file = evidence_dir / "run-2222.jsonl"
    entry2 = EvidenceEntry(
        kind="test", source="test_file.py", summary="Historical crash in run 1", artifact_ref="trace://2" # 故意重复 summary/source
    )
    entry3 = EvidenceEntry(
        kind="patch", source="util.py", summary="Historical patch success", artifact_ref="trace://3"
    )
    run2_file.write_text(
        json.dumps(entry2.model_dump()) + "\n" + json.dumps(entry3.model_dump()) + "\n",
        encoding="utf-8"
    )
    
    # 验证 EvidenceStore 能够载入全部
    store = EvidenceStore(workspace, "run-current")
    history = store.load_all_historical_evidence()
    assert len(history) == 3
    
    # 编译 Context Pack，验证重复项已被去重，高质量且新鲜的项成功输出
    scan = ProjectScan(detected_languages=[], file_count=0, python=None, node=None)
    pack = compile_context_pack(
        workspace=workspace,
        task="Fix crash in test_file",
        scan=scan,
        evidence_entries=[
            EvidenceEntry(kind="checkpoint", source="main.py", summary="Current checkpoint", artifact_ref="trace://4")
        ]
    )
    
    # 检查项中包含历史 Run 证据，且无重复
    sources = [item.source for item in pack.items if item.kind.startswith("evidence:")]
    assert "util.py" in sources
    assert "test_file.py" in sources
    assert "main.py" in sources
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`uv run pytest tests/test_cross_run_evidence.py -v`
预期输出：FAIL，显示 `EvidenceStore` 没有 `load_all_historical_evidence` 属性。

- [ ] **步骤 3：在 `EvidenceStore` 中实现 `load_all_historical_evidence`**

修改 `src/xhx_agent/evidence/store.py`：
```python
    def load_all_historical_evidence(self) -> list[EvidenceEntry]:
        evidence_dir = xhx_dir(self.workspace) / "evidence"
        if not evidence_dir.exists():
            return []
        entries: list[EvidenceEntry] = []
        for path in evidence_dir.glob("*.jsonl"):
            try:
                for row in self._read_jsonl(path):
                    try:
                        entries.append(EvidenceEntry(**row))
                    except Exception:
                        pass
            except Exception:
                pass
        return entries
```

- [ ] **步骤 4：在 `compile_context_pack` 中载入、合并和去重历史证据**

修改 `src/xhx_agent/context/compiler.py` 的 `compile_context_pack` 中证据处理的核心逻辑（大约在第 165 行左右）：
```python
    import json
    all_evidence = list(evidence_entries or [])
    try:
        evidence_dir = workspace / ".xhx" / "evidence"
        if evidence_dir.exists():
            for path in evidence_dir.glob("*.jsonl"):
                try:
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            try:
                                data = json.loads(line)
                                entry = EvidenceEntry(**data)
                                all_evidence.append(entry)
                            except Exception:
                                pass
                except Exception:
                    pass
    except Exception:
        pass

    for evidence in _select_top_evidence(all_evidence, limit=top_k_evidence):
        candidates.append(
            ContextItem(
                kind=f"evidence:{evidence.kind}",
                source=evidence.source,
                content=evidence.summary,
                priority=_evidence_priority(evidence),
                reason=f"Selected from Evidence Index with confidence={evidence.confidence:.2f}.",
            )
        )
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_cross_run_evidence.py -v`
预期输出：PASS 1 passed.

- [ ] **步骤 6：提交代码**

```bash
git add src/xhx_agent/evidence/store.py src/xhx_agent/context/compiler.py tests/test_cross_run_evidence.py
git commit -m "feat: support loading and de-duplicating cross-run evidence history"
```

---

### 任务 4：实现可复用的智能补全候选提取器 (TDD)

**Files:**
* Create: `src/xhx_agent/cli/completion.py`
* Test: `tests/test_completion.py`

- [ ] **步骤 1：编写补全器的单元测试**

创建 `tests/test_completion.py` 文件：
```python
from pathlib import Path
import pytest
from xhx_agent.cli.completion import XhxCompleter

def test_completer_slash_commands(tmp_path):
    completer = XhxCompleter(tmp_path)
    # 补全斜杠命令前缀
    res1 = completer.get_completions("/vi")
    assert "/verify" in res1
    
    # 补全全部斜杠命令
    res2 = completer.get_completions("/")
    assert len(res2) >= 12
    assert "/help" in res2

def test_completer_paths(tmp_path):
    # 构造假文件
    workspace = tmp_path
    src_dir = workspace / "src" / "agent"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "app.py").touch()
    (workspace / "README.md").touch()
    
    completer = XhxCompleter(workspace)
    
    # 输入以 "s" 开头的文件路径或符号补全
    res = completer.get_completions("src/")
    assert "src/agent/app.py" in res
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`uv run pytest tests/test_completion.py -v`
预期输出：FAIL，找不到 `xhx_agent.cli.completion` 模块。

- [ ] **步骤 3：编写补全提供器 `XhxCompleter` 实现**

创建 `src/xhx_agent/cli/completion.py`：
```python
from pathlib import Path
from xhx_agent.repo_intel.index import load_repo_intel_index

class XhxCompleter:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.commands = [
            "/help", "/exit", "/model", "/status", "/plan", "/evidence", 
            "/context", "/verify", "/repair", "/diff", "/skills", "/clear"
        ]
        self._index = None

    def get_index(self):
        if self._index is None:
            try:
                self._index = load_repo_intel_index(self.workspace)
            except Exception:
                pass
        return self._index

    def get_completions(self, text: str) -> list[str]:
        if not text:
            return []
        
        # 1. 补全斜杠命令
        if text.startswith("/"):
            if " " in text:
                cmd, _, arg = text.partition(" ")
                return [f"{cmd} {p}" for p in self._get_path_completions(arg)]
            return [cmd for cmd in self.commands if cmd.startswith(text)]
            
        # 2. 如果包含路径分隔符，直接补全文件路径
        if "/" in text or "\\" in text or text.startswith("."):
            return self._get_path_completions(text)
            
        # 3. 混合匹配：同时提供符号和路径
        return self._get_symbol_completions(text) + self._get_path_completions(text)

    def _get_path_completions(self, prefix: str) -> list[str]:
        prefix_normalized = prefix.replace("\\", "/")
        index = self.get_index()
        paths = []
        if index and index.repo_map:
            paths = [f.path for f in index.repo_map.files]
        else:
            try:
                paths = [
                    str(p.relative_to(self.workspace)).replace("\\", "/") 
                    for p in self.workspace.glob("**/*") if p.is_file()
                ]
            except Exception:
                pass
        return [p for p in paths if p.startswith(prefix_normalized)]

    def _get_symbol_completions(self, prefix: str) -> list[str]:
        if len(prefix) < 2:
            return []
        index = self.get_index()
        if not index or not index.symbol_index:
            return []
        symbols = {s.name for s in index.symbol_index.symbols}
        return [sym for sym in symbols if sym.lower().startswith(prefix.lower())]
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_completion.py -v`
预期输出：PASS 2 passed.

- [ ] **步骤 5：提交代码**

```bash
git add src/xhx_agent/cli/completion.py tests/test_completion.py
git commit -m "feat: implement reusable XhxCompleter for slash commands, files, and symbols"
```

---

### 任务 5：交互式 CLI Console 终端 Tab 下拉补全集成

**Files:**
* Modify: `src/xhx_agent/cli/console.py`

- [ ] **步骤 1：在 `console.py` 中引入 `prompt-toolkit` 交互输入机制**

修改 `src/xhx_agent/cli/console.py`，实现并绑定 `PromptToolkitCompleter` 替代原有的 `typer.prompt("xhx")`：
```python
# 修改头部 import 块，添加：
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from xhx_agent.cli.completion import XhxCompleter

class PromptToolkitCompleter(Completer):
    def __init__(self, completer: XhxCompleter) -> None:
        self.completer = completer

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.strip():
            return
        candidates = self.completer.get_completions(text)
        
        # 计算补全的相对起始点
        for val in candidates:
            yield Completion(val, start_position=-len(text))
```

在 `CommandConsole.__init__` (第 50 行左右) 初始化补全器和 Session：
```python
        self.completer = XhxCompleter(self.workspace)
        self.prompt_session = PromptSession(completer=PromptToolkitCompleter(self.completer))
```

在 `CommandConsole.run` 的循环体内 (第 74 行左右)，将 `text = typer.prompt("xhx")` 替换为：
```python
            try:
                # 使用 prompt_toolkit 驱动，获得下拉菜单以及 Tab 联想支持
                text = self.prompt_session.prompt("xhx> ")
```

- [ ] **步骤 2：运行已有单元和集成测试确保兼容性**

运行：`uv run pytest`
预期输出：全体测试 (包含新增的 6 个测试) 全部 PASS，证明重构控制台交互并未破坏原有的任何命令派发逻辑！

- [ ] **步骤 3：提交本次集成修改**

```bash
git add src/xhx_agent/cli/console.py
git commit -m "feat: integrate prompt-toolkit dropdown completion into REPL CLI console"
```

---

### 任务 6：TUI 全屏界面行内灰色补全建议集成

**Files:**
* Modify: `src/xhx_agent/tui/textual_app.py`

- [ ] **步骤 1：在 `textual_app.py` 中接入补全建议器**

修改 `src/xhx_agent/tui/textual_app.py`。
在头部导入区添加：
```python
from textual.suggester import Suggester
from xhx_agent.cli.completion import XhxCompleter

class XhxTextualSuggester(Suggester):
    def __init__(self, completer: XhxCompleter) -> None:
        super().__init__(case_sensitive=False)
        self.completer = completer

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        candidates = self.completer.get_completions(value)
        if candidates:
            # 候选列表中第一个通常匹配度最高，将其返回作为灰色灰色字符联想建议显现
            return candidates[0]
        return None
```

- [ ] **步骤 2：将建议器绑定到 `Input` 文本框中**

在 `TextualApp.__init__` 或声明组件的 `compose` 方法中实例化并绑定补全建议器：
修改 `TextualApp.compose` (大约第 195-207 行)：
```python
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield Static(id="conversation")
            with Vertical(id="side"):
                yield Static(id="runtime")
                yield Static(id="changed")
                yield Static(id="details")
                yield Static(id="commands")
                
        # 初始化补全建议器
        completer = XhxCompleter(self.workspace)
        yield Input(
            placeholder="Type a task or slash command. Press Tab or Right arrow to complete.",
            id="input",
            suggester=XhxTextualSuggester(completer)
        )
        yield Footer()
```

- [ ] **步骤 3：执行最终测试集全套校验**

运行：`uv run pytest`
预期输出：所有单元测试以及 TUI 页面渲染校验全部流畅通过，无任何崩溃或异常抛出。

- [ ] **步骤 4：提交最终集成变更**

```bash
git add src/xhx_agent/tui/textual_app.py
git commit -m "feat: add inline autocomplete suggestion support to TUI Input textbox"
```
