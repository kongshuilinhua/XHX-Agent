from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# 取消原因上下文变量。在 cancel() agent task 之前设置，让被中断的
# 工具（如 Bash）能判断是用户主动中断（不杀进程）还是系统取消。
cancel_reason: contextvars.ContextVar[str] = contextvars.ContextVar("cancel_reason", default="")

from pydantic import ValidationError

from xhx_agent.client import LLMClient
from xhx_agent.context import (
    CompactBoundary,
    CompactCircuitBreaker,
    CompactEvent,
    ContentReplacementState,
    RecoveryState,
    append_replacement_records,
    apply_tool_result_budget,
    auto_compact,
    create_replacement_state,
    ensure_session_dir,
)
from xhx_agent.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from xhx_agent.conversation import ThinkingBlock as ConvThinkingBlock
from xhx_agent.hooks import HookContext, HookEngine, ToolRejectedError
from xhx_agent.memory.auto_memory import MemoryManager
from xhx_agent.permissions import (
    PermissionChecker,
    PermissionMode,
)
from xhx_agent.prompts import build_environment_context, build_plan_mode_reminder, build_system_prompt
from xhx_agent.tools import ToolRegistry
from xhx_agent.tools.base import (
    MAX_OUTPUT_CHARS,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)

log = logging.getLogger(__name__)

MEMORY_EXTRACTION_INTERVAL = 5
MAX_TOKENS_CEILING = 64000
MAX_OUTPUT_TOKENS_RECOVERIES = 3

# plan 模式下不向模型暴露的工具：规划阶段禁止建团队（团队是并行写代码的执行手段）。
# 注意：Agent(dispatch) 仍保留——plan 模式需要派只读子 agent 做广泛调研；spawn 出的子 agent
# 会被强制为只读（见 tools/agent_tool.py：父 plan_mode → 子 agent 用 PLAN 模式）。
_PLAN_MODE_DISALLOWED_TOOLS = frozenset({"TeamCreate", "TeamDelete", "EnterPlanMode"})

# 模型调用这些工具表示"规划完成、请求弹出审批对话框"。present_plan 可在任何模式下使用
# （模型自行判断任务复杂、主动提方案，对标 Claude）；ExitPlanMode 仅用于退出 plan 模式。
_EXIT_PLAN_TOOLS = frozenset({"ExitPlanMode", "present_plan"})

# 非 plan 模式下不暴露给模型的工具：仅 ExitPlanMode（"退出 plan 模式"在非 plan 模式无意义）。
# present_plan 不在此列——它是通用的"提交方案待审批"工具，任何模式都可用。
_NON_PLAN_DISALLOWED_TOOLS = frozenset({"ExitPlanMode"})


# ---------------------------------------------------------------------------
# AgentEvent 事件类型
# ---------------------------------------------------------------------------


@dataclass
class StreamText:
    text: str


@dataclass
class ThinkingText:
    text: str


@dataclass
class RetryEvent:
    reason: str
    wait: float = 0.0


@dataclass
class ToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict[str, Any]


@dataclass
class ToolResultEvent:
    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class TurnComplete:
    turn: int


@dataclass
class LoopComplete:
    total_turns: int


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int


@dataclass
class ErrorEvent:
    message: str


@dataclass
class CompactNotification:
    before_tokens: int
    message: str
    # 结构化 boundary（摘要 + 原文保留尾部），UI/session 层用它持久化 compact_boundary 记录。
    # 失败路径下为 None。
    boundary: CompactBoundary | None = None


@dataclass
class HookEvent:
    hook_id: str
    event: str
    output: str
    success: bool


