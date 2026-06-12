import argparse
from pathlib import Path

from rich.console import Console

from xhx_agent.tui.page import render_console_page
from xhx_agent.tui.state import ConsoleState


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=".xhx/render", help="Path to output SVG files")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Streaming in progress state
    state_streaming = ConsoleState(
        status="planning",
        run_id="run_streaming_123",
        task="refactor testing suite to use TDD pattern",
        mode="loop",
        context_turn=2,
        model_output="def test_something():\n    assert True\n",
        model_delta_count=35,
        is_streaming=True,
    )

    # 2. Finished state
    state_finished = ConsoleState(
        status="success",
        run_id="run_finished_456",
        task="refactor testing suite to use TDD pattern",
        mode="loop",
        context_turn=2,
        model_output="def test_something():\n    assert True\n",
        model_delta_count=35,
        is_streaming=False,
    )

    # Output streaming svg
    console_str = Console(width=100, record=True)
    panel_str = render_console_page(state_streaming, workspace="D:/pycharmprojects/XHX-Agent", profile="mock", auto_repair=False, assume_yes=True)
    console_str.print(panel_str)
    svg_streaming = console_str.export_svg(title="xhx-agent (streaming...)")
    (output_dir / "streaming.svg").write_text(svg_streaming, encoding="utf-8")

    # Output finished svg
    console_fin = Console(width=100, record=True)
    panel_fin = render_console_page(state_finished, workspace="D:/pycharmprojects/XHX-Agent", profile="mock", auto_repair=False, assume_yes=True)
    console_fin.print(panel_fin)
    svg_finished = console_fin.export_svg(title="xhx-agent (finished)")
    (output_dir / "finished.svg").write_text(svg_finished, encoding="utf-8")

    print(f"SVGs successfully exported to {output_dir}")

if __name__ == "__main__":
    main()
