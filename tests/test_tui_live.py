from rich.console import Console

from xhx_agent.tui.live import LiveDashboard
from xhx_agent.tui.state import ConsoleState


def test_live_dashboard_render_uses_current_options() -> None:
    state = ConsoleState()
    dashboard = LiveDashboard(
        Console(record=True, force_terminal=False, width=120),
        state,
        workspace="D:/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=False,
    )

    dashboard.update_options(profile="real", auto_repair=True, assume_yes=True)
    renderable = dashboard.render()

    console = Console(record=True, force_terminal=False, width=120)
    console.print(renderable)
    output = console.export_text()

    assert "profile: real" in output
    assert "repair:on" in output
    assert "yes:on" in output
