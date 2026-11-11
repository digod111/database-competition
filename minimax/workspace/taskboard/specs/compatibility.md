# 兼容性约束

本次修改是局部增强，以下既有行为必须保持不变。

## REQ-COMPAT-001：原有查询行为不变

- `TaskService.list_tasks()` 在不传参数时返回全部任务，顺序与构造时一致。
- `TaskService.list_tasks(status="done")` 只返回状态为 `done` 的任务。
- `TaskService.list_tasks(status="doing")` 只返回状态为 `doing` 的任务。
- 传入不存在的状态时返回空列表，不抛异常。

## REQ-COMPAT-002：公开模型不变

- `Task` 仍是冻结 dataclass，字段为 `id`、`title`、`status`、`priority`、`assignee`。
- `Priority` 仍是 `IntEnum`，取值为 `LOW=1`、`MEDIUM=3`、`HIGH=5`。
- 不新增必填字段，不改变已有字段的含义。

## REQ-COMPAT-003：返回副本

`list_tasks` 返回的列表与内部存储解耦。调用方清空或修改返回列表，不得影响后续查询结果。

## 回归测试

- `tests/test_task_service_regression.py`
