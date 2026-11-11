from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Priority(IntEnum):
    LOW = 1
    MEDIUM = 3
    HIGH = 5


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    status: str
    priority: int
    assignee: str | None = None
