# 使用效果记录

> 导出时间：2026-11-10T18:00:20+08:00
> 数据来源：seekdb attempts / sessions / usage_events / feedback；已排除 demo- 与 pytest- 验证账号

## 一、真实使用数据

| 指标 | 数值 |
| --- | --- |
| 真实学习者 | 0 人 |
| 复习会话 | 0 轮 |
| 累计答题 | 0 题 |
| 整体正确率 | 0% |
| 活跃天数 | 0 天 |

**当前还没有真实用户数据。** 上面的 0 不是估算或占位，而是导出脚本排除了 `demo-` / `pytest-` 验证账号后的真实结果。
邀请同学使用后，重新执行 `python tools/export_usage.py`，再发布网站即可更新。

## 二、验证样例（不是真实使用数据）

以下数字来自 `python -m wengu demo` 生成的演示账号，用于证明 seekdb / PowerContext 的功能闭环，**不计入上面的真实使用统计**。

| 项目 | 结果 |
| --- | --- |
| 自检通过 | 23/23 |
| 第一轮 B+树题 | 1 道 |
| 第二轮 B+树题 | 3 道 |
| 重启召回 | {"profile": 1, "weak_point": 1, "session_summary": 1, "handoff": 1, "prepared_citations": 2} |
| 检索样例 | B+树 插入 分裂 → B+树 / B+树的插入与分裂 |
| 证据文件 | `docs/runs/demo_summary.json` |

## 三、如何更新

```bash
# 1. 让同学在 http://127.0.0.1:8000 使用（每人用唯一昵称）
# 2. 导出真实统计（自动排除 demo-/pytest- 账号）
python tools/export_usage.py
# 3. 在 Qoder Sites 重新发布网站，使 usage.js / verification.js 生效
```

所有数字来自 seekdb 的 `attempts`、`sessions`、`usage_events`、`feedback` 表；不手工填写。