class PermissionResponse(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ALLOW_ALWAYS = "allow_always"


@dataclass
class PermissionRequest:
    tool_name: str
    description: str
    future: asyncio.Future[PermissionResponse]


AgentEvent = (
    StreamText
    | ThinkingText
    | RetryEvent
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | LoopComplete
    | UsageEvent
    | ErrorEvent
    | PermissionRequest
    | CompactNotification
    | HookEvent
)


# ---------------------------------------------------------------------------
# LLM 响应收集器
# ---------------------------------------------------------------------------


@dataclass
class ThinkingBlock:
    thinking: str
    signature: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(self, stream: AsyncIterator[StreamEvent]) -> AsyncIterator[AgentEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield StreamText(text=event.text)
            elif isinstance(event, ThinkingDelta):
                yield ThinkingText(text=event.text)
            elif isinstance(event, ThinkingComplete):
                self.response.thinking_blocks.append(ThinkingBlock(thinking=event.thinking, signature=event.signature))
            elif isinstance(event, (ToolCallStart, ToolCallDelta)):
                pass
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(
                    tool_name=event.tool_name,
                    tool_id=event.tool_id,
                    arguments=event.arguments,
                )
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
                self.response.input_tokens = event.input_tokens
                self.response.output_tokens = event.output_tokens
                self.response.cache_read = event.cache_read
                self.response.cache_creation = event.cache_creation


# ---------------------------------------------------------------------------
# tool 批量执行
# ---------------------------------------------------------------------------


@dataclass
class ToolBatch:
    concurrent: bool
    calls: list[ToolCallComplete]


def partition_tool_calls(
    tool_calls: list[ToolCallComplete],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = tool is not None and tool.is_concurrency_safe and registry.is_enabled(tc.tool_name)

        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))
    return batches


# ---------------------------------------------------------------------------
# streaming 执行器 — 在 LLM streaming 期间启动 tool 执行
# ---------------------------------------------------------------------------


@dataclass
class _ToolExecResult:
    tool_id: str
    tool_name: str
    result: ToolResult
    elapsed: float
    is_unknown: bool


class StreamingExecutor:
    def __init__(self) -> None:
        self._tasks: list[tuple[int, asyncio.Task[_ToolExecResult]]] = []
        self._order = 0

    def submit(
        self,
        coro: Any,
    ) -> None:
        task = asyncio.create_task(coro)
        self._tasks.append((self._order, task))
        self._order += 1

    async def collect_results(self) -> list[_ToolExecResult]:
        if not self._tasks:
            return []
        tasks = [t for _, t in sorted(self._tasks, key=lambda x: x[0])]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[_ToolExecResult] = []
        for r in results:
            if isinstance(r, BaseException):
                out.append(
                    _ToolExecResult(
                        tool_id="",
                        tool_name="",
                        result=ToolResult(output=f"Tool execution error: {r}", is_error=True),
                        elapsed=0.0,
                        is_unknown=False,
                    )
                )
            else:
                out.append(r)
        return out


# ---------------------------------------------------------------------------
# Agent 主循环
# ---------------------------------------------------------------------------


class Agent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        protocol: str,
        work_dir: str = ".",
        max_iterations: int = 50,
        permission_checker: PermissionChecker | None = None,
        context_window: int = 200_000,
        instructions_content: str = "",
        memory_manager: MemoryManager | None = None,
        hook_engine: HookEngine | None = None,
    ) -> None:
        self.client = client
        # auto 分类器用的客户端：默认 None（沿用主 client）；上层可按 routing.roles["classify"]
        # 路由成一个便宜模型注入（见 models.routing.build_role_client），没配就走主模型。
        self.classifier_client: LLMClient | None = None
        self.registry = registry
        self.protocol = protocol
        self.work_dir = work_dir
        self.max_iterations = max_iterations
        self.permission_checker = permission_checker
        self.permission_mode: PermissionMode = permission_checker.mode if permission_checker else PermissionMode.DEFAULT
        self.context_window = context_window
        self.session_dir = ensure_session_dir(work_dir)
        self.compact_breaker = CompactCircuitBreaker()
        self.replacement_state: ContentReplacementState = create_replacement_state()
        # 保存重建工作上下文所需的快照，在 Layer 2 压缩对话后使用：
        # 最近的文件读取和 skill 调用。每次 ReadFile / skill 调用时记录，
        # auto_compact 触发阈值时消费。
        self.recovery_state: RecoveryState = RecoveryState()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.instructions_content = instructions_content
        self.memory_manager = memory_manager
        self.hook_engine = hook_engine
        self._loop_count = 0
        self._extracting = False
        self.session_id: str = ""
        self.active_skills: dict[str, str] = {}
        self._skill_catalog: str = ""
        self._agent_catalog: str = ""
        self._agent_catalog_list: list[tuple[str, str]] = []
        self.agent_id: str = uuid.uuid4().hex[:12]
        self.parent_id: str | None = None
        self.trace_id: str | None = None
        # 持久化证据链（`.xhx/traces/<run_id>.jsonl`）：上层（headless/TUI）挂一个
        # EvidenceStore 即启用，None 时零开销。写入永不打断运行。
        self.trace_store: Any | None = None
        self._trace_turn: int = 0
        self.coordinator_mode: bool = False
        self.team_name: str = ""
        self._team_manager: Any = None
        self.notification_fn: Callable[[], list[str]] | None = None
        self.file_history: Any = None
        self._current_conversation: ConversationManager | None = None
        # 本轮累计被改动的文件（相对路径），供 stop 事件上的 verification 钩子定向跑测试。
        self._changed_files: list[str] = []
        # 完成前验证关卡（交互路径）默认关闭，需显式 opt-in 才启用——与 headless `--verify`
        # 一致。开启后每次有 .py/JS 改动的收尾轮会先跑定向测试，失败则注入对话让模型修复。
        self.verification_gate: bool = False

    @property
    def _transcript_path(self) -> str:
        if self.session_id:
            return str(Path(self.work_dir) / ".xhx" / "sessions" / f"{self.session_id}.jsonl")
        return ""

    @property
    def plan_mode(self) -> bool:
        return self.permission_mode == PermissionMode.PLAN

    def _active_tool_schemas(self) -> list[dict[str, Any]]:
        """当前轮喂给模型的工具 schema：按模式裁剪。

        - plan 模式：剔除 team 类工具（团队是并行写代码的执行手段，不属于规划）；
        - 非 plan 模式：剔除 plan 专属工具（present_plan / ExitPlanMode），否则模型会误调被拒。
        """
        disallowed = _PLAN_MODE_DISALLOWED_TOOLS if self.plan_mode else _NON_PLAN_DISALLOWED_TOOLS
        return [
            s
            for s in self.registry.get_all_schemas(self.protocol)
            if s.get("function", {}).get("name") not in disallowed
        ]

    @property
    def turn_count(self) -> int:
        """已执行的对话轮数（公开只读）。"""
        return self._loop_count

    @property
    def changed_files(self) -> list[str]:
        """本轮累计被改动的文件列表（公开只读）。"""
        return list(self._changed_files)

    _plan_path_cache: Path | None = None

    def _get_plan_path(self) -> Path:
        if self._plan_path_cache is not None:
            return self._plan_path_cache
        import datetime
        import random

        _ADJECTIVES = [  # noqa: N806 局部常量保持大写惯例
            "bold",
            "bright",
            "calm",
            "cool",
            "deep",
            "fair",
            "fast",
            "fine",
            "glad",
            "keen",
            "kind",
            "lean",
            "mild",
            "neat",
            "pure",
            "safe",
            "slim",
            "soft",
            "tall",
            "warm",
            "wise",
            "grand",
            "swift",
            "vivid",
        ]
        _NOUNS = [  # noqa: N806 局部常量保持大写惯例
            "sketch",
            "draft",
            "spark",
            "bloom",
            "trail",
            "ridge",
            "creek",
            "grove",
            "cliff",
            "cloud",
            "field",
            "forge",
            "frost",
            "haven",
            "pearl",
            "stone",
            "storm",
            "river",
            "tower",
            "delta",
            "flame",
            "orbit",
            "pulse",
            "shore",
        ]
        plans_dir = Path(self.work_dir) / ".xhx" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%m%d-%H%M")
        slug = f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{ts}"
        self._plan_path_cache = plans_dir / f"{slug}.md"
        return self._plan_path_cache

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_mode = mode
        if self.permission_checker:
            self.permission_checker.mode = mode

    def _build_env_context(self) -> str:
        return build_environment_context(self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog)

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body
        if self._current_conversation is not None:
            self._current_conversation.update_environment(self._build_env_context())

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog

    def set_agent_catalog(self, catalog: str, catalog_list: list[tuple[str, str]] | None = None) -> None:
        self._agent_catalog = catalog
        if catalog_list is not None:
            self._agent_catalog_list = catalog_list

    def _build_hook_context(self, event: str, **kwargs: str | dict) -> HookContext:
        _ta = kwargs.get("tool_args", {})
        return HookContext(
            event_name=event,
            tool_name=str(kwargs.get("tool_name", "")),
            tool_args=_ta if isinstance(_ta, dict) else {},
            file_path=str(kwargs.get("file_path", "")),
            message=str(kwargs.get("message", "")),
            error=str(kwargs.get("error", "")),
            work_dir=str(self.work_dir),
            changed_files=list(self._changed_files),
        )

    _MUTATION_TOOLS = {"WriteFile", "EditFile", "apply_patch"}

    def _infer_file_path(self, args: dict) -> str:
        return str(args.get("file_path", args.get("path", "")))

    def _record_changed_file(self, tool_name: str, args: dict) -> None:
        """记录写/改类工具触及的文件，供后续 verification 定向测试。"""
        if tool_name not in self._MUTATION_TOOLS:
            return
        path = self._infer_file_path(args)
        if path and path not in self._changed_files:
            self._changed_files.append(path)

    def _trace(self, entry_type: str, payload: dict[str, Any]) -> None:
        """写一条持久化 trace（挂了 trace_store 才生效）；失败只吞掉，绝不打断运行。"""
        if self.trace_store is None:
            return
        try:
            self.trace_store.write_trace(entry_type, payload)
        except Exception:
            pass

    def _trace_model_turn(self, turn: int, response: LLMResponse) -> None:
        self._trace(
            "model_turn",
            {
                "turn": turn,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "text": response.text[:2000],
                "tool_calls": [tc.tool_name for tc in response.tool_calls],
            },
        )

    def _trace_tool_exec(self, tc: ToolCallComplete, result: ToolResult, elapsed: float) -> None:
        self._trace("tool_call", {"turn": self._trace_turn, "tool": tc.tool_name, "arguments": tc.arguments})
        self._trace(
            "tool_result",
            {
                "turn": self._trace_turn,
                "tool": tc.tool_name,
                "is_error": result.is_error,
                "output": result.output[:500],
                "elapsed": round(elapsed, 3),
            },
        )

    def _drain_hook_events(self) -> list[HookEvent]:
        if not self.hook_engine:
            return []
        return [
            HookEvent(
                hook_id=n.hook_id,
                event=n.event,
                output=n.output,
                success=n.success,
            )
            for n in self.hook_engine.drain_notifications()
        ]

    def _inject_context_pack_memories(self, conversation: ConversationManager) -> None:
        """通过 compile_context_pack 召回记忆并注入 system reminder（headless 路径也生效）。"""
        try:
            from xhx_agent.context.compiler import compile_context_pack
            from xhx_agent.repo_intel.scanner import scan_project

            workspace = Path(self.work_dir)
            task = ""
            if conversation.history:
                for msg in reversed(conversation.history):
                    if msg.role == "user" and msg.content:
                        task = msg.content[:500]
                        break

            if not task:
                return

            scan = scan_project(workspace)

            pack = compile_context_pack(
                workspace=workspace,
                task=task,
                scan=scan,
                changed_files=self._changed_files or None,
                budget_tokens=3000,
            )

            memory_items = [item for item in pack.items if item.kind.startswith("memory:")]
            if memory_items:
                parts = [f"[{item.source}] {item.content}" for item in memory_items]
                reminder = "<context-pack-memories>\n" + "\n\n".join(parts) + "\n</context-pack-memories>"
                conversation.add_system_reminder(reminder)
        except Exception:
            log.debug("Context-pack memory injection skipped", exc_info=True)

    async def _run_verification_gate(self, conversation: ConversationManager) -> str:
        """完成前验证关卡：推断并运行验证命令，失败则注入对话让模型修复。"""
        from xhx_agent.safety.repair import decide_repair
        from xhx_agent.verification.router import infer_verification

        plan = infer_verification(Path(self.work_dir), self._changed_files)
        if not plan.commands:
            self._verification_passed = True
            return "pass"

        attempts = getattr(self, "_repair_attempts", 0)
        all_passed = True
        failure_output: list[str] = []

        for cmd in plan.commands:
            try:
                import subprocess

                # 在线程池里跑，避免阻塞交互式 run() 所在的事件循环（关卡现已在 TUI 真触发）。
                result = await asyncio.to_thread(
                    subprocess.run,
                    cmd.command,
                    shell=True,
                    cwd=self.work_dir,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    all_passed = False
                    output = (result.stdout + result.stderr).strip()
                    if len(output) > 3000:
                        output = output[:3000] + "\n...<truncated>"
                    failure_output.append(f"$ {cmd.command}\nExit code: {result.returncode}\n{output}")
            except Exception as e:
                all_passed = False
                failure_output.append(f"$ {cmd.command}\nError: {e}")

        if all_passed:
            self._verification_passed = True
            return "pass"

        decision = decide_repair("failed", attempts)
        self._repair_attempts = attempts + 1

        if decision.should_repair:
            repair_msg = "Verification FAILED before completion. Fix the issues and try again.\n\n" + "\n\n".join(
                failure_output
            )
            conversation.add_user_message(repair_msg)
            return "retry"

        self._verification_passed = True
        log.warning("Verification failed but repair attempts exhausted; proceeding")
        return "pass"

    async def run(self, conversation: ConversationManager) -> AsyncIterator[AgentEvent]:
        self._current_conversation = conversation
        conversation.inject_environment(self._build_env_context())

        memory_content = self.memory_manager.load() if self.memory_manager else ""
        conversation.inject_long_term_memory(self.instructions_content, memory_content)

        self._inject_context_pack_memories(conversation)

        if self.hook_engine:
            ctx = self._build_hook_context("session_start")
            await self.hook_engine.run_hooks("session_start", ctx)
            for he in self._drain_hook_events():
                yield he

        iteration = 0
        consecutive_unknown = 0
        max_tokens_escalated = False
        output_recoveries = 0

        while True:
            iteration += 1
            self._trace_turn = iteration

            if iteration > self.max_iterations:
                yield ErrorEvent(message=f"Agent reached maximum iterations ({self.max_iterations})")
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_start")
                await self.hook_engine.run_hooks("turn_start", ctx)
                for he in self._drain_hook_events():
                    yield he

            self._consume_mailbox(conversation)
            if self.notification_fn:
                for note in self.notification_fn():
                    conversation.add_system_reminder(note)

            # Layer 2: 接近 context window 上限时自动 compact（操作原始对话）
            compact_result = await auto_compact(
                conversation,
                self.client,
                self.context_window,
                self.session_dir,
                protocol=self.protocol,
                breaker=self.compact_breaker,
                recovery=self.recovery_state,
                tool_schemas=self.registry.get_all_schemas(self.protocol),
                transcript_path=self._transcript_path,
            )
            if isinstance(compact_result, CompactEvent):
                yield CompactNotification(
                    before_tokens=compact_result.before_tokens,
                    message=f"上下文已压缩（压缩前 {compact_result.before_tokens:,} tokens）",
                    boundary=compact_result.boundary,
                )
                conversation.inject_environment(self._build_env_context())
                mem = self.memory_manager.load() if self.memory_manager else ""
                conversation.inject_long_term_memory(self.instructions_content, mem)
            elif isinstance(compact_result, str):
                yield ErrorEvent(message=compact_result)

            if self.hook_engine:
                ctx = self._build_hook_context("pre_send")
                await self.hook_engine.run_hooks("pre_send", ctx)
                for he in self._drain_hook_events():
                    yield he

            hook_prompts = self.hook_engine.collect_prompt_messages() if self.hook_engine else None
            system = build_system_prompt(
                hook_prompts=hook_prompts,
                coordinator_mode=self.coordinator_mode,
                agent_catalog=self._agent_catalog_list or None,
            )

            if self.plan_mode:
                plan_path = str(self._get_plan_path())
                if self.permission_checker:
                    self.permission_checker.plan_file_path = plan_path
                plan_exists = self._get_plan_path().exists()
                plan_reminder = build_plan_mode_reminder(plan_path, plan_exists, iteration)
                conversation.add_system_reminder(plan_reminder)

            if self.hook_engine:
                for hook_note in self.hook_engine.drain_notifications():
                    conversation.add_system_reminder(
                        f"Hook [{hook_note.hook_id}] {hook_note.event}: {hook_note.output}"
                    )

            deferred_names = self.registry.get_deferred_tool_names()
            if deferred_names:
                conversation.add_system_reminder(
                    "The following deferred tools are available via ToolSearch. "
                    "Their schemas are NOT loaded - use ToolSearch with "
                    'query "select:<name>[,<name>...]" to load tool schemas before calling them:\n'
                    + "\n".join(deferred_names)
                )

            tools = self._active_tool_schemas()

            # Layer 1: 在 LLM 调用前应用 tool-result budget，确保 api_conv 反映
            # 本轮迭代中所有已发生的写入（system reminders、hook 通知等）。
            # 原始 conversation 不会被修改；替换决策保存在 self.replacement_state 中。
            api_conv, _new_records = apply_tool_result_budget(conversation, self.session_dir, self.replacement_state)
            if _new_records:
                append_replacement_records(self.session_dir, _new_records)

            collector = StreamCollector()
            llm_stream = self.client.stream(api_conv, system=system, tools=tools)
            async for event in collector.consume(llm_stream):
                yield event

            response = collector.response

            if self.hook_engine:
                ctx = self._build_hook_context("post_receive", message=response.text)
                await self.hook_engine.run_hooks("post_receive", ctx)
                for he in self._drain_hook_events():
                    yield he

            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens
            yield UsageEvent(
                input_tokens=self.total_input_tokens,
                output_tokens=self.total_output_tokens,
            )
            self._trace_model_turn(iteration, response)

            conv_thinking = [
                ConvThinkingBlock(thinking=tb.thinking, signature=tb.signature) for tb in response.thinking_blocks
            ]

            if response.stop_reason == "max_tokens":
                if not max_tokens_escalated:
                    self.client.set_max_output_tokens(MAX_TOKENS_CEILING)
                    max_tokens_escalated = True
                    if response.text:
                        conversation.add_assistant_message(response.text, thinking_blocks=conv_thinking)
                        conversation.add_user_message(
                            "Output token limit hit. Resume directly from where you stopped. "
                            "Do not apologize or repeat previous content. Pick up mid-thought if needed."
                        )
                    yield RetryEvent(reason="max_tokens escalation")
                    continue
                elif output_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES:
                    output_recoveries += 1
                    conversation.add_assistant_message(response.text, thinking_blocks=conv_thinking)
                    conversation.add_user_message(
                        "Output token limit hit. Resume directly from where you stopped. "
                        "Break remaining work into smaller pieces."
                    )
                    yield RetryEvent(reason=f"max_tokens recovery {output_recoveries}/{MAX_OUTPUT_TOKENS_RECOVERIES}")
                    continue
            else:
                output_recoveries = 0

            if not response.tool_calls:
                conversation.add_assistant_message(response.text, thinking_blocks=conv_thinking)
                self._loop_count += 1

                # 完成前验证关卡：仅在显式开启（verification_gate）且有变更文件时运行
                if self.verification_gate and self._changed_files and not getattr(self, "_verification_passed", False):
                    gate_result = await self._run_verification_gate(conversation)
                    if gate_result == "retry":
                        continue

                if self._loop_count % MEMORY_EXTRACTION_INTERVAL == 0 and self.memory_manager:
                    asyncio.ensure_future(self._extract_memories(conversation))
                if self.hook_engine:
                    ctx = self._build_hook_context("turn_end")
                    await self.hook_engine.run_hooks("turn_end", ctx)
                    ctx = self._build_hook_context("session_end")
                    await self.hook_engine.run_hooks("session_end", ctx)
                    for he in self._drain_hook_events():
                        yield he
                if self.file_history is not None:
                    summary = response.text[:60] + "..." if len(response.text) > 60 else response.text
                    self.file_history.make_snapshot(len(conversation.history), summary)
                yield LoopComplete(total_turns=iteration)
                break

            tool_uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in response.tool_calls
            ]
            conversation.add_assistant_message(response.text, tool_uses, thinking_blocks=conv_thinking)
            # 在 assistant 回复加入历史后锚定实际用量：基线（input + cache + output）
            # 覆盖到当前位置，因此下一轮迭代顶部的 auto-compact 检查只需对
            # 接下来追加的 tool results 做字符估算。
            conversation.record_usage_anchor(
                response.input_tokens,
                response.output_tokens,
                response.cache_read,
                response.cache_creation,
            )

            tool_results: list[ToolResultBlock] = []
            batches = partition_tool_calls(response.tool_calls, self.registry)

            for batch in batches:
                if batch.concurrent and len(batch.calls) > 1:
                    batch_results = await self._execute_batch_parallel(batch.calls)
                    for br in batch_results:
                        if br.is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0
                        content = self._maybe_persist_or_truncate(br.tool_id, br.result.output)
                        tool_results.append(
                            ToolResultBlock(
                                tool_use_id=br.tool_id,
                                content=content,
                                is_error=br.result.is_error,
                            )
                        )
                        yield ToolResultEvent(
                            tool_id=br.tool_id,
                            tool_name=br.tool_name,
                            output=br.result.output,
                            is_error=br.result.is_error,
                            elapsed=br.elapsed,
                        )
                else:
                    for tc in batch.calls:
                        result: ToolResult | None = None
                        elapsed = 0.0
                        is_unknown = False

                        if self.hook_engine:
                            file_path = self._infer_file_path(tc.arguments)
                            hook_ctx = self._build_hook_context(
                                "pre_tool_use",
                                tool_name=tc.tool_name,
                                tool_args=tc.arguments,
                                file_path=file_path,
                            )
                            rejection = None
                            try:
                                await self.hook_engine.run_hooks("pre_tool_use", hook_ctx)
                            except ToolRejectedError as _rej:
                                rejection = _rej
                            for he in self._drain_hook_events():
                                yield he
                            if rejection is not None:
                                result = ToolResult(
                                    output=f"Hook rejected: {rejection.reason}",
                                    is_error=True,
                                )
                                content = self._maybe_persist_or_truncate(tc.tool_id, result.output)
                                tool_results.append(
                                    ToolResultBlock(
                                        tool_use_id=tc.tool_id,
                                        content=content,
                                        is_error=True,
                                    )
                                )
                                yield ToolResultEvent(
                                    tool_id=tc.tool_id,
                                    tool_name=tc.tool_name,
                                    output=result.output,
                                    is_error=True,
                                    elapsed=0.0,
                                )
                                continue

                        async for item in self._execute_tool(tc):
                            if isinstance(item, PermissionRequest):
                                yield item
                            else:
                                result, elapsed, is_unknown = item

                        if result is None:
                            result = ToolResult(output="Error: no result from tool", is_error=True)

                        # 记录写/改类工具触及的文件，供完成前验证关卡 / context-pack diff /
                        # stop 钩子定向测试使用。交互路径此前漏记，导致这些功能在 TUI 下哑火。
                        if not result.is_error:
                            self._record_changed_file(tc.tool_name, tc.arguments)

                        if is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0

                        if self.hook_engine:
                            file_path = self._infer_file_path(tc.arguments)
                            hook_ctx = self._build_hook_context(
                                "post_tool_use",
                                tool_name=tc.tool_name,
                                tool_args=tc.arguments,
                                file_path=file_path,
                            )
                            await self.hook_engine.run_hooks("post_tool_use", hook_ctx)
                            for he in self._drain_hook_events():
                                yield he

                        content = self._maybe_persist_or_truncate(tc.tool_id, result.output)
                        tool_results.append(
                            ToolResultBlock(
                                tool_use_id=tc.tool_id,
                                content=content,
                                is_error=result.is_error,
                            )
                        )
                        yield ToolResultEvent(
                            tool_id=tc.tool_id,
                            tool_name=tc.tool_name,
                            output=result.output,
                            is_error=result.is_error,
                            elapsed=elapsed,
                        )

            if consecutive_unknown >= 3:
                yield ErrorEvent(message="Agent terminated: too many consecutive unknown tool calls")
                break

            exit_plan_called = any(tc.tool_name in _EXIT_PLAN_TOOLS for tc in response.tool_calls)
            conversation.add_tool_results_message(tool_results)
            if exit_plan_called:
                yield TurnComplete(turn=iteration)
                yield LoopComplete(total_turns=iteration)
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_end")
                await self.hook_engine.run_hooks("turn_end", ctx)
                for he in self._drain_hook_events():
                    yield he
            yield TurnComplete(turn=iteration)

    def _consume_mailbox(self, conversation: ConversationManager) -> None:
        if not self.team_name or not self._team_manager:
            return
        try:
            mailbox = self._team_manager.get_mailbox(self.team_name)
            if mailbox is None:
                return
            messages = mailbox.consume(self.agent_id)
            for msg in messages:
                prefix = f"[Message from {msg.from_agent}]"
                if msg.message_type != "text":
                    prefix = f"[{msg.message_type} from {msg.from_agent}]"
                content = f"{prefix} {msg.content}"
                conversation.add_user_message(content)
        except Exception as e:
            log.debug("Mailbox consumption failed: %s", e)

    def _build_permission_description(self, tc: ToolCallComplete) -> str:
        if tc.tool_name == "Bash":
            return tc.arguments.get("command", tc.tool_name)
        if tc.tool_name in ("ReadFile", "WriteFile", "EditFile"):
            return tc.arguments.get("file_path", tc.tool_name)
        return str(tc.arguments)

    async def _execute_single_tool_direct(self, tc: ToolCallComplete) -> _ToolExecResult:
        tool = self.registry.get(tc.tool_name)
        start = time.monotonic()

        if tool is None:
            return _ToolExecResult(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=ToolResult(output=f"Error: unknown tool '{tc.tool_name}'", is_error=True),
                elapsed=time.monotonic() - start,
                is_unknown=True,
            )

        if not self.registry.is_enabled(tc.tool_name):
            return _ToolExecResult(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=ToolResult(output=f"Error: tool '{tc.tool_name}' is disabled", is_error=True),
                elapsed=time.monotonic() - start,
                is_unknown=False,
            )

        try:
            params = tool.params_model.model_validate(tc.arguments)
            result = await tool.execute(params)
        except ValidationError as e:
            result = ToolResult(output=f"Parameter validation error: {e}", is_error=True)
        except Exception as e:
            result = ToolResult(output=f"Tool execution error: {e}", is_error=True)

        self._snapshot_for_recovery(tc, result)
        elapsed = time.monotonic() - start
        self._trace_tool_exec(tc, result, elapsed)

        return _ToolExecResult(
            tool_id=tc.tool_id,
            tool_name=tc.tool_name,
            result=result,
            elapsed=elapsed,
            is_unknown=False,
        )

    async def _execute_batch_parallel(self, calls: list[ToolCallComplete]) -> list[_ToolExecResult]:
        tasks = [self._execute_single_tool_direct(tc) for tc in calls]
        return list(await asyncio.gather(*tasks))

    async def _execute_tool(
        self, tc: ToolCallComplete
    ) -> AsyncIterator[tuple[ToolResult, float, bool] | PermissionRequest]:
        tool = self.registry.get(tc.tool_name)
        start = time.monotonic()
        is_unknown = False

        if tool is None:
            result = ToolResult(output=f"Error: unknown tool '{tc.tool_name}'", is_error=True)
            is_unknown = True
            elapsed = time.monotonic() - start
            yield result, elapsed, is_unknown
            return

        if not self.registry.is_enabled(tc.tool_name):
            result = ToolResult(
                output=f"Error: tool '{tc.tool_name}' is disabled in current mode",
                is_error=True,
            )
            elapsed = time.monotonic() - start
            yield result, elapsed, is_unknown
            return

        # 权限检查
        if self.permission_checker:
            decision = self.permission_checker.check(tool.name, tc.arguments, tool_category=tool.category)

            if decision.effect == "deny":
                result = ToolResult(
                    output=f"Permission denied: {decision.reason}",
                    is_error=True,
                )
                elapsed = time.monotonic() - start
                yield result, elapsed, is_unknown
                return

            if decision.effect == "ask":
                desc = self._build_permission_description(tc)
                # auto 模式：规则拿不准的命令先问 LLM 分类器，放行则静默执行、不打扰用户。
                need_prompt = True
                if decision.needs_classification and await self._classify_command_safe(desc):
                    need_prompt = False

                if need_prompt:
                    loop = asyncio.get_running_loop()
                    future: asyncio.Future[PermissionResponse] = loop.create_future()
                    # 向调用方 yield 权限请求事件，由调用方处理
                    yield PermissionRequest(
                        tool_name=tc.tool_name,
                        description=desc,
                        future=future,
                    )
                    response = await future

                    if response == PermissionResponse.DENY:
                        result = ToolResult(
                            output="Permission denied: 用户拒绝了此操作",
                            is_error=True,
                        )
                        elapsed = time.monotonic() - start
                        yield result, elapsed, is_unknown
                        return

                    if response == PermissionResponse.ALLOW_ALWAYS:
                        from xhx_agent.permissions import build_allow_always_rule

                        # 对 Bash 生成命令前缀规则（如 `git diff:*`），一次批准覆盖该命令族而非
                        # 只匹配那条完整命令行；对文件工具用精确内容。
                        rule = build_allow_always_rule(tc.tool_name, tc.arguments)
                        if rule is not None:
                            self.permission_checker.rule_engine.append_local_rule(rule)

        try:
            params = tool.params_model.model_validate(tc.arguments)
            result = await tool.execute(params)
        except ValidationError as e:
            result = ToolResult(output=f"Parameter validation error: {e}", is_error=True)
        except Exception as e:
            result = ToolResult(output=f"Tool execution error: {e}", is_error=True)

        self._snapshot_for_recovery(tc, result)

        elapsed = time.monotonic() - start
        self._trace_tool_exec(tc, result, elapsed)
        yield result, elapsed, is_unknown

    def _snapshot_for_recovery(self, tc: ToolCallComplete, result: ToolResult) -> None:
        """捕获 ReadFile 刚交给模型的内容，以便 Layer 2 压缩对话后
        auto_compact 能重新附加这些数据。每次 ReadFile 多一次磁盘读取，
        比从 tool 输出中反向解析行号要划算。
        """
        if result.is_error or tc.tool_name != "ReadFile":
            return
        path = tc.arguments.get("file_path") if isinstance(tc.arguments, dict) else None
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return
        self.recovery_state.record_file_read(path, content)

    async def _classify_command(self, command: str) -> bool:
        """auto 模式：让模型判一条 shell 命令是否可安全自动执行。返回 True=放行。

        只在 checker 规则拿不准（risk 分级为 CONFIRM）的命令上触发——明显安全/危险的已被
        规则秒判，无需打 LLM。系统级绝对禁令仍由 checker Layer 1b 硬拦，与此无关。
        """
        prompt = (
            "You are the safety gate of an autonomous coding agent running on the user's machine. "
            "Decide whether the following shell command is safe to auto-execute WITHOUT asking the user, "
            "in a normal coding workflow. BLOCK if it could destroy data, exfiltrate secrets, "
            "download-and-run remote code, delete many files, or modify system/global state. "
            "ALLOW ordinary dev commands (build, test, package install, scaffolding, version-control reads).\n\n"
            f"Command:\n{command}\n\n"
            "Answer with a single token on the first line: ALLOW or BLOCK."
        )
        mini = ConversationManager()
        mini.add_user_message(prompt)
        text = ""
        # 配了 routing.roles["classify"] 的便宜模型就用它，否则沿用主 client。
        client = self.classifier_client or self.client
        async for ev in client.stream(mini):
            if isinstance(ev, TextDelta):
                text += ev.text
        return text.strip().upper().startswith("ALLOW")

    async def _classify_command_safe(self, command: str) -> bool:
        """分类器封装：异常 / 超时 / 无客户端一律保守返回 False（转人工确认）。"""
        if self.classifier_client is None and self.client is None:
            return False
        try:
            return await asyncio.wait_for(self._classify_command(command), timeout=20)
        except Exception:
            return False

    async def _extract_memories(self, conversation: ConversationManager) -> None:
        if self._extracting or not self.memory_manager:
            return
        self._extracting = True
        try:
            from xhx_agent.conversation import Message as _Msg

            messages = [
                {"role": m.role, "content": m.content}
                for m in conversation.history
                if m.role in ("user", "assistant") and m.content
            ]

            async def summarize_fn(prompt: str) -> str:
                mini = ConversationManager()
                mini.history = [_Msg(role="user", content=prompt)]
                collected = ""
                async for event in self.client.stream(mini):
                    if isinstance(event, TextDelta):
                        collected += event.text
                return collected.strip()

            await self.memory_manager.extract(messages, summarize_fn)
        except Exception as e:
            log.debug("Memory extraction failed: %s", e)
        finally:
            self._extracting = False

    async def manual_compact(self, conversation: ConversationManager) -> CompactNotification | ErrorEvent:
        # auto_compact 会用摘要替换 conversation.history，所有 tool-result 内容
        # （原始或已替换的）都将被丢弃。这里跳过 apply_tool_result_budget —
        # 它在主循环中的唯一目的是为 LLM 调用生成 api_conv，而本路径不需要
        # 发起看到替换结果的 LLM 调用（auto_compact 内部的摘要调用操作的是原始对话）。
        result = await auto_compact(
            conversation,
            self.client,
            self.context_window,
            self.session_dir,
            protocol=self.protocol,
            manual=True,
            breaker=self.compact_breaker,
            recovery=self.recovery_state,
            tool_schemas=self.registry.get_all_schemas(self.protocol),
            transcript_path=self._transcript_path,
        )
        if isinstance(result, CompactEvent):
            conversation.inject_environment(self._build_env_context())
            memory_content = self.memory_manager.load() if self.memory_manager else ""
            conversation.inject_long_term_memory(self.instructions_content, memory_content)
            return CompactNotification(
                before_tokens=result.before_tokens,
                message=f"上下文已压缩（压缩前 {result.before_tokens:,} tokens）",
                boundary=result.boundary,
            )
        return ErrorEvent(message=result or "压缩失败：对话历史为空或未达到压缩条件")

    async def run_to_completion(
        self,
        task: str,
        conversation: ConversationManager | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        if conversation is None:
            conversation = ConversationManager()
            self._current_conversation = conversation

            conversation.inject_environment(self._build_env_context())

            if self.instructions_content:
                memory_content = self.memory_manager.load() if self.memory_manager else ""
                conversation.inject_long_term_memory(self.instructions_content, memory_content)
        else:
            self._current_conversation = conversation

        if task:
            conversation.add_user_message(task)

        hook_prompts = self.hook_engine.collect_prompt_messages() if self.hook_engine else None
        system = build_system_prompt(
            hook_prompts=hook_prompts,
            coordinator_mode=self.coordinator_mode,
        )

        tools = self._active_tool_schemas()

        # tool schema 在 openai-compat 协议下是嵌套的（{"function": {"name": ...}}），
        # anthropic 协议下是扁平的（{"name": ...}）；日志要兼容两种形状。
        tool_names = [t.get("name") or t.get("function", {}).get("name") for t in tools]
        log.info(
            "[run_to_completion] agent=%s tools=%d names=%s coordinator=%s",
            self.agent_id,
            len(tools),
            tool_names[:10],
            self.coordinator_mode,
        )

        last_text = ""

        for iteration in range(1, self.max_iterations + 1):
            self._trace_turn = iteration
            if self.hook_engine:
                ctx = self._build_hook_context("turn_start")
                await self.hook_engine.run_hooks("turn_start", ctx)

            self._consume_mailbox(conversation)
            if self.notification_fn:
                for note in self.notification_fn():
                    conversation.add_system_reminder(note)

            compact_result = await auto_compact(
                conversation,
                self.client,
                self.context_window,
                self.session_dir,
                protocol=self.protocol,
                breaker=self.compact_breaker,
                recovery=self.recovery_state,
                tool_schemas=self.registry.get_all_schemas(self.protocol),
                transcript_path=self._transcript_path,
            )
            if isinstance(compact_result, CompactEvent):
                conversation.inject_environment(self._build_env_context())

            deferred_names = self.registry.get_deferred_tool_names()
            if deferred_names:
                conversation.add_system_reminder(
                    "The following deferred tools are available via ToolSearch. "
                    "Their schemas are NOT loaded - use ToolSearch with "
                    'query "select:<name>[,<name>...]" to load tool schemas before calling them:\n'
                    + "\n".join(deferred_names)
                )

            api_conv, _new_records = apply_tool_result_budget(conversation, self.session_dir, self.replacement_state)
            if _new_records:
                append_replacement_records(self.session_dir, _new_records)

            collector = StreamCollector()
            llm_stream = self.client.stream(api_conv, system=system, tools=tools)
            async for _event in collector.consume(llm_stream):
                pass

            response = collector.response
            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens
            self._trace_model_turn(iteration, response)

            if event_callback:
                event_callback(
                    {
                        "type": "usage",
                        "usage": {
                            "inputTokens": self.total_input_tokens,
                            "outputTokens": self.total_output_tokens,
                        },
                    }
                )

            if response.text:
                last_text = response.text
                if event_callback:
                    event_callback(
                        {
                            "type": "stream_text",
                            "text": response.text,
                        }
                    )

            log.info(
                "[run_to_completion] agent=%s iter=%d tool_calls=%d text_len=%d stop=%s",
                self.agent_id,
                iteration,
                len(response.tool_calls),
                len(response.text),
                response.stop_reason,
            )

            if not response.tool_calls:
                conversation.add_assistant_message(response.text)
                self._loop_count += 1
                if self.file_history is not None:
                    summary = response.text[:60] + "..." if len(response.text) > 60 else response.text
                    self.file_history.make_snapshot(len(conversation.history), summary)
                break

            tool_uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in response.tool_calls
            ]
            conversation.add_assistant_message(response.text, tool_uses)
            # assistant 回复已在历史中，锚定实际用量；下一轮迭代只需对
            # 下方追加的 tool results 做字符估算。
            conversation.record_usage_anchor(
                response.input_tokens,
                response.output_tokens,
                response.cache_read,
                response.cache_creation,
            )

            tool_results: list[ToolResultBlock] = []
            for tc in response.tool_calls:
                if event_callback:
                    event_callback(
                        {
                            "type": "tool_use",
                            "toolName": tc.tool_name,
                            "args": tc.arguments,
                        }
                    )
                result = await self._execute_tool_noninteractive(tc)
                content = self._maybe_persist_or_truncate(tc.tool_id, result.output)
                tool_results.append(
                    ToolResultBlock(
                        tool_use_id=tc.tool_id,
                        content=content,
                        is_error=result.is_error,
                    )
                )

            conversation.add_tool_results_message(tool_results)

            if self.hook_engine:
                ctx = self._build_hook_context("turn_end")
                await self.hook_engine.run_hooks("turn_end", ctx)

        # agent 已停止：触发 stop 事件，让 verification 等收尾钩子运行（如改完自动跑定向测试）。
        if self.hook_engine:
            await self.hook_engine.run_hooks("stop", self._build_hook_context("stop"))

        return last_text

    async def _execute_tool_noninteractive(self, tc: ToolCallComplete) -> ToolResult:
        tool = self.registry.get(tc.tool_name)

        if tool is None:
            return ToolResult(output=f"Error: unknown tool '{tc.tool_name}'", is_error=True)

        if not self.registry.is_enabled(tc.tool_name):
            return ToolResult(
                output=f"Error: tool '{tc.tool_name}' is disabled",
                is_error=True,
            )

        if self.hook_engine:
            file_path = self._infer_file_path(tc.arguments)
            hook_ctx = self._build_hook_context(
                "pre_tool_use",
                tool_name=tc.tool_name,
                tool_args=tc.arguments,
                file_path=file_path,
            )
            try:
                await self.hook_engine.run_hooks("pre_tool_use", hook_ctx)
            except ToolRejectedError as rej:
                return ToolResult(
                    output=f"Hook rejected: {rej.reason}",
                    is_error=True,
                )

        if self.permission_checker:
            decision = self.permission_checker.check(tool.name, tc.arguments, tool_category=tool.category)
            if decision.effect == "deny":
                return ToolResult(
                    output=f"Permission denied: {decision.reason}",
                    is_error=True,
                )
            if decision.effect == "ask":
                if self.permission_mode == PermissionMode.BYPASS:
                    pass  # 自动批准
                elif decision.needs_classification:
                    # auto 模式：非交互下用 LLM 分类器替代弹框；判定不安全则拒绝。
                    if not await self._classify_command_safe(self._build_permission_description(tc)):
                        return ToolResult(
                            output="Permission denied: auto 分类器判定该命令需人工确认",
                            is_error=True,
                        )
                else:
                    return ToolResult(
                        output="Permission denied: non-interactive agent cannot prompt user",
                        is_error=True,
                    )

        start = time.monotonic()
        try:
            params = tool.params_model.model_validate(tc.arguments)
            result = await tool.execute(params)
        except ValidationError as e:
            result = ToolResult(output=f"Parameter validation error: {e}", is_error=True)
        except Exception as e:
            result = ToolResult(output=f"Tool execution error: {e}", is_error=True)
        self._trace_tool_exec(tc, result, time.monotonic() - start)

        if not result.is_error:
            self._record_changed_file(tc.tool_name, tc.arguments)

        if self.hook_engine:
            file_path = self._infer_file_path(tc.arguments)
            hook_ctx = self._build_hook_context(
                "post_tool_use",
                tool_name=tc.tool_name,
                tool_args=tc.arguments,
                file_path=file_path,
            )
            await self.hook_engine.run_hooks("post_tool_use", hook_ctx)

        return result

    def _maybe_persist_or_truncate(self, tool_use_id: str, text: str) -> str:
        from xhx_agent.context.manager import (
            SINGLE_RESULT_CHAR_LIMIT,
            make_persisted_preview,
            persist_tool_result,
        )

        if len(text) > SINGLE_RESULT_CHAR_LIMIT:
            fp = persist_tool_result(tool_use_id, text, self.session_dir)
            return make_persisted_preview(text, fp)
        if len(text) > MAX_OUTPUT_CHARS:
            return text[:MAX_OUTPUT_CHARS] + "\n… (output truncated)"
        return text
