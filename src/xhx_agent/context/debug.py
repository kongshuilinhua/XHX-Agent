from __future__ import annotations

import json
from pathlib import Path

from xhx_agent.context.pack import ContextPack
from xhx_agent.runtime.paths import ensure_xhx_dirs, xhx_dir


def write_context_debug_report(workspace: Path, run_id: str, turn: int, pack: ContextPack) -> Path:
    ensure_xhx_dirs(workspace)
    path = context_debug_path(workspace, run_id, turn)
    payload = {
        "run_id": run_id,
        "turn": turn,
        "task": pack.task,
        "mode": pack.mode,
        "budget_tokens": pack.budget_tokens,
        "used_tokens_estimate": pack.used_tokens_estimate,
        "selected": [item.model_dump() for item in pack.items],
        "omitted": pack.omitted,
        "debug": pack.debug.model_dump() if pack.debug else None,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def context_debug_path(workspace: Path, run_id: str, turn: int) -> Path:
    return xhx_dir(workspace) / "context" / f"{run_id}-turn-{turn}.json"
