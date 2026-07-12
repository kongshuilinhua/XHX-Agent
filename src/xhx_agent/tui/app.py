from __future__ import annotations

import asyncio
import os
import random
import re
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from textual.timer import Timer

    from xhx_agent.askuser_dialog import InlineAskUserWidget
    from xhx_agent.permission_dialog import InlinePermissionWidget
    from xhx_agent.plan_dialog import InlinePlanWidget
    from xhx_agent.tools.present_plan import PresentPlanTool

from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message as TMessage
from textual.theme import Theme
from textual.widgets import Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from xhx_agent import __version__
from xhx_agent.agent import (
    Agent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    PermissionRequest,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from xhx_agent.agents.loader import AgentLoader
from xhx_agent.agents.notification import inject_task_notifications
from xhx_agent.agents.task_manager import TaskManager
from xhx_agent.agents.trace import TraceManager
from xhx_agent.cache import FileCache
from xhx_agent.client import (
    AuthenticationError,
    LLMClient,
    LLMError,
    resolve_context_window,
)
from xhx_agent.commands import (
    CommandContext,
    CommandRegistry,
    complete,
    parse_command,
)
from xhx_agent.commands.completion import CompletionPopup
from xhx_agent.commands.handlers import register_all_commands
from xhx_agent.commands.handlers.skill_register import register_skill_commands
from xhx_agent.commands.handlers.tasks import create_tasks_command
from xhx_agent.commands.handlers.worktree import create_worktree_command
from xhx_agent.config import ProviderConfig
from xhx_agent.conversation import ConversationManager, Message
from xhx_agent.hooks import HookContext, HookEngine
from xhx_agent.mcp import MCPManager
from xhx_agent.memory import (
    MemoryManager,
    Session,
    SessionManager,
    find_relevant_memories,
    generate_session_summary,
    load_instructions,
    make_compact_boundary,
    render_reminder,
)
from xhx_agent.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from xhx_agent.runtime.mcp_config import MCPServerConfig
from xhx_agent.skills.executor import SkillExecutor
from xhx_agent.skills.loader import SkillLoader
from xhx_agent.teammate_tree import TeammateTree
from xhx_agent.tools import ToolRegistry, create_default_registry
from xhx_agent.tools.agent_tool import AgentTool
from xhx_agent.tools.ask_user import AskUserEvent, AskUserTool
from xhx_agent.tools.impl.tool_search import ToolSearchTool
from xhx_agent.tools.load_skill import LoadSkill
from xhx_agent.tui.clipboard import read_clipboard
from xhx_agent.tui.format import strip_emoji, strip_system_reminder
from xhx_agent.worktree.cleanup import start_stale_cleanup_task
from xhx_agent.worktree.manager import WorktreeManager

MAX_TRUNCATED_LINES = 20
MAX_AT_REF_BYTES = 10240

_AT_REF_RE = re.compile(r"@([\w./_\-]+(?:\.[\w]+)*)")

_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".xhx", "build", ".gradle"}


def scan_files_for_at(prefix: str, work_dir: str, limit: int = 10) -> list[str]:
    matches: list[str] = []
    base = os.path.join(work_dir, os.path.dirname(prefix)) if "/" in prefix else work_dir
    name_prefix = os.path.basename(prefix).lower()
    if not os.path.isdir(base):
        return matches
    try:
        for entry in sorted(os.listdir(base)):
            if entry in _SKIP_DIRS or entry.startswith("."):
                continue
            if entry.lower().startswith(name_prefix):
                rel = os.path.join(os.path.dirname(prefix), entry) if "/" in prefix else entry
                if os.path.isdir(os.path.join(base, entry)):
                    rel += "/"
                matches.append(rel)
                if len(matches) >= limit:
                    break
    except OSError:
        pass
    return matches


def expand_at_refs(text: str, work_dir: str) -> str:
    def _replace(m: re.Match) -> str:
        rel_path = m.group(1)
        full_path = os.path.join(work_dir, rel_path)
        if not os.path.isfile(full_path):
            return m.group(0)
        try:
            content = open(full_path, encoding="utf-8", errors="replace").read(MAX_AT_REF_BYTES)  # noqa: SIM115 句柄需在方法间持有,不能用 with
            return f"[File: {rel_path}]\n```\n{content}\n```"
        except Exception:
            return m.group(0)

    return _AT_REF_RE.sub(_replace, text)


