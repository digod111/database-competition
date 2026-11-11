# taskboard 示例业务工程

一个小型任务列表服务，用于验证 SDD 上下文模块。工程只依赖 Python 标准库和 pytest。

## 运行测试

```bash
python -m pytest -q
```

## 目录

- `specs/`：任务筛选规格与兼容性约束。
- `src/taskboard/`：业务源码。
- `tests/test_task_filter.py`：目标功能测试。
- `tests/test_task_service_regression.py`：回归测试。

## 初始状态

当前 `TaskService.list_tasks` 只支持 `status` 筛选，目标功能测试因此失败；回归测试应当通过。
