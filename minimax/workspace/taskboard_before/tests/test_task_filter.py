from __future__ import annotations

import pytest

from taskboard.models import Task
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


def ids(tasks: list[Task]) -> list[int]:
    return [task.id for task in tasks]


def test_filter_by_min_priority_only() -> None:
    assert ids(make_service().list_tasks(min_priority=4)) == [2, 3, 5, 6]


def test_filter_combines_status_and_min_priority() -> None:
    assert ids(make_service().list_tasks(status="todo", min_priority=3)) == [1, 6]


def test_min_priority_boundary_is_inclusive() -> None:
    assert ids(make_service().list_tasks(min_priority=5)) == [2, 5, 6]


@pytest.mark.parametrize("value", [0, 6, -1])
def test_min_priority_out_of_range_raises(value: int) -> None:
    with pytest.raises(ValueError, match="min_priority"):
        make_service().list_tasks(min_priority=value)


def test_min_priority_none_keeps_all_tasks() -> None:
    assert ids(make_service().list_tasks(min_priority=None)) == [1, 2, 3, 4, 5, 6]
