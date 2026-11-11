from __future__ import annotations

from .models import Task


class TaskService:
    def __init__(self, tasks: list[Task] | None = None) -> None:
        self._tasks = list(tasks or [])

    def list_tasks(self, status: str | None = None) -> list[Task]:
        """Return tasks in input order, optionally filtered by status."""
        tasks = list(self._tasks)
        if status is None:
            return tasks
        return [task for task in tasks if task.status == status]
