# 验证样例

## 1. 任务

在 `taskboard` 示例工程中完成一项局部修改：给 `TaskService.list_tasks` 增加可选的 `min_priority` 筛选。

- 任务文件：`tasks/add_min_priority.json`
- 任务 ID：`TASK-001`
- 要改什么：`src/taskboard/service.py` 中的 `TaskService.list_tasks`。
- 必须保持什么：原有 `list_tasks()`、`list_tasks(status=...)` 行为不变；`Task` 与 `Priority` 公开模型不变；返回值仍是副本。

## 2. 初始数据

- 改前快照：`workspace/taskboard_before/`
  - 6 条固定任务，id 1–6，覆盖 `todo`/`doing`/`done` 与优先级 1/3/4/5。
  - `TaskService.list_tasks` 当前只支持 `status` 参数。
- 目标功能测试：`tests/test_task_filter.py`
  - `test_filter_by_min_priority_only`
  - `test_filter_combines_status_and_min_priority`
  - `test_min_priority_boundary_is_inclusive`
  - `test_min_priority_out_of_range_raises[0/6/-1]`
  - `test_min_priority_none_keeps_all_tasks`
- 回归测试：`tests/test_task_service_regression.py`
  - 全量列表、原有 `status` 筛选、返回副本、优先级枚举值。
- 改前/改后的测试文件完全一致；没有删除断言、跳过测试或修改预期结果。

## 3. 有效规格

- `specs/task_filter.md`
  - `REQ-FILTER-001`：新增 `min_priority`，与 `status` 按 AND 组合，保持输入顺序。
  - `REQ-FILTER-002`：合法范围 1–5，越界抛 `ValueError`，消息包含 `min_priority`，边界包含。
  - `REQ-FILTER-003`：组合筛选示例与预期 id 顺序。
  - `REQ-FILTER-004`：返回新列表，不修改内部状态。
- `specs/compatibility.md`
  - `REQ-COMPAT-001`：原有查询行为不变。
  - `REQ-COMPAT-002`：`Task`/`Priority` 公开模型不变。
  - `REQ-COMPAT-003`：返回副本。

## 4. 上下文中必须包含的来源

| 类型 | 来源 |
| --- | --- |
| 规格 | `REQ-FILTER-001`、`REQ-FILTER-002`、`REQ-FILTER-003`、`REQ-FILTER-004`、`REQ-COMPAT-001`、`REQ-COMPAT-002`、`REQ-COMPAT-003` |
| 代码 | `src/taskboard/service.py` 的 `TaskService.list_tasks` |
| 测试 | `tests/test_task_filter.py` 中 5 个目标测试函数，及其文件头（imports/helper） |
| 状态 | PowerContext 中的 `TASK-001` 最新状态：已完成、待办、已有验证结果 |

`docs/runs/05_context_pack_resumed.json` 的 `specs`、`code`、`tests`、`status`、`powercontext` 字段可直接核对。

## 5. 操作步骤

```bash
# 0. 准备：seekdb 容器、Python 依赖、PowerContext、.env 中的 MINIMAX_API_KEY

# 1. 从干净状态开始
python -m sddctx reset --hard --clean-logs

# 2. 真实调用 MiniMax 的完整验证
python -m sddctx demo --live-minimax
```

如果已有一次真实调用的 `artifacts/minimax_edits.json`，可离线复现：

```bash
python -m sddctx reset --hard --clean-logs
python -m sddctx demo
```

## 6. 预期结果

| 阶段 | 命令/产物 | 预期 |
| --- | --- | --- |
| 改前目标测试 | `docs/runs/01_before_target_tests.log` | `7 failed`，失败原因均为 `TypeError: ... unexpected keyword argument 'min_priority'` |
| 改前回归测试 | `docs/runs/02_before_regression_tests.log` | `4 passed` |
| 首次上下文 | `docs/runs/03_context_pack_empty_state.json` | 包含必需规格/代码/测试；`status.source=empty`（干净 PowerContext） |
| 状态重启读取 | `docs/runs/04_state_after_restart.log` | `found=true`，`stage=context_built` |
| 重启后上下文 | `docs/runs/05_context_pack_resumed.json` | `powercontext.state_found=true`，`status.completed` 含 `ingest_materials` |
| MiniMax 调用 | `docs/runs/minimax_request_*.json`、`minimax_response_*.json` | 请求含任务+上下文；响应为结构化 JSON 编辑；无 API Key 泄漏 |
| 改后目标测试 | `docs/runs/06_after_target_tests.log` | `7 passed` |
| 改后回归测试 | `docs/runs/07_after_regression_tests.log` | `4 passed` |
| 改后全量测试 | `docs/runs/08_after_all_tests.log` | `11 passed` |
| 规格更新检查 | `docs/runs/09_spec_update_check.log` | `found=true`，上下文包含临时新增的 `REQ-FILTER-005` |
| 代码差异 | `artifacts/changes.diff` | 只修改 `src/taskboard/service.py` |
| seekdb 查询 | `docs/runs/db_queries.jsonl` | 包含 `retrieve_specs`、`retrieve_code`、`retrieve_tests` 等 SQL 与 query_id |
| PowerContext | `docs/runs/powercontext_state.jsonl` | 包含 `remember` 和 `search_and_recall` 记录 |

## 7. 重置方法

```bash
# 恢复改前快照 + 清空 seekdb 材料表
python -m sddctx reset

# 同时删除本地 PowerContext 数据和运行状态
python -m sddctx reset --hard

# 再清理旧证据，重新生成一套干净材料
python -m sddctx reset --hard --clean-logs
```

`reset --hard` 只删除 `minimax/.powercontext`、`minimax/.runtime` 和本地回放编辑，不删除 `workspace/taskboard_before` 或 `docs/` 中的源码说明。

## 8. 判定口径

- 改前失败是**功能缺失**导致的 `TypeError`，不是依赖缺失、无法启动等环境错误。
- 改前/改后运行同一组测试文件；没有删除断言、跳过测试或改预期结果。
- 上下文中的来源全部来自 seekdb 查询结果或任务显式锚点，且这些来源真实存在于工程中。
- 规格更新后重新导入，上下文出现 `REQ-FILTER-005`，说明模块没有返回固定答案。
- 模块重启（新 Python 进程）后从 PowerContext 读到状态，并将其带入后续上下文包。
