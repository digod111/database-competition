# 任务列表筛选规格

本规格定义 `taskboard` 任务列表的一个局部增强：在原有 `status` 筛选之上增加最低优先级筛选。实现必须保持向后兼容。

## REQ-FILTER-001：按最低优先级筛选

`TaskService.list_tasks` 必须接受可选参数 `min_priority: int | None = None`。

- 当 `min_priority` 不为 `None` 时，只返回 `priority >= min_priority` 的任务。
- 该条件必须与已有的 `status` 条件同时生效，即两个条件按 AND 组合。
- 结果必须保持构造 `TaskService` 时的输入顺序。
- `min_priority=None` 表示不按优先级筛选，行为与当前版本一致。

## REQ-FILTER-002：优先级范围与边界输入

`min_priority` 的合法取值为 1 到 5（含 1 和 5）。

- 取值小于 1 或大于 5 时，`list_tasks` 必须抛出 `ValueError`。
- 异常消息必须包含参数名 `min_priority`，便于调用方定位。
- 即使当前没有任何任务满足条件，也必须先完成参数校验。
- 边界值是包含关系：`priority == min_priority` 的任务必须保留。

## REQ-FILTER-003：组合筛选示例

测试数据固定为以下任务，按 id 升序构造：

| id | title | status | priority |
| --- | --- | --- | --- |
| 1 | Write release notes | todo | 3 |
| 2 | Fix login redirect | done | 5 |
| 3 | Triage crash reports | doing | 4 |
| 4 | Update onboarding | todo | 1 |
| 5 | Audit permissions | doing | 5 |
| 6 | Rotate API keys | todo | 5 |

预期结果：

| 调用 | 返回 id 顺序 |
| --- | --- |
| `list_tasks(min_priority=4)` | `[2, 3, 5, 6]` |
| `list_tasks(status="todo", min_priority=3)` | `[1, 6]` |
| `list_tasks(min_priority=5)` | `[2, 5, 6]` |
| `list_tasks(min_priority=None)` | `[1, 2, 3, 4, 5, 6]` |

## REQ-FILTER-004：不修改内部状态

`list_tasks` 必须返回新的列表。调用方修改返回的列表，不得影响 `TaskService` 内部保存的任务集合，也不得修改任何 `Task` 对象。

## 适用代码

- `src/taskboard/service.py` 中的 `TaskService.list_tasks` 是唯一需要修改的符号。
- `src/taskboard/models.py` 中的 `Task` 与 `Priority` 不应修改。

## 验证测试

- 目标功能测试：`tests/test_task_filter.py`，覆盖 `min_priority` 单独筛选、与 `status` 组合、边界包含、越界异常和 `None` 行为。
- 回归测试：`tests/test_task_service_regression.py`，覆盖原有 `status` 筛选、全量列表、返回副本和优先级枚举值。
