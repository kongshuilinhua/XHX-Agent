"""静态核对：项目内每一条 import（含函数内局部导入、TYPE_CHECKING 块）都指向真实存在的
模块，且 `from xhx_agent... import <name>` 的每个名字都真实存在（属性或子模块）。

动机：模块级坏导入会被普通导入冒烟发现，但**函数内的局部导入**只有命中那条代码路径才会
炸（曾出现 `from xhx_agent.permissions.rules import ...` 这类旧路径，平时测不出、运行时才崩）。
本测试用 AST 静态遍历，不执行业务代码路径即可全覆盖，纳入 CI 防止再犯。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"


def _module_name(path: Path) -> str:
    rel = path.relative_to(_SRC).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolvable(modname: str) -> bool:
    try:
        return importlib.util.find_spec(modname) is not None
    except Exception:
        return False


def _collect_problems() -> list[str]:
    problems: list[str] = []
    for py in sorted(_SRC.rglob("*.py")):
        if ".venv" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"), filename=str(py))
        except SyntaxError as e:  # pragma: no cover - 不应发生
            problems.append(f"{py}:{e.lineno} SyntaxError: {e}")
            continue
        loc = py.relative_to(_SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not _resolvable(alias.name):
                        problems.append(f"{loc}:{node.lineno} import {alias.name} -> 模块不存在")
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # 相对导入：项目内一律绝对导入，跳过
                    continue
                mod = node.module or ""
                if mod == "__future__":
                    continue
                if not _resolvable(mod):
                    problems.append(f"{loc}:{node.lineno} from {mod} import ... -> 模块不存在")
                    continue
                # 仅深入校验内部包的名字（外部库属性差异多，避免误报）
                if not mod.startswith("xhx_agent"):
                    continue
                try:
                    m = importlib.import_module(mod)
                except Exception as e:
                    problems.append(f"{loc}:{node.lineno} from {mod} import ... -> 导入崩 {type(e).__name__}: {e}")
                    continue
                for alias in node.names:
                    if alias.name == "*" or hasattr(m, alias.name) or _resolvable(f"{mod}.{alias.name}"):
                        continue
                    problems.append(f"{loc}:{node.lineno} from {mod} import {alias.name} -> 名字不存在")
    return problems


def test_all_imports_resolve() -> None:
    problems = _collect_problems()
    assert not problems, "存在指向不存在模块/名字的 import：\n" + "\n".join(problems)
