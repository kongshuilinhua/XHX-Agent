from __future__ import annotations

from types import TracebackType

from rich.console import Console
from rich.live import Live

from xhx_agent.tui.page import render_console_page
from xhx_agent.tui.state import ConsoleState


class LiveDashboard:
    """Small Rich Live wrapper for the v0.5 command console."""

    def __init__(
        self,
        console: Console,
        state: ConsoleState,
        *,
        workspace: str,
        profile: str,
        auto_repair: bool,
        assume_yes: bool,
        refresh_per_second: int = 8,
    ) -> None:
        self.console = console
        self.state = state
        self.workspace = workspace
        self.profile = profile
        self.auto_repair = auto_repair
        self.assume_yes = assume_yes
        self.refresh_per_second = refresh_per_second
        self._live: Live | None = None

    def __enter__(self) -> LiveDashboard:
        self._live = Live(
            self.render(),
            console=self.console,
            refresh_per_second=self.refresh_per_second,
            transient=False,
        )
        self._live.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc, traceback)
            self._live = None

    def update_options(self, *, profile: str, auto_repair: bool, assume_yes: bool) -> None:
        self.profile = profile
        self.auto_repair = auto_repair
        self.assume_yes = assume_yes

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self.render(), refresh=True)

    def render(self):
        return render_console_page(
            self.state,
            workspace=self.workspace,
            profile=self.profile,
            auto_repair=self.auto_repair,
            assume_yes=self.assume_yes,
        )
