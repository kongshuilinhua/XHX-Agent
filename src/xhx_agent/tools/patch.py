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
    """Parse a patch into structured operations, accepting the formats real LLMs emit.

    Dispatch by shape after stripping any markdown code fence:
    - ``*** Begin Patch`` envelope (the project's v0.1 format) -> _parse_envelope
    - standard unified diff (``--- ``/``+++ ``/``@@``) -> _parse_unified_diff
    Unknown input falls through to the envelope parser so its strict error is preserved.
    """

    lines = _strip_fences(patch_text).splitlines()
    if lines and lines[0] == "*** Begin Patch":
        return _parse_envelope(lines)
    if _looks_like_unified_diff(lines):
        return _parse_unified_diff(lines)
    return _parse_envelope(lines)


def _strip_fences(text: str) -> str:
    """Strip a wrapping markdown code fence (``` / ```diff / ```patch) and surrounding blanks."""

    lines = text.strip().splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    return "\n".join(lines).strip("\n")


def _parse_envelope(lines: list[str]) -> list[_PatchOperation]:
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


def _looks_like_unified_diff(lines: list[str]) -> bool:
    return any(line.startswith(("--- ", "diff --git ", "@@")) for line in lines)


def _strip_ab_prefix(path: str) -> str:
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def _parse_unified_diff(lines: list[str]) -> list[_PatchOperation]:
    """Parse a standard unified diff into the same _PatchOperation list the envelope produces.

    Hunk @@ line numbers are ignored; matching is by context (like the envelope parser), so a
    correct context/removed block must exist verbatim in the target. ``--- /dev/null`` => add file.
    """

    operations: list[_PatchOperation] = []
    current_path: str | None = None
    is_add = False
    hunks: list[_PatchHunk] = []
    add_content: list[str] = []
    index = 0
    total = len(lines)

    def flush() -> None:
        nonlocal current_path, is_add, hunks, add_content
        if current_path is None:
            return
        if is_add:
            operations.append(_PatchOperation(kind="add", path=current_path, content="\n".join(add_content) + "\n"))
        elif hunks:
            operations.append(_PatchOperation(kind="update", path=current_path, hunks=list(hunks)))
        else:
            raise ValueError(f"Unified diff has no hunks for {current_path}.")
        current_path, is_add, hunks, add_content = None, False, [], []

    while index < total:
        line = lines[index]
        if line.startswith(("diff --git ", "index ", "new file mode", "deleted file mode", "similarity ", "rename ")):
            index += 1
            continue
        if line.startswith("--- "):
            flush()
            old_path = line[4:].strip()
            if index + 1 >= total or not lines[index + 1].startswith("+++ "):
                raise ValueError("Unified diff '---' header not followed by '+++'.")
            new_path = lines[index + 1][4:].strip()
            is_add = old_path.endswith("/dev/null")
            target = old_path if new_path.endswith("/dev/null") else new_path
            current_path = _validate_relative_path(_strip_ab_prefix(target))
            index += 2
            continue
        if line.startswith("@@"):
            index += 1
            old_lines: list[str] = []
            new_lines: list[str] = []
            while index < total and not lines[index].startswith(("@@", "--- ", "diff --git ")):
                hunk_line = lines[index]
                if hunk_line.startswith("\\"):  # "\ No newline at end of file"
                    index += 1
                    continue
                if hunk_line.startswith("+"):
                    new_lines.append(hunk_line[1:])
                    add_content.append(hunk_line[1:])
                elif hunk_line.startswith("-"):
                    old_lines.append(hunk_line[1:])
                elif hunk_line.startswith(" "):
                    old_lines.append(hunk_line[1:])
                    new_lines.append(hunk_line[1:])
                elif hunk_line == "":
                    old_lines.append("")
                    new_lines.append("")
                else:
                    break
                index += 1
            if not is_add:
                hunks.append(_PatchHunk(old="\n".join(old_lines), new="\n".join(new_lines)))
            continue
        index += 1
    flush()

    if not operations:
        raise ValueError("Unified diff contained no file operations.")
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
    return path.replace("\\", "/")


def _resolve_inside(workspace: Path, path: str) -> Path:
    return Path(workspace / path).resolve()


def _relative_path(workspace: Path, target: Path) -> str:
    try:
        return target.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return target.resolve().as_posix()
