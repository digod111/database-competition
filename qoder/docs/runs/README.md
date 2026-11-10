# 运行证据说明

本目录由 `python -m wengu demo` 生成，用于核对 seekdb 与 PowerContext 的实际作用。

| 文件 | 内容 |
| --- | --- |
| `01_session1_plan.json` | 第一轮计划、候选、记忆召回计数、prepared citation |
| `02_session1_answers.json` | 第一轮逐题作答、判分、解析证据 query_id |
| `03_session1_finish.json` | 第一轮总结、薄弱点、未完成交接、记忆 artifact |
| `04_powercontext_restart.json` | PowerContext 重启前后健康状态与 pid |
| `05_recall_after_restart.json` / `.log` | 新 Python 进程（含 subprocess_pid）召回结果 |
| `06_session2_plan.json` | 第二轮计划与记忆影响拆分（weak_point / weak_chapter / handoff / profile / prepared） |
| `07_session2_answers.json` | 第二轮逐题作答 |
| `08_session2_finish.json` | 第二轮总结、掌握度变化、记忆写回 |
| `09_recall_after_session2.json` / `.log` | 第二轮后的新进程召回 |
| `10_search_demo.json` | `B+树 插入 分裂` 检索结果 |
| `11_search_query_record.json` | 检索对应的 seekdb SQL、参数、相关度、query_id |
| `12_usage_stats.json` | demo 用户的统计快照 |
| `demo_summary.json` | 全部自检、两轮对比、召回、检索、统计、文件清单 |
| `db_queries.jsonl` | 全部 seekdb 查询日志 |
| `powercontext_memory.jsonl` | PowerContext 记忆写入/召回日志 |
| `powercontext_restart.jsonl` | PowerContext 启停/重启日志 |
| `coach_plans.jsonl` / `coach_answers.jsonl` / `coach_sessions.jsonl` | 计划、答题、会话流水 |
| `powercontext_server.log` | PowerContext Server 启动日志 |
| `archive_<时间戳>/` | 运行 demo 前归档的旧日志（不提交仓库） |

> 这些是**验证样例**数据，演示账号为 `demo-*`，不是真实使用数据。真实使用统计由 `tools/export_usage.py` 导出，并排除 `demo-` / `pytest-`。
