from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class PatchResult(BaseModel):
    status: str
    changed_files: list[str]
    stdout: str = ""
    stderr: str = ""


def apply_patch(workspace: Path, patch_text: str) -> PatchResult:
    operation = _parse_update_patch(patch_text)
    target = _resolve_inside(workspace, operation.path)
    original = target.read_text(encoding="utf-8")
    if operation.old not in original:
        return PatchResult(
            status="failed",
            changed_files=[],
            stderr=f"Patch context not found in {operation.path}.",
        )
    updated = original.replace(operation.old, operation.new, 1)
    target.write_text(updated, encoding="utf-8")
    return PatchResult(status="success", changed_files=[operation.path])


class _PatchOperation(BaseModel):
    path: str
    old: str
    new: str


def _parse_update_patch(patch_text: str) -> _PatchOperation:
    lines = patch_text.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("Patch must start with *** Begin Patch and end with *** End Patch.")
    update_lines = [line for line in lines if line.startswith("*** Update File: ")]
    if len(update_lines) != 1:
        raise ValueError("v0.1 apply_patch supports exactly one Update File operation.")
    path = update_lines[0].removeprefix("*** Update File: ").strip()
    if not path or Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError(f"Invalid patch path: {path}")

    old_lines: list[str] = []
    new_lines: list[str] = []
    in_hunk = False
    for line in lines:
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("*** End Patch"):
            break
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

    if not old_lines and not new_lines:
        raise ValueError("Patch hunk is empty.")
    return _PatchOperation(path=path, old="\n".join(old_lines), new="\n".join(new_lines))


def _resolve_inside(workspace: Path, path: str) -> Path:
    target = (workspace / path).resolve()
    root = workspace.resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"Patch path is outside workspace: {path}")
    if not target.is_file():
        raise FileNotFoundError(path)
    return target
