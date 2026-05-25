from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class PatchResult(BaseModel):
    status: str
    changed_files: list[str]
    stdout: str = ""
    stderr: str = ""


class _PatchHunk(BaseModel):
    old: str
    new: str


class _PatchOperation(BaseModel):
    kind: Literal["add", "update"]
    path: str
    content: str = ""
    hunks: list[_PatchHunk] = []


def apply_patch(workspace: Path, patch_text: str) -> PatchResult:
    """Apply the v0.1 structured patch subset atomically.

    Supported operations:
    - *** Add File: path
    - *** Update File: path with one or more @@ hunks
    """

    try:
        operations = _parse_patch(patch_text)
        planned_writes = _plan_writes(workspace, operations)
    except Exception as exc:  # noqa: BLE001 - tool result should be structured
        return PatchResult(status="failed", changed_files=[], stderr=str(exc))

    for target, content in planned_writes:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    changed_files = [_relative_path(workspace, target) for target, _content in planned_writes]
    return PatchResult(
        status="success",
        changed_files=sorted(dict.fromkeys(changed_files)),
        stdout=f"changed files: {', '.join(sorted(dict.fromkeys(changed_files)))}",
    )


def _parse_patch(patch_text: str) -> list[_PatchOperation]:
    lines = patch_text.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("Patch must start with *** Begin Patch and end with *** End Patch.")

    operations: list[_PatchOperation] = []
    index = 1
    while index < len(lines) - 1:
        line = lines[index]
        if line.startswith("*** Add File: "):
            operation, index = _parse_add_file(lines, index)
            operations.append(operation)
            continue
        if line.startswith("*** Update File: "):
            operation, index = _parse_update_file(lines, index)
            operations.append(operation)
            continue
        raise ValueError(f"Unsupported patch operation line: {line}")

    if not operations:
        raise ValueError("Patch must contain at least one file operation.")
    paths = [operation.path for operation in operations]
    if len(paths) != len(set(paths)):
        raise ValueError("Patch cannot contain multiple operations for the same file.")
    return operations


def _parse_add_file(lines: list[str], index: int) -> tuple[_PatchOperation, int]:
    path = _validate_relative_path(lines[index].removeprefix("*** Add File: ").strip())
    index += 1
    content_lines: list[str] = []
    while index < len(lines) - 1 and not lines[index].startswith("*** "):
        line = lines[index]
        if not line.startswith("+"):
            raise ValueError(f"Add File lines must start with '+': {line}")
        content_lines.append(line[1:])
        index += 1
    return _PatchOperation(kind="add", path=path, content="\n".join(content_lines) + "\n"), index


def _parse_update_file(lines: list[str], index: int) -> tuple[_PatchOperation, int]:
    path = _validate_relative_path(lines[index].removeprefix("*** Update File: ").strip())
    index += 1
    hunks: list[_PatchHunk] = []
    while index < len(lines) - 1 and not lines[index].startswith("*** "):
        if not lines[index].startswith("@@"):
            raise ValueError(f"Update File expected hunk header '@@', got: {lines[index]}")
        index += 1
        old_lines: list[str] = []
        new_lines: list[str] = []
        while index < len(lines) - 1 and not lines[index].startswith("@@") and not lines[index].startswith("*** "):
            line = lines[index]
            if line.startswith("-"):
                old_lines.append(line[1:])
            elif line.startswith("+"):
                new_lines.append(line[1:])
            elif line.startswith(" "):
                old_lines.append(line[1:])
                new_lines.append(line[1:])
            elif line == "":
                old_lines.append("")
                new_lines.append("")
            else:
                raise ValueError(f"Unsupported patch hunk line: {line}")
            index += 1
        if not old_lines and not new_lines:
            raise ValueError(f"Patch hunk is empty for {path}.")
        hunks.append(_PatchHunk(old="\n".join(old_lines), new="\n".join(new_lines)))

    if not hunks:
        raise ValueError(f"Update File operation has no hunks: {path}")
    return _PatchOperation(kind="update", path=path, hunks=hunks), index


def _plan_writes(workspace: Path, operations: list[_PatchOperation]) -> list[tuple[Path, str]]:
    planned: list[tuple[Path, str]] = []
    for operation in operations:
        target = _resolve_inside(workspace, operation.path)
        if operation.kind == "add":
            if target.exists():
                raise FileExistsError(f"Cannot add file that already exists: {operation.path}")
            planned.append((target, operation.content))
            continue

        if not target.is_file():
            raise FileNotFoundError(operation.path)
        original = target.read_text(encoding="utf-8")
        updated = original
        for hunk in operation.hunks:
            if hunk.old not in updated:
                raise ValueError(f"Patch context not found in {operation.path}.")
            updated = updated.replace(hunk.old, hunk.new, 1)
        planned.append((target, updated))
    return planned


def _validate_relative_path(path: str) -> str:
    if not path:
        raise ValueError("Patch path is empty.")
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Invalid patch path: {path}")
    return path.replace("\\", "/")


def _resolve_inside(workspace: Path, path: str) -> Path:
    target = (workspace / path).resolve()
    root = workspace.resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"Patch path is outside workspace: {path}")
    return target


def _relative_path(workspace: Path, target: Path) -> str:
    return target.resolve().relative_to(workspace.resolve()).as_posix()
