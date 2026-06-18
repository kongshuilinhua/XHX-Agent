"""命令补全系统。"""
from textual.widgets import Static


class CompletionPopup(Static):
    """补全弹出框。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__("", id="completion-popup", **kwargs)
        self._items: list[str] = []
        self._selected_idx: int = -1
        self.is_visible: bool = False
        self.display = False

    def show_items(self, items: list[str]) -> None:
        self._items = items
        self._selected_idx = 0 if items else -1
        if items:
            text = "\n".join(
                f"{'> ' if i == self._selected_idx else '  '}{item}"
                for i, item in enumerate(items[:10])
            )
            self.update(text)
            self.display = True
            self.is_visible = True
        else:
            self.display = False
            self.is_visible = False

    def hide(self) -> None:
        self.display = False
        self.is_visible = False
        self._items = []
        self._selected_idx = -1

    def get_selected(self) -> str | None:
        if 0 <= self._selected_idx < len(self._items):
            return self._items[self._selected_idx]
        return None

    def nav_up(self) -> None:
        if self._items:
            self._selected_idx = (self._selected_idx - 1) % len(self._items)
            self._refresh_display()

    def nav_down(self) -> None:
        if self._items:
            self._selected_idx = (self._selected_idx + 1) % len(self._items)
            self._refresh_display()

    def _refresh_display(self) -> None:
        text = "\n".join(
            f"{'> ' if i == self._selected_idx else '  '}{item}"
            for i, item in enumerate(self._items[:10])
        )
        self.update(text)


# 重导出 XhxCompleter 用于老代码
from xhx_agent.cli.completion import XhxCompleter  # noqa: E402, F401
