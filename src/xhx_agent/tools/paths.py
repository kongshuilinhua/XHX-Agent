from dataclasses import dataclass
from pathlib import Path


@dataclass
class ResolvedScope:
    target: Path
    in_scope: bool
    outside_root: Path | None


def resolve_with_scope(workspace: Path, allowed_dirs: list[Path], path: str | Path) -> ResolvedScope:
    workspace_resolved = Path(workspace).resolve()
    target = Path(workspace_resolved / path).resolve()

    # Check if target is inside workspace
    if workspace_resolved == target or workspace_resolved in target.parents:
        return ResolvedScope(target=target, in_scope=True, outside_root=None)

    # Check if target is inside allowed_dirs
    for allowed in allowed_dirs:
        allowed_resolved = Path(allowed).resolve()
        if allowed_resolved == target or allowed_resolved in target.parents:
            return ResolvedScope(target=target, in_scope=True, outside_root=None)

    # Outside scope
    # Find parent of the target to use as the directory to allow
    outside_root = target.parent
    return ResolvedScope(target=target, in_scope=False, outside_root=outside_root)


def extract_glob_root(workspace: Path, glob: str) -> Path:
    workspace_resolved = Path(workspace).resolve()
    # Find the prefix before any wildcard character
    wildcards = ["*", "?", "["]
    first_wildcard = len(glob)
    for w in wildcards:
        idx = glob.find(w)
        if idx != -1 and idx < first_wildcard:
            first_wildcard = idx
    prefix = glob[:first_wildcard]

    # If the prefix has a directory part, e.g. contains '/' or '\'
    if "/" in prefix or "\\" in prefix:
        # Find the last directory separator in the prefix
        last_sep = max(prefix.rfind("/"), prefix.rfind("\\"))
        dir_part = prefix[:last_sep]
        return Path(workspace_resolved / dir_part).resolve()
    elif prefix.startswith("..") or Path(prefix).is_absolute():
        return Path(workspace_resolved / prefix).resolve()
    return workspace_resolved
