# 2026-05-30 核心功能增强设计规范 (Design Spec)

## 一、概述 (Goal Description)
为进一步提升 XHX-Agent 终端开发工具的智能化、鲁棒性以及用户交互体验，本项目决定实现以下三个方向的重大增强：
1. **精确的 Token 计算器 (Tokenizer Integration)**：引入 `tiktoken` 库，并使用 `"cl100k_base"` 编码器进行高精度的 Token 计数。如果载入失败，优雅回退到原字符数估算算法。
2. **TUI/CLI 交互增强 (Tab Completion)**：基于 `prompt-toolkit` 和 Textual 的 `Suggester` 机制，提供斜杠命令、本地文件路径和 codebase 代码符号的实时智能 Tab 补全能力。
3. **跨 Run 的全局证据追踪与记忆 (Cross-Run Evidence History)**：打破单次执行 (Run) 的限制，动态合并历史 Run 的证据库条目，参与 Context Pack 的选择、去重和排序，为 Agent 提供跨会话的深度记忆。

---

## 二、详细设计 (Detailed Design)

### 1. Tokenizer 集成与优雅降级

#### 设计要点
* **依赖库**：在 `pyproject.toml` 中添加 `"tiktoken>=0.8.0"`。
* **延迟载入 (Lazy Loading)**：避免在模块级别直接 `import tiktoken`。而是在 `compiler.py` 的估算入口中进行 `try-except` 动态载入，确保在 `tiktoken` 未安装或二进制库冲突时系统依然能正常运行。
* **安全性**：在对文本进行编码时，使用 `encode(text, disallowed_special=())`，避免因为输入包含诸如 `<|endoftext|>` 等特殊标识符而引发运行时 `ValueError` 崩溃。

#### 代码架构设计
```python
# src/xhx_agent/context/compiler.py 中的变化

_tiktoken_encoding = None

def _estimate_tokens(text: str) -> int:
    global _tiktoken_encoding
    if _tiktoken_encoding is None:
        try:
            import tiktoken
            _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _tiktoken_encoding = False  # 载入失败，置为 False 触发降级
            
    if _tiktoken_encoding:
        try:
            return len(_tiktoken_encoding.encode(text, disallowed_special=()))
        except Exception:
            pass  # 如果编码过程中抛出异常，继续向下走降级逻辑
            
    # 优雅回退逻辑 (原有基于字符的快速估算)
    tokens = 0.0
    for char in text:
        if ord(char) > 127:
            tokens += 1.5
        else:
            tokens += 0.25
    return max(1, int(tokens))
```

---

### 2. TUI/CLI 自动补全设计

#### 共享候选提供者设计 (Completion Provider)
实现一个通用的补全建议提供函数，能够根据当前输入的前缀，检索出相匹配的命令、文件路径或代码符号。
```python
# 新增公共文件：src/xhx_agent/cli/completion.py

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
        stripped = text.strip()
        
        # 1. 补全斜杠命令
        if text.startswith("/"):
            # 如果是具体命令后的参数补全，比如 "/diff " 后面接路径
            if " " in text:
                cmd, _, arg = text.partition(" ")
                return [f"{cmd} {p}" for p in self._get_path_completions(arg)]
            return [cmd for cmd in self.commands if cmd.startswith(text)]
            
        # 2. 补全路径或代码符号
        # 如果看起来像路径前缀，或者已输入了 "/" 等字符
        if "/" in text or "." in text or text.startswith("."):
            return self._get_path_completions(text)
            
        # 3. 补全符号和文件路径混合
        return self._get_symbol_completions(text) + self._get_path_completions(text)

    def _get_path_completions(self, prefix: str) -> list[str]:
        index = self.get_index()
        paths = []
        if index and index.repo_map:
            paths = [f.path for f in index.repo_map.files]
        else:
            # 备用：物理扫描工作区
            try:
                paths = [str(p.relative_to(self.workspace)).replace("\\", "/") 
                         for p in self.workspace.glob("**/*") if p.is_file()]
            except Exception:
                pass
        return [p for p in paths if p.startswith(prefix)]

    def _get_symbol_completions(self, prefix: str) -> list[str]:
        if len(prefix) < 2:
            return []
        index = self.get_index()
        if not index or not index.symbol_index:
            return []
        symbols = {s.name for s in index.symbol_index.symbols}
        return [sym for sym in symbols if sym.lower().startswith(prefix.lower())]
```

#### CLI REPL 命令行集成
替换 `src/xhx_agent/cli/console.py` 中阻塞的 `typer.prompt` 为 `prompt_toolkit`。
```python
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion

class PromptToolkitCompleter(Completer):
    def __init__(self, completer: XhxCompleter) -> None:
        self.completer = completer

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        candidates = self.completer.get_completions(text)
        
        # 提取当前正在输入的词段进行相对替换
        # 如果是路径或含有空格，计算合理替换位置
        last_word = text.split()[-1] if text.strip() else ""
        for val in candidates:
            # 提供智能下拉选项
            yield Completion(val, start_position=-len(text))
```

#### TUI 界面集成
在 `src/xhx_agent/tui/textual_app.py` 中利用 Textual 的 `Suggester` 机制提供行内灰色联想补全：
```python
from textual.suggester import Suggester

class XhxTextualSuggester(Suggester):
    def __init__(self, completer: XhxCompleter) -> None:
        super().__init__(case_sensitive=False)
        self.completer = completer

    async def get_suggestion(self, value: str) -> str | None:
        candidates = self.completer.get_completions(value)
        if candidates:
            # 返回匹配度最高的第一个补全项作为行内灰色字显
            return candidates[0]
        return None
```

---

### 3. 跨 Run 的全局证据追踪与记忆

#### 数据模型设计
* 历史 Run 的证据文件存储在 `.xhx/evidence/{run_id}.jsonl` 下。
* 每一个 `.jsonl` 文件的每一行都是一个 JSON 格式的 `EvidenceEntry` 实例。

#### 核心逻辑设计
1. 在 `compiler.py` 的 `compile_context_pack` 编译流程中，除了接收当前 Run 内存中已收集的 `evidence_entries` 之外，动态从磁盘中捞取所有历史数据：
```python
    # 动态加载所有历史 Run 的证据条目
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
```
2. **去重与精选**：由于合并了历史 Run，证据数量可能大大增加。利用原有的 `_select_top_evidence` 进行深层去重：
   * 去重 Key 为：`(entry.kind, entry.source, entry.summary)`。
   * 排序权重保持：`_evidence_priority` 结合 `created_at` 时间戳降序排序，确保最高质量且最鲜活的历史证据能够自动夺得预算额度，而过时或不重要的历史记录被自然遗忘。

---

## 三、验证计划 (Verification Plan)

### 1. 自动化单元测试
* 编写 `tests/test_token_estimator.py`：测试高精度 `tiktoken` 计数与降级机制在 `tiktoken` 导入失败时的正常运转。
* 编写 `tests/test_completion.py`：验证 `XhxCompleter` 对命令、文件路径和代码符号匹配的精确度。
* 编写 `tests/test_cross_run_evidence.py`：模拟写入多个 Run 的 `.jsonl` 证据文件，调用 `compile_context_pack` 验证其合并、去重及最优筛选逻辑。

### 2. 手动集成测试
* 启动终端：运行 `uv run xhx chat` 验证交互式 REPL 终端中的下拉 Tab 补全效果。
* 启动 TUI：运行交互式全屏终端，测试 Input 文本框中的行内灰色建议补全。
