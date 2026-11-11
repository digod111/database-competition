from __future__ import annotations

from .models import Task


class TaskService:
    def __init__(self, tasks: list[Task] | None = None) -> None:
        self._tasks = list(tasks or [])

    def list_tasks(
        self, status: str | None = None, min_priority: int | None = None
    ) -> list[Task]:
        """Return tasks in input order, optionally filtered by status and priority."""
        if min_priority is not None and not 1 <= min_priority <= 5:
            raise ValueError("min_priority must be between 1 and 5")

        tasks = list(self._tasks)
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        if min_priority is not None:
            tasks = [task for task in tasks if task.priority >= min_priority]
        return tasks
