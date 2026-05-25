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


class ArtifactExpansion(BaseModel):
    artifact_ref: str
    status: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)


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

    def list_traces(self) -> list[RawTraceEntry]:
        return [RawTraceEntry(**item) for item in self._read_jsonl(self.trace_path)]

    def list_evidence(self) -> list[EvidenceEntry]:
        return [EvidenceEntry(**item) for item in self._read_jsonl(self.evidence_path)]

    def get_evidence(self, evidence_id: str) -> EvidenceEntry | None:
        for entry in self.list_evidence():
            if entry.id == evidence_id:
                return entry
        return None

    def expand_artifact_ref(self, artifact_ref: str) -> ArtifactExpansion:
        if artifact_ref.startswith("trace://"):
            return self._expand_trace_ref(artifact_ref)
        if artifact_ref.startswith("checkpoint://"):
            return self._expand_checkpoint_ref(artifact_ref)
        return ArtifactExpansion(
            artifact_ref=artifact_ref,
            status="unsupported",
            summary="Unsupported artifact reference scheme.",
        )

    def _append_jsonl(self, path: Path, data: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _expand_trace_ref(self, artifact_ref: str) -> ArtifactExpansion:
        trace_id = artifact_ref.removeprefix("trace://")
        if "/" in trace_id:
            trace_id = trace_id.rsplit("/", 1)[-1]
        for entry in self.list_traces():
            if entry.id == trace_id:
                return ArtifactExpansion(
                    artifact_ref=artifact_ref,
                    status="found",
                    summary=f"Trace {entry.type} found.",
                    payload=entry.model_dump(),
                )
        return ArtifactExpansion(
            artifact_ref=artifact_ref,
            status="missing",
            summary="Trace reference was not found in this run.",
        )

    def _expand_checkpoint_ref(self, artifact_ref: str) -> ArtifactExpansion:
        checkpoint_id = artifact_ref.removeprefix("checkpoint://")
        for trace in self.list_traces():
            payload = trace.payload
            if payload.get("id") == checkpoint_id:
                return ArtifactExpansion(
                    artifact_ref=artifact_ref,
                    status="found",
                    summary=f"Checkpoint artifact {checkpoint_id} found in trace.",
                    payload=payload,
                )
        return ArtifactExpansion(
            artifact_ref=artifact_ref,
            status="missing",
            summary="Checkpoint reference was not found in this run.",
        )
