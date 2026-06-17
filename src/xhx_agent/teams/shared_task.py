"""共享任务看板。来源：mewcode teams/shared_task.py。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SharedTask:
    id: str
    title: str
    description: str = ""
    status: str = "pending"
    assignee: str = ""
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    created_by: str = ""


class SharedTaskStore:
    def __init__(self, file_path: Path) -> None:
        self._path = file_path
        self._next_id: int = 1
        self._tasks: list[SharedTask] = []
        self._load()  # 尝试加载已有数据，不覆盖

    def _load(self) -> None:
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._next_id = data.get("next_id", 1)
                self._tasks = [SharedTask(**t) for t in data.get("tasks", [])]
            except (json.JSONDecodeError, TypeError):
                pass

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({
            "next_id": self._next_id,
            "tasks": [t.__dict__ for t in self._tasks],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def init_empty(self) -> None:
        self._next_id = 1
        self._tasks = []
        self._save()

    def create(self, title: str, description: str = "", assignee: str = "",
               blocks: list[str] | None = None, blocked_by: list[str] | None = None,
               created_by: str = "") -> SharedTask:
        self._load()
        task = SharedTask(
            id=str(self._next_id), title=title, description=description,
            assignee=assignee, blocks=blocks or [],
            blocked_by=blocked_by or [], created_by=created_by,
        )
        self._next_id += 1
        self._tasks.append(task)
        self._save()
        return task

    def get(self, task_id: str) -> SharedTask | None:
        self._load()
        for t in self._tasks:
            if t.id == task_id:
                return t
        return None

    def list_tasks(self, status: str | None = None, assignee: str | None = None) -> list[SharedTask]:
        self._load()
        result = self._tasks
        if status:
            result = [t for t in result if t.status == status]
        if assignee:
            result = [t for t in result if t.assignee == assignee]
        return result

    def update(self, task_id: str, status: str | None = None, assignee: str | None = None,
               description: str | None = None) -> SharedTask | None:
        self._load()
        task = self.get(task_id)
        if task is None:
            return None
        if status is not None:
            task.status = status
        if assignee is not None:
            task.assignee = assignee
        if description is not None:
            task.description = description
        self._save()
        return task
