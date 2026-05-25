from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from xhx_agent.runtime.paths import ensure_xhx_dirs, xhx_dir


class RawTraceEntry(BaseModel):
    type: str
    id: str = Field(default_factory=lambda: f"trace_{uuid4().hex}")
    run_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceEntry(BaseModel):
    id: str = Field(default_factory=lambda: f"ev_{uuid4().hex}")
    kind: str
    source: str
    summary: str
    artifact_ref: str
    confidence: float = 0.8
    task_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class EvidenceStore:
    def __init__(self, workspace: Path, run_id: str) -> None:
        self.workspace = workspace
        self.run_id = run_id
        ensure_xhx_dirs(workspace)
        self.trace_path = xhx_dir(workspace) / "traces" / f"{run_id}.jsonl"
        self.evidence_path = xhx_dir(workspace) / "evidence" / f"{run_id}.jsonl"

    def write_trace(self, entry_type: str, payload: dict[str, Any]) -> RawTraceEntry:
        entry = RawTraceEntry(type=entry_type, run_id=self.run_id, payload=payload)
        self._append_jsonl(self.trace_path, entry.model_dump())
        return entry

    def write_evidence(
        self,
        kind: str,
        source: str,
        summary: str,
        artifact_ref: str,
        confidence: float = 0.8,
        task_id: str | None = None,
    ) -> EvidenceEntry:
        entry = EvidenceEntry(
            kind=kind,
            source=source,
            summary=summary,
            artifact_ref=artifact_ref,
            confidence=confidence,
            task_id=task_id,
        )
        self._append_jsonl(self.evidence_path, entry.model_dump())
        return entry

    def _append_jsonl(self, path: Path, data: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")
