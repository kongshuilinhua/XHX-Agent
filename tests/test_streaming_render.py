from pathlib import Path

from rich.console import Console

from xhx_agent.cli.console import CommandConsole
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.tui.page import render_console_page
from xhx_agent.tui.state import ConsoleState


def test_streaming_incremental_render():
    state = ConsoleState()

    # Send model_delta
    event_delta = RuntimeEvent(type="model_delta", message="Hello world", payload={})
    state.reduce(event_delta)

    assert state.is_streaming is True
    assert state.model_delta_count == 1
    assert state.model_output == "Hello world"

    console = Console(width=80)
    with console.capture() as capture:
        console.print(render_console_page(state, workspace="dummy_ws", profile="mock", auto_repair=False, assume_yes=False))
    output_str = capture.get()

    # Must show streaming indicator and cursor
    assert "model (streaming...)>" in output_str
    assert "Hello world▌" in output_str

    # Send model_plan (stops streaming)
    event_plan = RuntimeEvent(type="model_plan", message="Finished plan", payload={"status": "done"})
    state.reduce(event_plan)

    assert state.is_streaming is False
    with console.capture() as capture2:
        console.print(render_console_page(state, workspace="dummy_ws", profile="mock", auto_repair=False, assume_yes=False))
    output_str2 = capture2.get()

    assert "model>" in output_str2
    assert "▌" not in output_str2


def test_fine_status_line():
    state = ConsoleState(status="planning", mode="loop", context_turn=3, model_delta_count=42, is_streaming=True)

    console = Console(width=80)
    with console.capture() as capture:
        console.print(render_console_page(state, workspace="dummy_ws", profile="mock", auto_repair=False, assume_yes=False))
    output_str = capture.get()

    # Status line must contain key fields: state, mode, turn, tokens, streaming
    assert "state: planning" in output_str
    assert "mode: loop" in output_str
    assert "turn: 3" in output_str
    assert "tokens: 42" in output_str
    assert "streaming: yes" in output_str


def test_debouncing_and_immediate_refresh(tmp_path, monkeypatch):
    RuntimeApp(tmp_path).init_project()
    command_console = CommandConsole(tmp_path, console=Console(), live_enabled=True)

    class TrackedLiveDashboard:
        def __init__(self, *args, **kwargs):
            self.refresh_calls = []

        def update_options(self, *, profile: str, auto_repair: bool, assume_yes: bool) -> None:
            pass

        def refresh(self, refresh: bool = True) -> None:
            self.refresh_calls.append(refresh)

    tracker = TrackedLiveDashboard()
    command_console.live_dashboard = tracker  # type: ignore

    # Send model_delta event -> should refresh=False
    command_console.handle_event(RuntimeEvent(type="model_delta", message="token", payload={}))
    assert tracker.refresh_calls == [False]

    # Send tool_start event -> should refresh=True
    command_console.handle_event(RuntimeEvent(type="tool_start", message="tool", payload={"tool": "apply_patch"}))
    assert tracker.refresh_calls == [False, True]


def test_render_script_output(tmp_path):
    # Run the script to render SVG and assert output
    script_path = Path("scripts/render_dashboard.py")
    assert script_path.exists(), "scripts/render_dashboard.py must exist"

    output_dir = tmp_path / "render"
    output_dir.mkdir(parents=True, exist_ok=True)

    # We execute python scripts/render_dashboard.py using subprocess, passing output path if parameterized
    import subprocess
    subprocess.run(
        ["uv", "run", "python", str(script_path), "--output-dir", str(output_dir)],
        capture_output=True,
        text=True,
        check=True
    )

    streaming_svg = output_dir / "streaming.svg"
    finished_svg = output_dir / "finished.svg"

    assert streaming_svg.exists()
    assert finished_svg.exists()
    assert "<svg" in streaming_svg.read_text(encoding="utf-8")
    assert "<svg" in finished_svg.read_text(encoding="utf-8")
