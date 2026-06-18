from pathlib import Path

from xhx_agent.repo_intel.index import load_repo_intel_index


class XhxCompleter:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.commands = [
            "/help",
            "/exit",
            "/model",
            "/plan",
            "/clear",
            "/verbose",
            "/permission",
            "/cancel",
            "/tools",
            "/new",
            "/allow",
            "/deny",
            "/status",
            "/compact",
            "/memory",
            "/mcp",
            "/review",
            "/rewind",
            "/session",
            "/skill",
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

        # 2. 如果包含路径分隔符或以点开头，补全文件路径
        if "/" in text or "\\" in text or text.startswith("."):
            return self._get_path_completions(text)

        # 3. 混合补全：支持符号名与前缀路径
        return self._get_symbol_completions(text) + self._get_path_completions(text)

    def _get_path_completions(self, prefix: str) -> list[str]:
        prefix_normalized = prefix.replace("\\", "/")
        # 空前缀不做整仓遍历（否则在 UI 线程上 glob("**/*") 卡顿）。
        if not prefix_normalized:
            return []
        index = self.get_index()
        paths = []
        if index and index.repo_map:
            paths = [f.path for f in index.repo_map.files]
        else:
            try:
                paths = [
                    str(p.relative_to(self.workspace)).replace("\\", "/")
                    for p in self.workspace.glob("**/*")
                    if p.is_file()
                ]
            except Exception:
                pass
        return [p for p in paths if p.startswith(prefix_normalized)][:50]

    def _get_symbol_completions(self, prefix: str) -> list[str]:
        if len(prefix) < 2:
            return []
        index = self.get_index()
        if not index or not index.symbol_index:
            return []
        symbols = {s.name for s in index.symbol_index.symbols}
        return [sym for sym in symbols if sym.lower().startswith(prefix.lower())]
