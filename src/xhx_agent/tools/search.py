from __future__ import annotations

import subprocess
from pathlib import Path


def search(workspace: Path, query: str, glob: str | None = None, max_results: int = 50) -> list[str]:
    if glob:
        from xhx_agent.tools.paths import extract_glob_root
        glob_root = extract_glob_root(workspace, glob)
        last_sep = max(glob.rfind("/"), glob.rfind("\\"))
        rel_glob = glob[last_sep + 1:] if last_sep != -1 else glob
    else:
        glob_root = workspace
        rel_glob = None

    command = ["rg", "--line-number", "--no-heading", query]
    if rel_glob:
        command.extend(["--glob", rel_glob])
    try:
        result = subprocess.run(
            command,
            cwd=glob_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
        lines = result.stdout.splitlines()
        formatted_lines = []
        for line in lines:
            parts = line.split(":", 2)
            if len(parts) >= 2:
                path_part = parts[0]
                full_path = Path(glob_root / path_part).resolve()
                try:
                    rel_path = full_path.relative_to(workspace.resolve()).as_posix()
                except ValueError:
                    rel_path = full_path.as_posix()
                formatted_lines.append(f"{rel_path}:{parts[1]}:{parts[2] if len(parts) > 2 else ''}")
            else:
                formatted_lines.append(line)
        return formatted_lines[:max_results]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return _python_search(glob_root, workspace, query, rel_glob, max_results)


def _python_search(glob_root: Path, workspace: Path, query: str, glob: str | None, max_results: int) -> list[str]:
    pattern = glob or "*"
    results: list[str] = []
    for path in glob_root.rglob(pattern):
        if not path.is_file() or any(
            part in {".git", ".venv", "node_modules"} for part in path.relative_to(glob_root).parts
        ):
            continue
        try:
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if query in line:
                    try:
                        rel_path = path.relative_to(workspace).as_posix()
                    except ValueError:
                        rel_path = path.as_posix()
                    results.append(f"{rel_path}:{index}:{line}")
                    if len(results) >= max_results:
                        return results
        except UnicodeDecodeError:
            continue
    return results
