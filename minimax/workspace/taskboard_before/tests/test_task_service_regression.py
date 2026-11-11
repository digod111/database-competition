from __future__ import annotations

from taskboard.models import Priority, Task
from taskboard.service import TaskService


def make_service() -> TaskService:
    return TaskService(
        [
            Task(1, "Write release notes", "todo", 3, "alice"),
            Task(2, "Fix login redirect", "done", 5, "bob"),
            Task(3, "Triage crash reports", "doing", 4, None),
            Task(4, "Update onboarding", "todo", 1, "carol"),
            Task(5, "Audit permissions", "doing", 5, "dave"),
            Task(6, "Rotate API keys", "todo", 5, "erin"),
        ]
    )


def test_list_all_returns_every_task_in_input_order() -> None:
    assert [task.id for task in make_service().list_tasks()] == [1, 2, 3, 4, 5, 6]


def test_filter_by_status_preserves_existing_behavior() -> None:
    service = make_service()
    assert [task.id for task in service.list_tasks(status="done")] == [2]
    assert [task.id for task in service.list_tasks(status="doing")] == [3, 5]
    assert service.list_tasks(status="missing") == []


def test_list_returns_a_copy_not_internal_storage() -> None:
    service = make_service()
    result = service.list_tasks()
    result.clear()
    assert len(service.list_tasks()) == 6


def test_priority_enum_values_unchanged() -> None:
    assert (Priority.LOW, Priority.MEDIUM, Priority.HIGH) == (1, 3, 5)
