"""路径沙箱：确保文件操作不超出允许的目录范围。
"""

from __future__ import annotations

import tempfile
from pathlib import Path


class PathSandbox:
    """检查文件路径是否在允许的根目录集合内。

    自动包含项目根目录和系统临时目录；可通过 *extra_allowed* 扩展。
    """

    def __init__(
        self,
        project_root: str,
        extra_allowed: list[str] | None = None,
    ) -> None:
        root = Path(project_root).resolve()
        self._allowed_roots: list[Path] = [root, Path(tempfile.gettempdir()).resolve()]
        if extra_allowed:
            for p in extra_allowed:
                self._allowed_roots.append(Path(p).resolve())

    @property
    def project_root(self) -> Path:
        return self._allowed_roots[0]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def check(self, path: str) -> tuple[bool, str]:
        """返回 ``(ok, reason)``。

        路径可以是相对路径（相对于 project_root）或绝对路径。
        对不存在的路径，沿祖先链向上查找第一个存在的目录来解析。
        """
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.project_root / p
        abs_path = p.absolute()

        try:
            real_path = abs_path.resolve(strict=True)
        except OSError:
            # 路径不存在 → 沿祖先链向上找第一个存在的
            ancestor = abs_path
            while not ancestor.exists():
                parent = ancestor.parent
                if parent == ancestor:  # 到达根，无路可退
                    return False, f"无法解析路径: {path}"
                ancestor = parent
            try:
                resolved_ancestor = ancestor.resolve(strict=True)
            except OSError:
                return False, f"无法解析路径: {path}"
            real_path = resolved_ancestor / abs_path.relative_to(ancestor)

        # 检查是否在任一允许根内
        for root in self._allowed_roots:
            try:
                real_path.relative_to(root)
                return True, ""
            except ValueError:
                continue

        return False, f"路径 {path} 超出沙箱范围"

    def add_allowed_root(self, path: str) -> None:
        """动态添加允许的根目录。"""
        resolved = Path(path).resolve()
        if resolved not in self._allowed_roots:
            self._allowed_roots.append(resolved)
