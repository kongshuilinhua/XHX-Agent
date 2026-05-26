from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.tui.state import ConsoleState
from xhx_agent.tui.textual_app import TextualCommandConsoleApp, TextualSnapshot


def test_textual_snapshot_from_console_state_shows_status_and_commands() -> None:
    state = ConsoleState()
    state.reduce(
        RuntimeEvent(
            type="run_start",
            message="Run started.",
            payload={"run_id": "run-1", "task": "fix tests", "profile": "mock"},
        )
    )
    state.reduce(
        RuntimeEvent(
            type="context_pack",
            message="Context pack compiled.",
            payload={"turn": 1, "selected": 3, "omitted": 1, "used_tokens_estimate": 120, "budget_tokens": 6000},
        )
    )
    state.reduce(RuntimeEvent(type="model_plan", message="Patch failing test", payload={"status": "continue", "step_count": 1}))
    state.reduce(
        RuntimeEvent(
            type="run_end",
            message="Run finished.",
            payload={"status": "success", "verification": "passed", "changed_files": ["src/calc.py"], "summary_path": ".xhx/logbook/run-1.md"},
        )
    )

    snapshot = TextualSnapshot.from_state(
        state,
        workspace="D:/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=True,
    )

    assert snapshot.header == "xhx-agent | success | profile: mock | run: run-1"
    assert "fix tests" in snapshot.conversation
    assert "Patch failing test" in snapshot.conversation
    assert "verification: passed" in snapshot.runtime_state
    assert "context: 120/6000" in snapshot.runtime_state
    assert "src/calc.py" in snapshot.changed_files
    assert "/help /model /status" in snapshot.commands


def test_textual_command_console_app_can_render_initial_shell(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            assert app.title.startswith("xhx-agent | idle | profile: mock")
            assert "No conversation yet." in str(pilot.app.query_one("#conversation").content)
            assert "changed files:" in str(pilot.app.query_one("#changed").content)

    import asyncio

    asyncio.run(run_app())


def test_textual_command_console_handles_read_only_slash_commands(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    assert app.handle_text_input("/help")
    assert "available commands" in app.messages[-1]

    assert app.handle_text_input("/status")
    assert "verification: not_started" in app.messages[-1]

    assert app.handle_text_input("/unknown")
    assert "Unknown command: /unknown" in app.messages[-1]


def test_textual_command_console_defers_task_execution_to_rich_console(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    assert app.handle_text_input("fix tests")

    assert "not wired into fullscreen mode yet" in app.messages[-1]
    assert app.state.task is None


def test_textual_command_console_clear_and_exit_are_local(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    app.messages.append("system> old")
    app.state.reduce(RuntimeEvent(type="run_start", message="Run started.", payload={"run_id": "run-1", "task": "fix", "profile": "mock"}))

    assert app.handle_text_input("/clear")
    assert app.messages == []
    assert app.state.status == "idle"

    assert app.handle_text_input("/exit") is False
    assert app.exit_requested is True


def test_textual_command_console_submitted_input_updates_window(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            await pilot.press("/", "h", "e", "l", "p", "enter")
            assert "available commands" in str(pilot.app.query_one("#conversation").content)

    import asyncio

    asyncio.run(run_app())