class ChatInput(TextArea):
    BINDINGS = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("shift+enter", "newline", "Newline", priority=True),
        Binding("ctrl+j", "newline", "Newline", priority=True),
        Binding("ctrl+u", "clear_input", "Clear input", priority=True),
        Binding("tab", "complete", "Complete", priority=True),
        Binding("escape", "dismiss_popup", "Dismiss", priority=True),
        Binding("up", "nav_up", "Navigate up", priority=True),
        Binding("down", "nav_down", "Navigate down", priority=True),
    ]

    class Submitted(TMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class TabComplete(TMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cursor_blink = False
        self._history: list[str] = []
        self._history_index: int = -1
        self._history_draft: str = ""
        self._history_file: Path | None = None

    def load_history(self, work_dir: str) -> None:
        self._history_file = Path(work_dir) / ".xhx" / "history"
        if self._history_file.exists():
            try:
                lines = self._history_file.read_text(encoding="utf-8").splitlines()
                self._history = [l for l in lines if l.strip()]  # noqa: E741 单字母变量沿用既有渲染逻辑命名
            except Exception:
                pass

    def _persist_entry(self, text: str) -> None:
        if self._history_file is None:
            return
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_file, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def _popup(self) -> CompletionPopup | None:
        try:
            return self.app.query_one(CompletionPopup)
        except Exception:
            return None

    def action_submit(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            selected = popup.get_selected()
            popup.hide()
            if selected:
                self._history.append(selected)
                self._persist_entry(selected)
                self._history_index = -1
                self._history_draft = ""
                self.post_message(self.Submitted(selected))
                self.clear()
                return
        text = self.text.strip()
        if text:
            self._history.append(text)
            self._persist_entry(text)
            self._history_index = -1
            self._history_draft = ""
            self.post_message(self.Submitted(text))
            self.clear()

    def action_newline(self) -> None:
        self.insert("\n")

    def action_clear_input(self) -> None:
        """Ctrl+U：一键清空整个输入框（含多行草稿），并复位历史导航/补全状态。

        基类的 ctrl+u 只删当前行光标前的部分；对聊天框来说，整框清空才是预期，
        这里覆盖之。
        """
        self.clear()
        self._history_index = -1
        self._history_draft = ""
        popup = self._popup()
        if popup is not None:
            popup.hide()

    def action_paste(self) -> None:
        """Ctrl+V：从系统剪贴板插入文本。

        Textual 默认的 action_paste 读的是 app 内部剪贴板（只含 app 内复制过的内容），
        在 Windows 上粘不进系统剪贴板的文本。这里改为直接读 OS 剪贴板；读不到时回退
        到父类行为（仍能粘贴 app 内复制的内容）。整段一次性 replace，粘贴的换行保持为
        字面换行，不会被 enter→submit 绑定误触发提交。
        """
        text = read_clipboard()
        if not text:
            super().action_paste()
            return
        start, end = self.selection
        result = self.replace(text, start, end)
        self.move_cursor(result.end_location)

    def action_complete(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            selected = popup.get_selected()
            if selected:
                popup.hide()
                self.clear()
                self.insert(selected + " ")
            return
        text = self.text.strip()
        if text.startswith("/"):
            self.post_message(self.TabComplete(text))
        else:
            self.insert("\t")

    def action_dismiss_popup(self) -> None:
        popup = self._popup()
        if popup is not None:
            popup.hide()

    def action_nav_up(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            popup.nav_up()
            return
        if not self._history:
            return
        if self._history_index == -1:
            self._history_draft = self.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return
        self.clear()
        self.insert(self._history[self._history_index])

    def action_nav_down(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            popup.nav_down()
            return
        if self._history_index == -1:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.clear()
            self.insert(self._history[self._history_index])
        else:
            self._history_index = -1
            self.clear()
            self.insert(self._history_draft)

    class AtFileRequest(TMessage):
        def __init__(self, prefix: str) -> None:
            super().__init__()
            self.prefix = prefix

    class SlashMenuUpdate(TMessage):
        def __init__(self, prefix: str | None) -> None:
            super().__init__()
            self.prefix = prefix

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        text = self.text
        if text.startswith("/"):
            prefix = text[1:]
            if " " not in prefix and "\n" not in prefix:
                self.post_message(self.SlashMenuUpdate(prefix))
            else:
                self.post_message(self.SlashMenuUpdate(None))
        else:
            self.post_message(self.SlashMenuUpdate(None))

        at_idx = text.rfind("@")
        if at_idx < 0:
            return
        after = text[at_idx + 1 :]
        if " " in after or "\n" in after:
            return
        if after:
            self.post_message(self.AtFileRequest(after))


COLLAPSIBLE_TOOLS = {"ReadFile", "Glob", "Grep", "ToolSearch"}


def _is_subagent_tool(tool_name: str) -> bool:
    return tool_name == "Agent"


def _tool_title(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "ReadFile":
        path = os.path.basename(arguments.get("file_path", ""))
        return f"Read {path}" if path else "Read"
    if tool_name == "WriteFile":
        path = os.path.basename(arguments.get("file_path", ""))
        content = arguments.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        return f"Write {path} ({lines} lines)" if path else "Write"
    if tool_name == "EditFile":
        path = os.path.basename(arguments.get("file_path", ""))
        return f"Edit {path}" if path else "Edit"
    if tool_name == "Bash":
        cmd = arguments.get("command", "")
        short = cmd[:50] + "…" if len(cmd) > 50 else cmd
        return f"Bash: {short}" if short else "Bash"
    if tool_name == "Glob":
        return f"Glob: {arguments.get('pattern', '')}"
    if tool_name == "Grep":
        return f"Grep: {arguments.get('pattern', '')}"
    return tool_name


def _format_detail(tool_name: str, arguments: dict[str, Any], output: str) -> str:
    parts: list[str] = []

    if tool_name == "Bash":
        parts.append(f"  IN   {arguments.get('command', '')}")
        parts.append("")
        for line in output.splitlines():
            parts.append(f"  OUT  {line}")
    elif tool_name in ("ReadFile", "WriteFile", "EditFile"):
        parts.append(f"  {arguments.get('file_path', '')}")
        parts.append("")
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  … ({total - MAX_TRUNCATED_LINES} more lines)")
    else:
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  … ({total - MAX_TRUNCATED_LINES} more lines)")

    return "\n".join(parts)


class ToolCallBlock(Static, can_focus=True):
    def __init__(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self._arguments = arguments
        self._title = _tool_title(tool_name, arguments)
        self._full_output = ""
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._loading = True
        self._render_loading()

    def _render_loading(self) -> None:
        self.update(f"  ● {self._title} …")
        self.add_class("tool-block-loading")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        self._full_output = output
        self._is_error = is_error
        self._elapsed = elapsed
        self._loading = False
        self._collapsed = True
        self.remove_class("tool-block-loading")
        if is_error:
            self.add_class("tool-block-error")
        self._render_collapsed()

    def _render_collapsed(self) -> None:
        if self._is_error:
            self.update(f"  ✗ {self._title} ({self._elapsed:.1f}s)")
        else:
            self.update(f"  ✓ {self._title} ({self._elapsed:.1f}s)")

    def _render_expanded(self) -> None:
        if self._is_error:
            header = f"  ✗ {self._title} ({self._elapsed:.1f}s)"
        else:
            header = f"  ✓ {self._title} ({self._elapsed:.1f}s)"
        detail = _format_detail(self.tool_name, self._arguments, self._full_output)
        self.update(f"{header}\n{detail}")

    def on_click(self) -> None:
        if self._loading:
            return
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._render_collapsed()
        else:
            self._render_expanded()


_MODE_CYCLE = [
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.AUTO,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
]

_MODE_COLORS = {
    PermissionMode.DEFAULT: "dim",
    PermissionMode.ACCEPT_EDITS: "green",
    PermissionMode.AUTO: "magenta",
    PermissionMode.PLAN: "yellow",
    PermissionMode.BYPASS: "red",
}

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# MCP server 说明 reminder 的头部（add_system_reminder 会包一层 <system-reminder> 标签）。
_MCP_INSTRUCTIONS_HEADER = "<system-reminder>\n# MCP Server Instructions"


def _has_mcp_instructions(conversation: ConversationManager) -> bool:
    """当前对话历史里是否已注入过 MCP server 说明。

    用 startswith 而非全文包含：reminder 恒以固定头部开始，逐条 O(1) 判断，
    不随消息长度线性扫描。
    """
    return any(
        msg.role == "user" and isinstance(msg.content, str) and msg.content.startswith(_MCP_INSTRUCTIONS_HEADER)
        for msg in conversation.history
    )


def _to_past_tense(verb: str) -> str:
    """把现在进行时动词转换为过去式。"""
    if verb.endswith("ing"):
        stem = verb[:-3]
        if stem.endswith("e"):
            return stem + "d"
        if stem and stem[-1] in "atutitet":
            return stem + "ed"
        return stem + "ed"
    return verb + "ed"


THINKING_VERBS = [
    "Accomplishing",
    "Architecting",
    "Baking",
    "Beboppin'",
    "Befuddling",
    "Bloviating",
    "Boogieing",
    "Boondoggling",
    "Bootstrapping",
    "Brewing",
    "Calculating",
    "Canoodling",
    "Caramelizing",
    "Cascading",
    "Cerebrating",
    "Choreographing",
    "Churning",
    "Coalescing",
    "Cogitating",
    "Combobulating",
    "Composing",
    "Computing",
    "Concocting",
    "Considering",
    "Contemplating",
    "Cooking",
    "Crafting",
    "Creating",
    "Crunching",
    "Crystallizing",
    "Cultivating",
    "Deciphering",
    "Deliberating",
    "Dilly-dallying",
    "Discombobulating",
    "Doodling",
    "Elucidating",
    "Enchanting",
    "Envisioning",
    "Fermenting",
    "Finagling",
    "Flambéing",
    "Flibbertigibbeting",
    "Flummoxing",
    "Forging",
    "Frolicking",
    "Gallivanting",
    "Garnishing",
    "Generating",
    "Germinating",
    "Grooving",
    "Harmonizing",
    "Hatching",
    "Honking",
    "Hullaballooing",
    "Ideating",
    "Imagining",
    "Improvising",
    "Incubating",
    "Inferring",
    "Infusing",
    "Kneading",
    "Lollygagging",
    "Manifesting",
    "Marinating",
    "Meandering",
    "Metamorphosing",
    "Mewing",
    "Moonwalking",
    "Moseying",
    "Mulling",
    "Musing",
    "Noodling",
    "Orbiting",
    "Orchestrating",
    "Percolating",
    "Philosophising",
    "Pondering",
    "Pontificating",
    "Pouncing",
    "Purring",
    "Puzzling",
    "Razzle-dazzling",
    "Ruminating",
    "Scampering",
    "Simmering",
    "Sketching",
    "Spelunking",
    "Spinning",
    "Sprouting",
    "Synthesizing",
    "Thinking",
    "Tinkering",
    "Transfiguring",
    "Transmuting",
    "Undulating",
    "Unfurling",
    "Unravelling",
    "Vibing",
    "Wandering",
    "Whisking",
    "Working",
    "Wrangling",
    "Zigzagging",
]  # 共 105 个动词，与 Go 版 internal/tui/verbs.go 完全一致


class ToolGroupSummary(Static, can_focus=True):
    def __init__(self, count: int, total_elapsed: float, **kwargs: Any) -> None:
        label = f"● Done ({count} tool uses · {total_elapsed:.1f}s)  (ctrl+o to expand)"
        super().__init__(label, **kwargs)
        self._count = count
        self._total = total_elapsed
        self._expanded = False

    def _refresh_display(self) -> None:
        if self._expanded:
            self.update(f"▼ Done ({self._count} tool uses · {self._total:.1f}s)")
        else:
            self.update(f"● Done ({self._count} tool uses · {self._total:.1f}s)  (ctrl+o to expand)")

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh_display()

    def on_click(self) -> None:
        self.toggle()


class SubAgentBlock(Static, can_focus=True):
    def __init__(self, agent_type: str, description: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._agent_type = agent_type or "agent"
        self._description = description[:60] if description else ""
        self._done = False
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._result_preview = ""
        self._tool_count = 0
        self._render_running()

    def _render_running(self) -> None:
        desc = f"({self._description})" if self._description else ""
        self.update(f"● {self._agent_type}{desc}\n     Running…")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        self._done = True
        self._is_error = is_error
        self._elapsed = elapsed
        self._result_preview = output[:300] if output else ""
        self._parse_stats(output)
        self._render_done()

    def _parse_stats(self, output: str) -> None:
        import re

        m = re.search(r"(\d+)\s+tool", output[:200])
        if m:
            self._tool_count = int(m.group(1))

    def _render_done(self) -> None:
        desc = f"({self._description})" if self._description else ""
        tool_info = f"{self._tool_count} tool uses · " if self._tool_count else ""
        if self._collapsed:
            self.update(
                f"● {self._agent_type}{desc}\n    ⎿  Done ({tool_info}{self._elapsed:.1f}s)  (ctrl+o to expand)"
            )
        else:
            self.update(
                f"● {self._agent_type}{desc}\n    ⎿  Done ({tool_info}{self._elapsed:.1f}s)\n  {self._result_preview}"
            )

    def on_click(self) -> None:
        if not self._done:
            return
        self._collapsed = not self._collapsed
        self._render_done()


_XHX_THEME = Theme(
    name="XHX",
    primary="#875FFF",
    background="#1a1a1a",
    surface="#1a1a1a",
    panel="#1a1a1a",
    dark=True,
)


class XHXApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "XHX"
    INLINE_PADDING = 0
    theme = "XHX"
    BINDINGS = [
        Binding("ctrl+c", "handle_ctrl_c", "Quit", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle mode", priority=True),
        Binding("ctrl+o", "toggle_tool_blocks", "Toggle tools", priority=True),
    ]

    def __init__(
        self,
        providers: list[ProviderConfig],
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        mcp_servers: list[MCPServerConfig] | None = None,
        hook_engine: HookEngine | None = None,
        enable_fork: bool = False,
        enable_verification_agent: bool = False,
        enable_verification_gate: bool = False,
        worktree_config: Any = None,
        teammate_mode: str = "",
        enable_coordinator_mode: bool = False,
        driver_class: type | None = None,
    ) -> None:
        super().__init__(driver_class=driver_class)
        self.providers = providers
        self._initial_permission_mode = permission_mode
        self._mcp_server_configs = mcp_servers or []
        self.hook_engine = hook_engine
        self._enable_fork = enable_fork
        self._enable_verification_agent = enable_verification_agent
        self._enable_verification_gate = enable_verification_gate
        self._worktree_config = worktree_config
        self._teammate_mode = teammate_mode
        self._enable_coordinator_mode = enable_coordinator_mode
        self.file_cache = FileCache()
        self.client: LLMClient | None = None
        self.conversation = ConversationManager()
        self.registry: ToolRegistry = create_default_registry(file_cache=self.file_cache)
        self.agent: Agent | None = None
        self.mcp_manager: MCPManager | None = None
        self._mcp_init_task: asyncio.Task[None] | None = None
        self._selected_provider: ProviderConfig | None = None
        self._streaming = False
        self._thinking_start: float = 0.0
        self._thinking_verb: str = ""
        self._spinner_idx: int = 0
        self._spinner_timer: Timer | None = None
        self._spinner_label: Static | None = None
        self._plan_pending: bool = False
        self._present_plan_tool: PresentPlanTool | None = None
        self._agent_task: asyncio.Task[None] | None = None
        self.session_manager: SessionManager | None = None
        self.session: Session | None = None
        self._session_saved_count: int = 0
        self.memory_manager: MemoryManager | None = None
        self._instructions_content: str = ""
        self.command_registry = CommandRegistry()
        # XHX 状态追踪
        self._xhx_tokens_total: int = 0
        self._xhx_tokens_prompt: int = 0
        self._xhx_tokens_completion: int = 0
        self._xhx_compaction_count: int = 0
        self._xhx_tool_count: int = 0
        self._xhx_last_model: str = ""
        self._xhx_last_duration_ms: int = 0
        self._xhx_context_used: int = 0
        self._xhx_context_budget: int = 0
        register_all_commands(self.command_registry)
        self.skill_loader: SkillLoader | None = None
        self.skill_executor: SkillExecutor | None = None
        self._load_skill_tool: LoadSkill | None = None
        self.agent_loader: AgentLoader | None = None
        self.task_manager: TaskManager = TaskManager()
        self.trace_manager: TraceManager = TraceManager()
        self._notification_check_task: asyncio.Task[None] | None = None
        self.worktree_manager: WorktreeManager | None = None
        self._stale_cleanup_task: asyncio.Task[None] | None = None
        self._current_streaming_label: Static | None = None
        self._current_ai_row: Vertical | None = None
        self._current_accumulated_text: str = ""
        self._mcp_instructions: str = ""
        self._mcp_connecting: bool = False
        self._teammate_tree: TeammateTree | None = None
        self._teammate_timer: Timer | None = None
        self._pending_askuser_event: AskUserEvent | None = None
        self._pending_perm_request: PermissionRequest | None = None

    @staticmethod
    def _make_banner(model: str = "", work_dir: str = "") -> RichText:
        t = RichText()
        # Line 1: stars + version
        t.append("      ★   ★    ", style="color(220)")
        t.append(f"XHX v{__version__}\n", style="bold color(99)")
        # Line 2: head top
        t.append('     \\_.-"C"-. ', style="bold color(99)")
        t.append("\n", style="")
        # Line 3: left ear + model
        t.append("  .-'        \\", style="bold color(99)")
        t.append(f"  {model}\n" if model else "\n", style="color(242)")
        # Line 4: face
        t.append(" /:::\\ ( ._. )", style="bold color(99)")
        t.append("\n", style="")
        # Line 5: body + work_dir
        t.append("|:::::| | | | |", style="bold color(99)")
        t.append(f"  {work_dir}\n" if work_dir else "\n", style="color(242)")
        # Line 6: body lower
        t.append(" \\:::/  | | | |", style="bold color(99)")
        t.append("\n", style="")
        # Line 7: tail
        t.append('  `"`   |_____|', style="bold color(99)")
        t.append("\n", style="")
        return t

    def compose(self) -> ComposeResult:
        yield Static(self._make_banner(), id="title-bar")

        if len(self.providers) > 1:
            with Vertical(id="provider-select"):
                yield Static("Select a Provider", id="select-label")
                yield OptionList(
                    *[Option(f"{p.name}  [{p.model}]", id=p.name) for p in self.providers],
                    id="provider-list",
                )
        yield VerticalScroll(id="chat-area")
        with Vertical(id="input-area"):
            yield ChatInput(id="chat-input")
            with Horizontal(id="status-bar"):
                yield Static("  default", id="mode-label")
                yield Static("", id="xhx-status")
                yield Static("", id="teammates-label")
                yield Static("", id="model-label")
            yield CompletionPopup()

    def on_mount(self) -> None:
        self.register_theme(_XHX_THEME)
        self.theme = "XHX"
        # AskUser 工具在 execute() 内阻塞 await future，主事件循环此时被挂起，
        # 无法靠流式分支检测其 _pending_event；用独立 interval 轮询来弹出询问框。
        self.set_interval(0.15, self._poll_askuser)
        if len(self.providers) == 1:
            self._select_provider(self.providers[0])
        else:
            self.query_one("#chat-area").display = False
            self.query_one("#input-area").display = False

    def _select_provider(self, provider: ProviderConfig) -> None:
        self._selected_provider = provider
        self._xhx_last_model = provider.model
        self._xhx_context_budget = provider.context_window
        try:
            from xhx_agent.models.routing import build_agent_client

            # 主 provider + .xhx/config.json 的 routing.fallback 链；无 fallback 时等价于单个 client。
            self.client = build_agent_client(Path.cwd(), provider)
        except AuthenticationError as e:
            self._show_error(str(e))
            return

        work_dir = str(Path.cwd())
        home = Path.home()
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(work_dir),
            rule_engine=RuleEngine(
                user_rules_path=home / ".xhx" / "permissions.json",
                project_rules_path=Path(work_dir) / ".xhx" / "permissions.json",
                local_rules_path=Path(work_dir) / ".xhx" / "permissions.local.yaml",
            ),
            mode=self._initial_permission_mode,
        )

        self._instructions_content = load_instructions(work_dir)
        self.memory_manager = MemoryManager(work_dir)
        self.session_manager = SessionManager(work_dir)
        self.session_manager.cleanup()
        self.session = self.session_manager.create()

        from xhx_agent.filehistory import FileHistory

        self.file_history = FileHistory(work_dir, self.session.session_id)
        for tool in self.registry.list_tools():
            if hasattr(tool, "file_history"):
                tool.file_history = self.file_history

        load_skill_tool = LoadSkill()
        self.registry.register(load_skill_tool)
        self._load_skill_tool = load_skill_tool

        self.registry.register(ToolSearchTool(self.registry, protocol=provider.protocol))
        self.registry.register(AskUserTool())

        from xhx_agent.tools.enter_plan_mode import EnterPlanModeTool
        from xhx_agent.tools.exit_plan_mode import ExitPlanModeTool
        from xhx_agent.tools.present_plan import PresentPlanTool

        self._exit_plan_tool = ExitPlanModeTool()
        self.registry.register(self._exit_plan_tool)

        # 进入 plan 模式是安全操作（仅收紧为只读）——模型可自主调用，无需审批。
        self._enter_plan_tool = EnterPlanModeTool(
            on_enter=lambda: self.set_plan_mode(True),
            is_plan_mode=lambda: self.agent is not None and self.agent.plan_mode,
        )
        self.registry.register(self._enter_plan_tool)

        # 找到 default factory 已注册的 PresentPlanTool 并注入回调
        self._present_plan_tool = None
        for tool in self.registry.list_tools():
            if isinstance(tool, PresentPlanTool):
                self._present_plan_tool = tool
                break

        self.agent = Agent(
            client=self.client,
            registry=self.registry,
            protocol=provider.protocol,
            work_dir=work_dir,
            permission_checker=checker,
            context_window=provider.get_context_window(),
            instructions_content=self._instructions_content,
            memory_manager=self.memory_manager,
            hook_engine=self.hook_engine,
        )
        self.agent.file_history = self.file_history
        self.agent.session_id = self.session.session_id
        self.agent.verification_gate = self._enable_verification_gate
        # 持久化证据链：交互会话按 session_id 落 `.xhx/traces/<session_id>.jsonl`，
        # `xhx replay <session_id>` 可回放。构造失败只降级为无 trace，不影响启动。
        try:
            from xhx_agent.evidence.store import EvidenceStore

            self.agent.trace_store = EvidenceStore(Path(work_dir), self.session.session_id)
        except Exception:
            pass
        # auto 分类器模型：配了 routing.roles["classify"] 的便宜 profile 就用它，没配走主模型。
        from xhx_agent.models.routing import build_role_client

        self.agent.classifier_client = build_role_client(Path.cwd(), "classify", provider.name)

        # 注入回调：两个工具共享同一个 _is_plan_mode / _plan_exists
        def _is_plan() -> bool:
            return self.agent is not None and self.agent.plan_mode

        def _exists() -> bool:
            return self.agent is not None and self.agent._get_plan_path().exists()

        self._exit_plan_tool._is_plan_mode = _is_plan
        self._exit_plan_tool._plan_exists = _exists
        if self._present_plan_tool is not None:
            self._present_plan_tool._is_plan_mode = _is_plan
            self._present_plan_tool._plan_exists = _exists

        # Layer 2: 在后台异步拉取模型的 context window，不阻塞启动流程。
        # agent 已经有一个同步解析的窗口值（来自配置 / 映射表 / 默认值）；
        # 如果异步拉取成功，就原地升级为更准确的值。
        self.run_worker(self._resolve_context_window(provider), exclusive=False)

        self.skill_loader = SkillLoader(Path(work_dir))
        self.skill_loader.load_all()

        load_skill_tool.set_loader(self.skill_loader)
        load_skill_tool.set_agent(self.agent)

        self.skill_executor = SkillExecutor(
            agent=self.agent,
            client=self.client,
            protocol=provider.protocol,
        )

        catalog = self.skill_loader.get_catalog()
        if catalog:
            lines = [
                "You can use the following Skills:",
                "",
            ]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append("If the user's request matches a Skill, call LoadSkill to activate it.")
            self.agent.set_skill_catalog("\n".join(lines))

        register_skill_commands(self.command_registry, self.skill_loader, self.skill_executor)

        # --- Worktree 系统初始化 ---
        from xhx_agent.config import WorktreeConfig

        wt_cfg = self._worktree_config or WorktreeConfig()
        self.worktree_manager = WorktreeManager(
            repo_root=work_dir,
            symlink_directories=wt_cfg.symlink_directories,
        )
        restored = self.worktree_manager.restore_session()
        if restored:
            self.agent.work_dir = restored.worktree_path

        wt_command = create_worktree_command(self.worktree_manager)
        self.command_registry.register_sync(wt_command)

        from xhx_agent.tools.enter_worktree import EnterWorktreeTool
        from xhx_agent.tools.exit_worktree import ExitWorktreeTool

        self.registry.register(EnterWorktreeTool(worktree_manager=self.worktree_manager))
        self.registry.register(ExitWorktreeTool(worktree_manager=self.worktree_manager))

        self._stale_cleanup_task = asyncio.create_task(
            start_stale_cleanup_task(
                self.worktree_manager,
                wt_cfg.stale_cleanup_interval,
                wt_cfg.stale_cutoff_hours,
            )
        )

        # --- 子 agent 系统初始化 ---
        self.agent_loader = AgentLoader(work_dir, enable_verification=self._enable_verification_agent)
        self.agent_loader.load_all()

        # --- Agent 团队系统初始化 ---
        from xhx_agent.teams.manager import TeamManager
        from xhx_agent.tools.team_create import TeamCreateTool
        from xhx_agent.tools.team_delete import TeamDeleteTool

        self.team_manager = TeamManager(worktree_manager=self.worktree_manager, trace_manager=self.trace_manager)

        agent_tool = AgentTool(
            agent_loader=self.agent_loader,
            task_manager=self.task_manager,
            trace_manager=self.trace_manager,
            parent_agent=self.agent,
            enable_fork=self._enable_fork,
            provider_config=provider,
            worktree_manager=self.worktree_manager,
            team_manager=self.team_manager,
        )
        self.registry.register(agent_tool)

        team_create_tool = TeamCreateTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
            teammate_mode=self._teammate_mode,
            is_interactive=True,
            enable_coordinator_mode=self._enable_coordinator_mode,
        )
        self.registry.register(team_create_tool)

        team_delete_tool = TeamDeleteTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
        )
        self.registry.register(team_delete_tool)

        agent_catalog = self.agent_loader.list_agents()
        if agent_catalog:
            lines = [
                "## Available Sub-Agent Types",
                "",
                "Use the Agent tool with subagent_type parameter to delegate tasks:",
                "",
            ]
            for agent_type, when_to_use in agent_catalog:
                lines.append(f"- **{agent_type}**: {when_to_use}")
            if self._enable_fork:
                lines.append("")
                lines.append(
                    "Leave subagent_type empty to fork the current conversation (inherits full dialog history)."
                )
            lines.append("")
            lines.append(
                "IMPORTANT: Sub-agents run in the background. "
                "After calling the Agent tool, you will get a task ID immediately. "
                "Do NOT wait, sleep, or poll for the result. "
                "Simply report the task ID to the user and end your turn. "
                "The system will automatically notify when the task completes."
            )
            self.agent.set_agent_catalog("\n".join(lines), catalog_list=agent_catalog)

        tasks_cmd = create_tasks_command(self.task_manager)
        self.command_registry.register_sync(tasks_cmd)

        from xhx_agent.commands.handlers.trace import create_trace_command

        trace_cmd = create_trace_command(self.trace_manager, self.agent.agent_id)
        self.command_registry.register_sync(trace_cmd)

        # --- 协调者模式初始化（工具已注册，激活推迟到 TeamCreate 时） ---
        from xhx_agent.tools.synthetic_output import SyntheticOutputTool

        self.registry.register(SyntheticOutputTool())
        self.agent._team_manager = self.team_manager

        if self.hook_engine:
            asyncio.ensure_future(self.hook_engine.run_hooks("startup", HookContext(event_name="startup")))

        if self._mcp_server_configs:
            self._mcp_init_task = asyncio.create_task(self._init_mcp())

        self.query_one("#model-label", Static).update(provider.model)
        work_dir = os.getcwd()
        self.query_one("#title-bar", Static).update(self._make_banner(provider.model, work_dir))
        self._update_mode_label()

        select = self.query("#provider-select")
        if select:
            select.first().display = False
        self.query_one("#chat-area").display = True
        self.query_one("#input-area").display = True
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.placeholder = "Send a message..."
        chat_input.load_history(work_dir)
        chat_input.focus()

        self._notification_check_task = asyncio.create_task(self._start_notification_polling())

    async def _resolve_context_window(self, provider: ProviderConfig) -> None:
        """Layer 2 后台 worker：异步拉取模型的 context window，
        拉到就原地升级 agent 的窗口值。

        尽力而为 — resolve_context_window 不会抛异常；如果拉不到，
        agent 继续使用同步解析得到的窗口值。
        """
        await resolve_context_window(provider)
        if self.agent is not None:
            self.agent.context_window = provider.get_context_window()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "provider-list":
            provider = self.providers[event.option_index]
            self._select_provider(provider)

    # -----------------------------------------------------------------
    # UIController 协议实现
    # -----------------------------------------------------------------

    def add_system_message(self, text: str) -> None:
        self._show_system_message(text)

    def send_user_message(self, text: str) -> None:
        if self._streaming or self.agent is None:
            return
        self._agent_task = asyncio.create_task(self._send_message(text))

    def set_plan_mode(self, enabled: bool) -> None:
        if self.agent is None:
            return
        if enabled:
            self._pre_plan_mode = self.agent.permission_mode
            self.agent.set_permission_mode(PermissionMode.PLAN)
        else:
            restore = getattr(self, "_pre_plan_mode", PermissionMode.DEFAULT)
            self.agent.set_permission_mode(restore)
        self._update_mode_label()

    def get_token_count(self) -> tuple[int, int]:
        if self.agent:
            return self.agent.total_input_tokens, self.agent.total_output_tokens
        return 0, 0

    def refresh_status(self) -> None:
        self._update_mode_label()

    # -----------------------------------------------------------------
    # 命令分发
    # -----------------------------------------------------------------

    def _build_command_context(self, args: str) -> CommandContext:
        return CommandContext(
            args=args,
            agent=self.agent,
            conversation=self.conversation,
            session=self.session,
            session_manager=self.session_manager,
            memory_manager=self.memory_manager,
            ui=self,
            config={
                "registry": self.command_registry,
                "set_session": self._set_session,
                "set_conversation": self._set_conversation,
                "clear_chat": self._clear_chat,
                "render_restored": self._render_restored_messages,
                "file_history": getattr(self, "file_history", None),
                "skill_loader": self.skill_loader,
                "skill_executor": self.skill_executor,
            },
        )

    def _set_session(self, session: Session | None) -> None:
        # /new 与 /session new 传 None 表示"开新会话"：这里就地新建一个空 Session，
        # 而不是把 self.session 置空。置空会让随后的 _send_message 无处落盘，新会话也
        # 无法被 /session resume 找回；新建后把已落盘计数清零，避免沿用旧会话的游标。
        if session is None and self.session_manager is not None:
            session = self.session_manager.create()
            self._session_saved_count = 0
        self.session = session
        if self.agent:
            self.agent.session_id = session.session_id if session is not None else ""

    def _persist_compact_boundary(self, notification: CompactNotification) -> None:
        """Layer-2 compact 后写入 compact_boundary 记录。

        将摘要 + 原样保留的尾部内联到一条记录中，resume 时只需这一条
        就能重建压缩后的状态。之前已写入磁盘的原始前缀不会被重放。
        没有活跃 session 或 compact 未产出 boundary 时直接跳过。
        """
        if not self.session or notification.boundary is None:
            return
        record = make_compact_boundary(
            notification.boundary.summary,
            notification.boundary.keep,
        )
        self.session.append_record(record)

    def _set_conversation(self, conv: ConversationManager | None) -> None:
        # /new 传 None 表示清空对话——新建一个空 ConversationManager 以维持不变量
        # （self.conversation 永远是有效对象）。否则 _send_message 里的
        # self.conversation.add_user_message(...) 会因 None 抛 AttributeError：既不回复，
        # 又把 _streaming 卡在 True，导致下一条消息只显示 "(response interrupted)"。
        self.conversation = conv if conv is not None else ConversationManager()

    def _clear_chat(self) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        chat.remove_children()

    async def _dispatch_command(self, text: str) -> None:
        name, args, is_command = parse_command(text)

        if not is_command:
            if self._streaming or self.agent is None:
                return
            self._agent_task = asyncio.create_task(self._send_message(text))
            return

        if name == "":
            commands = self.command_registry.list_commands()
            lines = ["可用命令："]
            for cmd in commands:
                aliases_str = ", ".join(f"/{a}" for a in cmd.aliases)
                name_part = f"/{cmd.name}"
                if aliases_str:
                    name_part += f", {aliases_str}"
                lines.append(f"  {name_part:<24} {cmd.description}")
            self._show_system_message("\n".join(lines))
            return

        cmd = self.command_registry.find(name)
        if cmd is None:
            self._show_system_message(f"未知命令：/{name}，输入 /help 查看可用命令")
            return

        if not args and cmd.arg_prompt:
            self._show_system_message(cmd.arg_prompt)
            return

        if cmd.handler is None:
            self._show_system_message(f"命令 /{name} 暂未实现")
            return

        ctx = self._build_command_context(args)
        try:
            await cmd.handler(ctx)
        except Exception as e:
            self._show_error(f"命令执行失败: {e}")

    # -----------------------------------------------------------------
    # 输入处理
    # -----------------------------------------------------------------

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.text.strip()
        if self._streaming and not text.startswith("/"):
            if self._agent_task and not self._agent_task.done():
                # 设置取消原因为 'interrupt'，让工具层（Bash 等）知道
                # 是用户主动中断，不应杀子进程。
                from xhx_agent.agent import cancel_reason

                cancel_reason.set("interrupt")
                self._agent_task.cancel()
                try:
                    await self._agent_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._finish_streaming()
            self._show_system_message("(response interrupted)")
        await self._dispatch_command(text)

    def on_chat_input_tab_complete(self, event: ChatInput.TabComplete) -> None:
        matches = complete(event.text, self.command_registry)
        if not matches:
            return
        popup = self.query_one(CompletionPopup)
        if len(matches) == 1:
            input_widget = self.query_one("#chat-input", ChatInput)
            input_widget.clear()
            input_widget.insert(matches[0][1] + " ")
        else:
            popup.show_items([display for display, _value in matches])

    def on_chat_input_slash_menu_update(self, event: ChatInput.SlashMenuUpdate) -> None:
        popup = self.query_one(CompletionPopup)
        if event.prefix is None:
            popup.hide()
            return
        matches = complete(event.prefix, self.command_registry)
        if not matches:
            popup.hide()
            return
        popup.show_items([display for display, _value in matches])

    def on_chat_input_at_file_request(self, event: ChatInput.AtFileRequest) -> None:
        work_dir = self.agent.work_dir if self.agent else os.getcwd()
        matches = scan_files_for_at(event.prefix, work_dir)
        if matches:
            popup = self.query_one(CompletionPopup)
            popup.show_items([f"@{m}" for m in matches])

    def action_cycle_mode(self) -> None:
        if self.agent is None:
            return
        current = self.agent.permission_mode
        try:
            idx = _MODE_CYCLE.index(current)
        except ValueError:
            idx = 0
        next_mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
        self.agent.set_permission_mode(next_mode)
        self._update_mode_label()

    def action_toggle_tool_blocks(self) -> None:
        for block in self.query(ToolCallBlock):
            if block._loading:
                continue
            block._collapsed = not block._collapsed
            if block._collapsed:
                block._render_collapsed()
            else:
                block._render_expanded()

        for summary in self.query(ToolGroupSummary):
            summary.toggle()
            parent = summary.parent
            if parent:
                for child in parent.children:
                    if isinstance(child, ToolCallBlock) and child.tool_name in COLLAPSIBLE_TOOLS:
                        child.display = summary._expanded

        for sa_block in self.query(SubAgentBlock):
            if not isinstance(sa_block, SubAgentBlock):
                continue
            if sa_block._done:
                sa_block._collapsed = not sa_block._collapsed
                sa_block._render_done()

    def action_cancel(self) -> None:
        popup = self.query_one(CompletionPopup)
        if popup.is_visible:
            popup.hide()
            self.query_one("#chat-input", ChatInput).focus()
            return
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()

    async def _prefetch_relevant_memories(self, query: str) -> str:
        """同步召回相关记忆，用线程防阻塞事件循环。失败返回 ""。"""
        if self.agent is None:
            return ""

        try:
            results = await asyncio.to_thread(
                find_relevant_memories,
                self.agent.work_dir,
                query,
                5,
            )
            return render_reminder(results)
        except TimeoutError:
            return ""

    async def _send_message(self, text: str, is_notification: bool = False) -> None:
        assert self.agent is not None

        if self._mcp_init_task and not self._mcp_init_task.done():
            self._show_system_message("Waiting for MCP servers to connect...")
            await self._mcp_init_task

        self._streaming = True
        chat = self.query_one("#chat-area", VerticalScroll)
        input_widget = self.query_one("#chat-input", ChatInput)

        if text and "@" in text:
            text = expand_at_refs(text, self.agent.work_dir)

        # Start memory recall prefetch before UI work.
        prefetch_task = asyncio.create_task(self._prefetch_relevant_memories(text)) if text else None

        if text:
            user_row = Vertical(classes="user-row")
            await chat.mount(user_row)
            from rich.text import Text as RichText

            user_rich = RichText()
            user_rich.append("❯ ", style="bold color(80)")
            user_rich.append(text, style="bold color(255)")
            user_bubble = Static(user_rich, classes="message user-message")
            await user_row.mount(user_bubble)
            self.call_after_refresh(chat.scroll_end, animate=False)

            self.conversation.add_user_message(text)
            if self.session:
                self.session.append(Message(role="user", content=text))
                self._session_saved_count += 1

        # MCP server 说明按"当前对话里是否已有"注入（自愈式）：/new、恢复会话、
        # 手动或自动压缩都会换掉/清掉历史，一次性标志会让说明永久丢失。
        if self._mcp_instructions and not _has_mcp_instructions(self.conversation):
            self.conversation.add_system_reminder(self._mcp_instructions)

        # Collect prefetched recall with 3s timeout, inject as system-reminder.
        if prefetch_task is not None:
            try:
                reminder = await asyncio.wait_for(prefetch_task, timeout=3.0)
                if reminder:
                    self.conversation.add_system_reminder(reminder)
            except (TimeoutError, Exception):
                pass

        history_cursor = len(self.conversation.history)

        # 准备 AI 回复区域
        ai_row = Vertical(classes="ai-row")
        await chat.mount(ai_row)
        _initial_label = Static("", classes="message ai-message")
        await ai_row.mount(_initial_label)
        streaming_label: Static | None = _initial_label

        accumulated_text = ""
        tool_blocks: dict[str, ToolCallBlock | SubAgentBlock] = {}

        # 在聊天区底部启动持续旋转的加载动画
        self._thinking_start = _time.monotonic()
        self._thinking_verb = random.choice(THINKING_VERBS)
        self._spinner_idx = 0
        self._spinner_label = Static(
            f"  {SPINNER_FRAMES[0]} {self._thinking_verb}…",
            id="spinner-live",
        )
        await chat.mount(self._spinner_label)

        # Mount teammate tree (initially hidden) below the spinner
        self._teammate_tree = TeammateTree(id="teammate-tree")
        self._teammate_tree.display = False
        await chat.mount(self._teammate_tree)
        self._start_teammate_polling()

        self.call_after_refresh(chat.scroll_end, animate=False)
        self._start_spinner()

        await asyncio.sleep(0)

        try:
            async for event in self.agent.run(self.conversation):
                if isinstance(event, ThinkingText):
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, StreamText):
                    if streaming_label is None or not accumulated_text:
                        if streaming_label is not None:
                            await streaming_label.remove()
                        streaming_label = Static("", classes="message ai-message")
                        await ai_row.mount(streaming_label)
                    accumulated_text += event.text
                    from rich.text import Text as RichText

                    t = RichText()
                    t.append("● ", style="bold color(99)")
                    t.append(strip_emoji(strip_system_reminder(accumulated_text)))
                    streaming_label.update(t)
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, RetryEvent):
                    self._show_system_message(f"↻ Retrying: {event.reason}")

                elif isinstance(event, ToolUseEvent):
                    if accumulated_text:
                        if streaming_label is not None:
                            await streaming_label.remove()
                        from rich.text import Text as RichText

                        prefix = Static(RichText("●  ", style="bold color(99)"), classes="message")
                        await ai_row.mount(prefix)
                        md = Markdown(
                            strip_emoji(strip_system_reminder(accumulated_text)), classes="message ai-message"
                        )
                        await ai_row.mount(md)
                        streaming_label = None
                        accumulated_text = ""
                    elif streaming_label is not None:
                        await streaming_label.remove()
                        streaming_label = None

                    block: ToolCallBlock | SubAgentBlock
                    if _is_subagent_tool(event.tool_name):
                        agent_type = event.arguments.get("subagent_type", "")
                        desc = event.arguments.get("description", "")
                        block = SubAgentBlock(
                            agent_type or "agent",
                            desc,
                            classes="tool-block subagent-block",
                        )
                    else:
                        block = ToolCallBlock(event.tool_name, event.arguments, classes="tool-block")
                    await ai_row.mount(block)
                    tool_blocks[event.tool_id] = block
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, PermissionRequest):
                    await self._handle_permission_request(event)

                elif isinstance(event, ToolResultEvent):
                    self._xhx_tool_count += 1
                    self.call_later(self._update_xhx_status)
                    result_block = tool_blocks.get(event.tool_id)
                    if result_block:
                        result_block.set_result(event.output, event.is_error, event.elapsed)
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, TurnComplete):
                    # 每轮都按当前对话本地估算 context 占用——不依赖 API 是否回传 usage
                    # （deepseek 流末常不带 usage chunk，纯靠 API token 会一直显示 0）。
                    self._recompute_context_used()
                    self.call_later(self._update_xhx_status)
                    if self.session:
                        for msg in self.conversation.history[history_cursor:]:
                            self.session.append(msg)
                        self._session_saved_count = len(self.conversation.history)
                        history_cursor = len(self.conversation.history)

                    collapsible = [
                        (tid, blk)
                        for tid, blk in tool_blocks.items()
                        if isinstance(blk, ToolCallBlock) and blk.tool_name in COLLAPSIBLE_TOOLS and not blk._loading
                    ]
                    if len(collapsible) >= 2:
                        total_elapsed = sum(b._elapsed for _, b in collapsible)
                        summary = ToolGroupSummary(
                            len(collapsible),
                            total_elapsed,
                            classes="tool-block tool-group-summary",
                        )
                        for _, blk in collapsible:
                            blk.display = False
                        await ai_row.mount(summary)

                    tool_blocks.clear()
                    ai_row = Vertical(classes="ai-row")
                    await chat.mount(ai_row)
                    streaming_label = Static("", classes="message ai-message")
                    await ai_row.mount(streaming_label)
                    accumulated_text = ""
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, UsageEvent):
                    self._xhx_tokens_prompt = event.input_tokens
                    self._xhx_tokens_completion = event.output_tokens
                    self._xhx_tokens_total += event.input_tokens + event.output_tokens
                    # context 占用由 _recompute_context_used()（current_tokens）统一算，
                    # 不用累计总量覆盖——后者会无限增长、与"窗口占用"语义不符。
                    if self._selected_provider:
                        self._xhx_last_model = self._selected_provider.model
                        self._xhx_context_budget = self._selected_provider.context_window
                    self.call_later(self._update_xhx_status)

                elif isinstance(event, HookEvent):
                    status = "✓" if event.success else "✗"
                    self._show_system_message(f"Hook [{event.hook_id}] {status} {event.output}")

                elif isinstance(event, CompactNotification):
                    self._show_system_message(event.message)
                    self._xhx_compaction_count += 1
                    self.call_later(self._update_xhx_status)
                    self._persist_compact_boundary(event)
                    history_cursor = len(self.conversation.history)

                elif isinstance(event, ErrorEvent):
                    self._show_error(event.message)

                elif isinstance(event, LoopComplete):
                    total_time = _time.monotonic() - self._thinking_start
                    done_label = Static(
                        f"✻ {_to_past_tense(self._thinking_verb)} for {total_time:.1f}s",
                        classes="message thinking-done",
                    )
                    await ai_row.mount(done_label)
                    if self.session:
                        for msg in self.conversation.history[history_cursor:]:
                            self.session.append(msg)
                        self._session_saved_count = len(self.conversation.history)
                        history_cursor = len(self.conversation.history)
                        self.session.meta.total_tokens = self.agent.total_input_tokens + self.agent.total_output_tokens
                        asyncio.ensure_future(self._update_session_summary())
                    # 模型调用 ExitPlanMode / present_plan 即弹审批——present_plan 任何模式可用，
                    # 故不再要求"必须在 plan 模式"。实际弹出推迟到 _send_message 收尾 input.focus()
                    # 之后，避免焦点被抢。
                    exit_requested = getattr(self._exit_plan_tool, "_exit_requested", False)
                    pp_requested = getattr(self, "_present_plan_tool", None) is not None and getattr(
                        self._present_plan_tool, "_exit_requested", False
                    )
                    if exit_requested or pp_requested:
                        self._plan_pending = True
                        self._exit_plan_tool._exit_requested = False
                        if self._present_plan_tool is not None:
                            self._present_plan_tool._exit_requested = False

            # 收尾：渲染剩余的累积文本
            if accumulated_text and streaming_label is not None:
                await streaming_label.remove()
                md = Markdown(strip_emoji(strip_system_reminder(accumulated_text)), classes="message ai-message")
                await ai_row.mount(md)
            elif streaming_label is not None:
                await streaming_label.remove()

            self.call_after_refresh(chat.scroll_end, animate=False)

        except asyncio.CancelledError:
            if accumulated_text:
                if streaming_label is not None:
                    await streaming_label.remove()
                md = Markdown(
                    accumulated_text + "\n\n*[cancelled]*",
                    classes="message ai-message",
                )
                await ai_row.mount(md)
            self._show_system_message("Operation cancelled")
        except LLMError as e:
            self._show_error(str(e))
        except Exception as e:
            # 兜底：任何未预期异常都要显式呈现，绝不让 agent 静默无反应。
            import logging
            import traceback

            logging.getLogger("XHX").error("Agent run failed: %s\n%s", e, traceback.format_exc())
            self._show_error(f"{type(e).__name__}: {e}")
        finally:
            # 对标 Claude Code：每条消息实时持久化，不依赖 TurnComplete。
            # finally 保证即使是取消、异常、中断，已写入 conversation 的消息都会落盘。
            if self.session:
                try:
                    unsaved = self.conversation.history[self._session_saved_count :]
                    for msg in unsaved:
                        self.session.append(msg)
                    self._session_saved_count = len(self.conversation.history)
                except Exception:
                    pass
            self._finish_streaming()
            input_widget.focus()

            await self._process_task_notifications()

        # 收尾之后再弹 plan 审批：此时 input.focus() 已执行，审批控件能稳拿到焦点。
        if getattr(self, "_plan_pending", False):
            self._plan_pending = False
            await self._show_plan_approval()

    async def _process_task_notifications(self) -> None:
        completed = self.task_manager.poll_completed()
        if not completed or self.agent is None:
            return

        inject_task_notifications(self.conversation, completed)

        for task in completed:
            status_icon = "✓" if task.status == "completed" else "✗"
            self._show_system_message(f"{status_icon} 后台任务完成: [{task.id}] {task.name} — {task.status}")

            if hasattr(self, "team_manager"):
                self.team_manager.on_teammate_completed(task.agent.agent_id)

        self._agent_task = asyncio.create_task(self._send_message("", is_notification=True))

    async def _start_notification_polling(self) -> None:
        while True:
            await asyncio.sleep(2)
            if not self._streaming and self.agent is not None:
                await self._process_task_notifications()
                await self._process_mailbox_notifications()

    async def _process_mailbox_notifications(self) -> None:
        if not hasattr(self, "team_manager") or self.team_manager is None:
            return
        if self._streaming or self.agent is None:
            return
        notes = self.team_manager.drain_lead_mailbox()
        if not notes:
            return
        for note in notes:
            self.conversation.add_system_reminder(f"[{note.from_agent}] {note.summary or note.content}")
        self._agent_task = asyncio.create_task(self._send_message("", is_notification=True))

    async def _show_plan_approval(self) -> None:
        from xhx_agent.plan_dialog import InlinePlanWidget

        # 优先用 present_plan 随参数带来的方案正文；否则回退读 plan 文件（ExitPlanMode 路径）。
        plan_text = ""
        pp = self._present_plan_tool
        if pp is not None and pp._plan_text:
            plan_text = pp._plan_text.strip()
        if not plan_text and self.agent is not None:
            plan_path = self.agent._get_plan_path()
            if plan_path.exists():
                try:
                    plan_text = plan_path.read_text(encoding="utf-8").strip()
                except OSError:
                    pass

        chat = self.query_one("#chat-area", VerticalScroll)
        # 防御：旧 plan 弹窗 remove() 延迟生效，先 await 移除避免重复 ID 崩溃。
        await chat.query("#plan-inline").remove()
        # 方案正文用 Markdown 控件渲染（标题/表格/代码块/列表都解析），而非 Static 原文。
        if plan_text:
            await chat.mount(Markdown(strip_emoji(plan_text), classes="message ai-message"))
        widget = InlinePlanWidget()
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_plan_widget_responded(self, event: InlinePlanWidget.Responded) -> None:
        from xhx_agent.plan_dialog import InlinePlanWidget, PlanChoice

        try:
            self.query_one("#plan-inline", InlinePlanWidget).remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

        if self.agent is None:
            return

        choice = event.choice
        feedback = event.feedback
        # 优先用 present_plan 带来的方案正文；否则回退 plan 文件（ExitPlanMode 路径）。
        plan_content = ""
        pp = self._present_plan_tool
        if pp is not None and pp._plan_text:
            plan_content = pp._plan_text
        if not plan_content:
            plan_path = self.agent._get_plan_path()
            if plan_path.exists():
                try:
                    plan_content = plan_path.read_text(encoding="utf-8")
                except Exception:
                    pass

        pre = getattr(self, "_pre_plan_mode", PermissionMode.DEFAULT)
        if choice == PlanChoice.YOLO:
            self.agent.set_permission_mode(PermissionMode.BYPASS)
            self._update_mode_label()
            if plan_content:
                self.send_user_message(f"Execute this plan:\n\n{plan_content}")
        elif choice == PlanChoice.MANUAL:
            self.agent.set_permission_mode(pre)
            self._update_mode_label()
            if plan_content:
                self.send_user_message(f"Execute this plan:\n\n{plan_content}")
        elif choice == PlanChoice.FEEDBACK:
            if feedback:
                self.send_user_message(feedback)
            else:
                self._show_system_message("Type your feedback and send.")

    async def _poll_askuser(self) -> None:
        """轮询 AskUser 工具的待处理事件并弹出询问框。

        工具 execute() 内阻塞 await future，流式主循环此刻被挂起，故无法在事件分支里
        检测；此处由 set_interval 独立驱动。仅在未在显示、且 future 未完成时弹框。
        """
        if self._pending_askuser_event is not None:
            return
        registry = getattr(self, "registry", None)
        if registry is None:
            return
        ask_tool = registry.get("AskUserQuestion")
        if not isinstance(ask_tool, AskUserTool):
            return
        ev = ask_tool._pending_event
        if ev is not None and not ev.future.done():
            await self._handle_askuser(ev)

    async def _handle_askuser(self, event: AskUserEvent) -> None:
        from xhx_agent.askuser_dialog import InlineAskUserWidget

        if self._pending_askuser_event is not None or self.query("#askuser-inline"):
            return
        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlineAskUserWidget(event.questions)
        self._pending_askuser_event = event
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_ask_user_widget_responded(self, event: InlineAskUserWidget.Responded) -> None:
        from xhx_agent.askuser_dialog import InlineAskUserWidget

        req = getattr(self, "_pending_askuser_event", None)
        if req is not None and not req.future.done():
            req.future.set_result(event.answers if event.answers else {})
            self._pending_askuser_event = None
        try:
            self.query_one("#askuser-inline", InlineAskUserWidget).remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    def _start_spinner(self) -> None:
        """启动 braille spinner 动画（每帧 80ms）。"""
        if self._spinner_timer is not None:
            return
        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _stop_spinner(self) -> None:
        """停止 spinner 动画。"""
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _finish_streaming(self) -> None:
        """清理所有 streaming 状态（取消或完成时调用）。"""
        self._streaming = False
        self._stop_spinner()
        self._stop_teammate_polling()
        self._agent_task = None
        if self._teammate_tree is not None:
            self._teammate_tree.remove()
            self._teammate_tree = None
        if self._spinner_label is not None:
            self._spinner_label.remove()
            self._spinner_label = None

    def _tick_spinner(self) -> None:
        """推进持久 spinner 标签上的动画帧。"""
        self._spinner_idx += 1
        frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        elapsed = _time.monotonic() - self._thinking_start
        if self._spinner_label is not None:
            self._spinner_label.update(f"  {frame} {self._thinking_verb}…  ({elapsed:.0f}s)")
            if self._spinner_idx % 5 == 0:
                try:
                    self.query_one("#chat-area", VerticalScroll).scroll_end(animate=False)
                except Exception:
                    pass

    def _start_teammate_polling(self) -> None:
        """Start polling teammate progress every 0.5s."""
        if self._teammate_timer is not None:
            return
        self._teammate_timer = self.set_interval(0.5, self._tick_teammate_tree)

    def _stop_teammate_polling(self) -> None:
        """Stop the teammate progress polling timer."""
        if self._teammate_timer is not None:
            self._teammate_timer.stop()
            self._teammate_timer = None

    def _tick_teammate_tree(self) -> None:
        """Poll team_manager for teammate progress and update the tree widget."""
        if not hasattr(self, "team_manager") or self.team_manager is None:
            return
        if self._teammate_tree is None:
            return

        progress_list = self.team_manager.get_all_teammate_progress()

        if not progress_list:
            self._teammate_tree.display = False
            self._update_teammates_label(0)
            return

        # Update the reactive properties via mutate_reactive for list
        self._teammate_tree.teammates = list(progress_list)

        # Update leader tokens from main agent
        if self.agent:
            self._teammate_tree.leader_tokens = self.agent.total_input_tokens + self.agent.total_output_tokens

        self._teammate_tree.display = True
        active_count = sum(1 for p in progress_list if p.status == "running")
        self._update_teammates_label(active_count)

    def _update_teammates_label(self, count: int) -> None:
        """Update the teammates count in the status bar."""
        try:
            label = self.query_one("#teammates-label", Static)
            if count > 0:
                label.update(f"[cyan]● {count} teammate{'s' if count != 1 else ''}[/cyan]  ")
            else:
                label.update("")
        except Exception:
            pass

    async def _handle_permission_request(self, request: PermissionRequest) -> None:
        from xhx_agent.permission_dialog import InlinePermissionWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        # 防御：上一个权限弹窗的 remove() 是延迟的（Textual 下个刷新才真正移除）；
        # 连续两个工具都要审批时，旧组件可能还在 DOM 里 → 重复 ID 崩溃。先 await 移除。
        await chat.query("#perm-inline").remove()
        widget = InlinePermissionWidget(request.tool_name, request.description)
        self._pending_perm_request = request
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        # 权限提示弹窗期间禁用输入框
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_permission_widget_responded(self, event: InlinePermissionWidget.Responded) -> None:
        from xhx_agent.permission_dialog import InlinePermissionWidget

        req = getattr(self, "_pending_perm_request", None)
        if req is not None:
            req.future.set_result(event.response)
            self._pending_perm_request = None
        # 从聊天区移除权限弹窗组件
        try:
            widget = self.query_one("#perm-inline", InlinePermissionWidget)
            widget.remove()
        except Exception:
            pass
        # 重新启用输入框
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    # -----------------------------------------------------------------
    # 会话恢复（上下键可选列表）
    # -----------------------------------------------------------------

    async def show_resume_picker(self) -> None:
        """弹出可上下键选择的历史会话列表（供 /session 调用）。"""
        from xhx_agent.tui.session_dialog import InlineResumeWidget

        if self.session_manager is None:
            self._show_system_message("会话管理器未初始化")
            return
        sessions = self.session_manager.list_sessions()
        # 排除当前这个（通常是空的）新会话
        if self.session is not None:
            sessions = [s for s in sessions if s.session_id != self.session.session_id]
        if not sessions:
            self._show_system_message("暂无可恢复的历史会话")
            return

        chat = self.query_one("#chat-area", VerticalScroll)
        # 防御：旧 resume 弹窗 remove() 延迟生效，先 await 移除避免重复 ID 崩溃。
        await chat.query("#resume-inline").remove()
        project = Path(self.agent.work_dir).name if self.agent is not None else ""
        widget = InlineResumeWidget(sessions, project_name=project)
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    async def on_inline_resume_widget_selected(self, event: Any) -> None:
        from xhx_agent.tui.session_dialog import InlineResumeWidget

        try:
            self.query_one("#resume-inline", InlineResumeWidget).remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass
        if event.session_id is None:
            return
        await self._load_session(event.session_id)

    async def _load_session(self, session_id: str) -> None:
        """把选中的历史会话读回当前对话并续写。"""
        if self.session_manager is None or self.agent is None:
            return
        messages = self.session_manager.load_messages(session_id)
        conv = ConversationManager()
        conv.history = list(messages)
        self._set_conversation(conv)
        # open() 续写同一个 jsonl；_session_saved_count 设为已加载条数，避免重复落盘。
        self._set_session(self.session_manager.open(session_id))
        self._session_saved_count = len(messages)
        await self._render_restored_messages(messages)
        self._recompute_context_used()
        self.call_later(self._update_xhx_status)
        self._show_system_message(f"已恢复会话（{len(messages)} 条消息）")

    # -----------------------------------------------------------------
    # 恢复 session 的消息渲染
    # -----------------------------------------------------------------

    async def _render_restored_messages(self, messages: list[Message]) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        await chat.remove_children()

        for msg in messages:
            if msg.tool_results or not msg.content:
                continue
            if msg.role == "user":
                row = Vertical(classes="user-row")
                await chat.mount(row)
                user_rich = RichText()
                user_rich.append("❯ ", style="bold color(80)")
                user_rich.append(msg.content, style="bold color(255)")
                bubble = Static(user_rich, classes="message user-message")
                await row.mount(bubble)
            elif msg.role == "assistant":
                row = Vertical(classes="ai-row")
                await chat.mount(row)
                md = Markdown(msg.content, classes="message ai-message")
                await row.mount(md)

        self.call_after_refresh(chat.scroll_end, animate=False)

    # -----------------------------------------------------------------
    # Session 摘要（异步后台生成）
    # -----------------------------------------------------------------

    async def _update_session_summary(self) -> None:
        if not self.session or not self.client or not self.agent:
            return
        try:
            summary = await generate_session_summary(self.client, self.conversation, self.agent.protocol)
            if summary:
                self.session.meta.summary = summary
                self.session.meta.save(self.session._sessions_dir / f"{self.session.session_id}.meta")
        except Exception:
            pass

    # -----------------------------------------------------------------
    # MCP
    # -----------------------------------------------------------------

    async def _init_mcp(self) -> None:
        self._mcp_connecting = True
        self._update_mode_label()
        manager = MCPManager()
        # 在后台线程中同步执行连接（connect_all 内部用 anyio portal）；
        # 单 server 失败明细记在 manager.failed_servers，回到事件循环后统一上报。
        await asyncio.to_thread(
            manager.connect_all,
            self._mcp_server_configs,
            None,  # on_error
        )
        tools_before = len(self.registry.list_tools())
        manager.register_tools_to_registry(self.registry)
        self.mcp_manager = manager
        self._mcp_connecting = False
        self._update_mode_label()
        tools_after = len(self.registry.list_tools())
        mcp_tools = tools_after - tools_before
        server_count = len(getattr(manager, "_sessions", {}))
        failed = getattr(manager, "failed_servers", {})
        if failed:
            from rich.markup import escape

            lines = [f"    {escape(name)}: {escape(err)}" for name, err in sorted(failed.items())]
            self.add_system_message("⚠ MCP server 连接失败（工具不可用）：\n" + "\n".join(lines))
        if server_count > 0 and mcp_tools > 0:
            parts = []
            for cfg in self._mcp_server_configs:
                srv_name = cfg.name if hasattr(cfg, "name") else str(cfg)
                tool_names = [t.name for t in self.registry.list_tools() if t.name.startswith(f"mcp_{srv_name}_")]
                section = f"## {srv_name}\n"
                if tool_names:
                    section += "Available tools: " + ", ".join(tool_names)
                parts.append(section)
            self._mcp_instructions = (
                "# MCP Server Instructions\n\n"
                "The following MCP servers are connected. "
                "Use their tools when the user asks.\n\n" + "\n\n".join(parts)
            )

    async def _shutdown_mcp(self) -> None:
        if self._mcp_init_task is not None:
            self._mcp_init_task.cancel()
            try:
                await self._mcp_init_task
            except (asyncio.CancelledError, Exception):
                pass
            self._mcp_init_task = None
        if self.mcp_manager is not None:
            self.mcp_manager.close()
            self.mcp_manager = None

    # -----------------------------------------------------------------
    # 退出
    # -----------------------------------------------------------------

    async def action_handle_ctrl_c(self) -> None:
        if self._streaming:
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
            self._show_system_message("(response interrupted)")
            self._finish_streaming()
            try:
                inp = self.query_one("#chat-input", ChatInput)
                inp.disabled = False
                inp.focus()
            except Exception:
                pass
            return

        if getattr(self, "_exit_requested", False):
            self.exit()
            return
        await self.graceful_exit()

    async def graceful_exit(self) -> None:
        """优雅退出：清理 MCP/hook/team、落盘会话，然后真正退出应用。

        ctrl+c 与 /exit 命令共用此路径——/exit 之前只设了 _exit_requested 标志却从不
        调 exit()，导致界面"卡住"（且把标志置真还会让随后的 ctrl+c 跳过清理）。
        """
        self._exit_requested = True

        async def _cleanup() -> None:
            tasks: list[asyncio.Task] = []

            if self.agent and self.agent.memory_manager:
                tasks.append(asyncio.create_task(self.agent._extract_memories(self.conversation)))
            if self.hook_engine:
                tasks.append(
                    asyncio.create_task(self.hook_engine.run_hooks("shutdown", HookContext(event_name="shutdown")))
                )
            tasks.append(asyncio.create_task(self._shutdown_mcp()))

            if tasks:
                await asyncio.wait(tasks, timeout=3.0)
                for t in tasks:
                    if not t.done():
                        t.cancel()

            if self._stale_cleanup_task and not self._stale_cleanup_task.done():
                self._stale_cleanup_task.cancel()

            if hasattr(self, "team_manager"):
                for name in list(self.team_manager._teams):
                    try:
                        team = self.team_manager._teams[name]
                        for m in team.members:
                            team.set_member_active(m.name, False)
                        self.team_manager.delete_team(name)
                    except Exception:
                        pass

            if self.session:
                # 刷盘所有尚未保存的对话消息（中断退出时尤其重要）
                unsaved = self.conversation.history[self._session_saved_count :]
                for msg in unsaved:
                    self.session.append(msg)
                self._session_saved_count = len(self.conversation.history)
                self.session.close()

        try:
            await _cleanup()
        except Exception as e:
            # 不要静默吞错——至少写 debug 日志
            import logging

            logging.getLogger("XHX").warning("Cleanup error: %s", e, exc_info=True)
        self.exit()

    def _show_error(self, text: str) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        error_widget = Static(f"✖ {text}", classes="message error-message")
        chat.mount(error_widget)
        self.call_after_refresh(chat.scroll_end, animate=False)

    def _show_system_message(self, text: str) -> None:
        # 关闭/取消阶段 #chat-area 可能已销毁，查询不到时安全跳过，避免二次崩溃。
        try:
            chat = self.query_one("#chat-area", VerticalScroll)
        except NoMatches:
            return
        msg = Static(f"  {text}", classes="message system-message")
        chat.mount(msg)
        self.call_after_refresh(chat.scroll_end, animate=False)

    _MODE_DISPLAY = {
        PermissionMode.DEFAULT: "default",
        PermissionMode.ACCEPT_EDITS: "accept-edits",
        PermissionMode.AUTO: "auto",
        PermissionMode.PLAN: "plan",
        PermissionMode.BYPASS: "bypass",
    }

    def _update_mode_label(self) -> None:
        if self.agent:
            perm = self.agent.permission_mode
            display = self._MODE_DISPLAY.get(perm, perm.value)
            color = _MODE_COLORS.get(perm, "dim")
            label = self.query_one("#mode-label", Static)
            if perm == PermissionMode.DEFAULT:
                label.update(f"[{color}]{display}[/{color}]")
            else:
                label.update(f"[{color}]{display}[/{color}]  (shift+tab to cycle)")
        try:
            model_label = self.query_one("#model-label", Static)
            model_text = self._selected_provider.model if self._selected_provider else ""
            if self._mcp_connecting:
                model_label.update(f"[yellow]MCP connecting…[/yellow]  {model_text}")
            else:
                model_label.update(model_text)
        except Exception:
            pass

    def _update_token_label(self, input_tokens: int, output_tokens: int) -> None:
        pass  # token 标签已从 UI 中移除

    def _recompute_context_used(self) -> None:
        """按当前对话估算 context 窗口占用（token）。

        用 ConversationManager.current_tokens()——它是规范算法：有真实用量锚点时
        baseline + 仅对锚点后新增消息做估算；冷启动则对整段历史估算。这能正确算进
        tool_calls / tool_results，且**不依赖 provider 是否在流末回传 usage**
        （deepseek 常不回传，纯靠 API 会一直显示 0）。

        注意：这是"当前窗口占用"，与累计消耗的 _xhx_tokens_total（逐轮相加、会无限增长）
        语义不同，不可混用。
        """
        conv = getattr(self, "conversation", None)
        if conv is None:
            return
        try:
            self._xhx_context_used = conv.current_tokens()
        except Exception:
            return

    def _update_xhx_status(self) -> None:
        """更新 XHX 独有状态栏：tokens / context / compaction。"""
        try:
            status = self.query_one("#xhx-status", Static)
        except Exception:
            return

        # 每次渲染都按当前对话刷新 context 占用——不依赖某个特定事件是否触发，
        # 也不依赖 provider 是否回传 usage。
        self._recompute_context_used()

        parts = []
        if self._xhx_tokens_total > 0:
            from xhx_agent.tui.format import human_tokens

            parts.append(f"tokens:{human_tokens(self._xhx_tokens_total)}")
        if self._xhx_context_budget > 0:
            from xhx_agent.tui.format import context_meter

            label, pct, level = context_meter(self._xhx_context_used, self._xhx_context_budget)
            color = {"ok": "green", "warn": "yellow", "crit": "red", "none": ""}.get(level, "")
            parts.append(f"[{color}]{label}[/{color}]" if color else label)
        if self._xhx_compaction_count > 0:
            parts.append(f"compact:{self._xhx_compaction_count}")
        if self._xhx_tool_count > 0:
            parts.append(f"tools:{self._xhx_tool_count}")
        if self._xhx_last_model:
            parts.append(self._xhx_last_model)
        if self._xhx_last_duration_ms > 0:
            parts.append(f"{self._xhx_last_duration_ms / 1000:.1f}s")

        status.update("  " + " · ".join(parts) if parts else "")


# ---------------------------------------------------------------------------
# CLI 入口兼容
# ---------------------------------------------------------------------------


def _resolve_perm_mode(s: str) -> PermissionMode:
    """把 config 的 default_permission_mode 字符串解析为 PermissionMode（容错，未知回退 DEFAULT）。"""
    t = (s or "").strip().lower().replace("_", "").replace("-", "")
    if t == "auto":
        return PermissionMode.AUTO
    if t in ("acceptedits", "accept", "edits"):
        return PermissionMode.ACCEPT_EDITS
    if t in ("bypass", "bypasspermissions", "yolo"):
        return PermissionMode.BYPASS
    if t == "plan":
        return PermissionMode.PLAN
    return PermissionMode.DEFAULT


def run_textual_console(
    workspace: str | Path | None = None,
    profile: str | None = None,
) -> None:
    """启动 TUI 控制台（从 CLI chat 命令调用）。

    将 XHX-Agent 的 profile 配置转换为新 TUI 需要的 ProviderConfig。
    """
    from pathlib import Path as P  # noqa: N817 P 为局部 Path 短别名

    from xhx_agent.config import ProviderConfig
    from xhx_agent.runtime.profiles import get_profile

    ws = P(workspace) if workspace else P.cwd()
    ws = ws.resolve()

    # 只用指定 profile（默认 default）
    profile_name = profile or "default"
    p = get_profile(ws, profile_name)
    if p is None:
        raise RuntimeError(f"Profile '{profile_name}' not found. Run 'xhx init' first.")

    provider = ProviderConfig.from_xhx_profile(p)

    # 从 .xhx/config.json 读取运行时开关，统一接入 app。此前 run_textual_console 只传
    # providers，XHXApp 的 mcp/hooks/coordinator/fork/verify 等开关全用默认——配了也不生效。
    from xhx_agent.hooks import HookEngine, load_hooks
    from xhx_agent.runtime.config import load_config
    from xhx_agent.runtime.mcp_config import load_mcp_servers

    cfg = load_config(ws)

    # MCP：.xhx/mcp.json（项目级优先，其次全局）；传原始项目根 ws（gitignored 的 .xhx/ 不在 worktree 里）。
    mcp_servers = load_mcp_servers(ws)

    # 生命周期钩子：坏配置不该炸启动，解析失败即视为无 hook。
    try:
        hooks = load_hooks(cfg.raw_hooks)
    except Exception:
        hooks = []
    hook_engine = HookEngine(hooks) if hooks else None

    # 初始权限模式（TUI 内仍可 shift+tab 切换）+ worktree 软链目录配置。
    perm_mode = _resolve_perm_mode(cfg.default_permission_mode)
    wt_config = None
    if cfg.worktree_symlink_directories:
        from xhx_agent.config import WorktreeConfig

        wt_config = WorktreeConfig(symlink_directories=cfg.worktree_symlink_directories)

    app = XHXApp(
        providers=[provider],
        permission_mode=perm_mode,
        mcp_servers=mcp_servers,
        hook_engine=hook_engine,
        worktree_config=wt_config,
        enable_fork=cfg.enable_fork,
        enable_verification_agent=cfg.enable_verification_agent,
        enable_verification_gate=cfg.enable_verification_gate,
        enable_coordinator_mode=cfg.enable_coordinator_mode,
    )
    app.run()
