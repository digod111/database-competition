from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from wengu import config, db
from wengu.text import now_iso, read_json, write_json

ROOT = Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "website" / "data"
EXCLUDED = ("demo-", "pytest-")


def _is_real_user(user_id: str) -> bool:
    return not any(user_id.startswith(prefix) for prefix in EXCLUDED)


def export() -> dict:
    real = db.stats_overview(None, exclude_prefixes=EXCLUDED)
    events = db.usage_event_counts(exclude_prefixes=EXCLUDED)
    feedback = [
        {
            "rating": row["rating"],
            "comment": row["comment"],
            "created_at": row["created_at"],
        }
        for row in db.list_feedback(200)
        if _is_real_user(row["user_id"])
    ]
    users = [user for user in db.list_users(500) if _is_real_user(user["id"])]
    payload = {
        "exported_at": now_iso(),
        "source": "seekdb attempts / sessions / usage_events / feedback；已排除 demo- 与 pytest- 验证账号",
        "has_real_data": real["attempts"] > 0,
        "real_usage": real,
        "usage_events": events,
        "feedback": feedback,
        "real_users": len(users),
    }

    demo = read_json(ROOT / "docs" / "runs" / "demo_summary.json", {}) or {}
    checks = demo.get("checks", [])
    verification = {
        "exported_at": now_iso(),
        "source": "docs/runs/demo_summary.json（演示账号，非真实使用数据）",
        "demo_user": demo.get("demo_user"),
        "scope_id": demo.get("scope_id"),
        "finished_at": demo.get("finished_at"),
        "session1_bplus": (demo.get("session1") or {}).get("bplus_count"),
        "session2_bplus": (demo.get("session2") or {}).get("bplus_count"),
        "session1_items": (demo.get("session1") or {}).get("plan_items"),
        "session2_items": (demo.get("session2") or {}).get("plan_items"),
        "attempts": (demo.get("stats") or {}).get("attempts"),
        "sessions": (demo.get("stats") or {}).get("sessions"),
        "accuracy": (demo.get("stats") or {}).get("accuracy"),
        "checks_passed": sum(1 for check in checks if check.get("passed")),
        "checks_total": len(checks),
        "restart_health": {
            "before": ((demo.get("restart") or {}).get("before") or {}).get("healthy"),
            "after": ((demo.get("restart") or {}).get("after") or {}).get("healthy"),
        },
        "recall_counts": (demo.get("recall_after_restart") or {}).get("counts"),
        "search": demo.get("search"),
    }

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    write_json(SITE_DATA / "usage.json", payload)
    write_json(SITE_DATA / "verification.json", verification)
    (SITE_DATA / "usage.js").write_text(
        "window.WENGU_USAGE = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )
    (SITE_DATA / "verification.js").write_text(
        "window.WENGU_VERIFICATION = " + json.dumps(verification, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )

    report = _report(payload, verification)
    (ROOT / "docs" / "usage_report.md").write_text(report, encoding="utf-8")
    return payload


def _report(payload: dict, verification: dict) -> str:
    real = payload["real_usage"]
    events = payload["usage_events"]
    feedback = payload["feedback"]
    lines = [
        "# 使用效果记录",
        "",
        f"> 导出时间：{payload['exported_at']}",
        f"> 数据来源：{payload['source']}",
        "",
        "## 一、真实使用数据",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 真实学习者 | {payload['real_users']} 人 |",
        f"| 复习会话 | {real['sessions']} 轮 |",
        f"| 累计答题 | {real['attempts']} 题 |",
        f"| 整体正确率 | {round(real['accuracy'] * 100, 1)}% |",
        f"| 活跃天数 | {real['active_days']} 天 |",
        "",
    ]
    if not payload["has_real_data"]:
        lines += [
            "**当前还没有真实用户数据。** 上面的 0 不是估算或占位，而是导出脚本排除了 `demo-` / `pytest-` 验证账号后的真实结果。",
            "邀请同学使用后，重新执行 `python tools/export_usage.py`，再发布网站即可更新。",
            "",
        ]
    if events:
        lines += ["### 使用事件", "", "| 事件 | 次数 |", "| --- | --- |"]
        lines += [f"| {event['event']} | {event['count']} |" for event in events]
        lines.append("")
    if real["recent_days"]:
        lines += ["### 近 7 天答题", "", "| 日期 | 答题 | 正确 | 正确率 |", "| --- | --- | --- | --- |"]
        for day in real["recent_days"]:
            lines.append(f"| {day['day']} | {day['attempts']} | {day['correct']} | {round(day['accuracy'] * 100, 1)}% |")
        lines.append("")
    if real["weak_points"]:
        lines += ["### 高频薄弱点", "", "| 知识点 | 错误次数 | 涉及学习者 |", "| --- | --- | --- |"]
        lines += [f"| {row['knowledge_point']} | {row['wrong_count']} | {row['users']} |" for row in real["weak_points"]]
        lines.append("")
    if feedback:
        lines += ["### 用户反馈（匿名）", ""]
        for index, item in enumerate(feedback, start=1):
            lines.append(f"- 同学 #{index}，评分 {item['rating']}/5：{item['comment'] or '（无文字）'}")
        lines.append("")
    lines += [
        "## 二、验证样例（不是真实使用数据）",
        "",
        "以下数字来自 `python -m wengu demo` 生成的演示账号，用于证明 seekdb / PowerContext 的功能闭环，**不计入上面的真实使用统计**。",
        "",
        "| 项目 | 结果 |",
        "| --- | --- |",
        f"| 自检通过 | {verification.get('checks_passed')}/{verification.get('checks_total')} |",
        f"| 第一轮 B+树题 | {verification.get('session1_bplus')} 道 |",
        f"| 第二轮 B+树题 | {verification.get('session2_bplus')} 道 |",
        f"| 重启召回 | {json.dumps(verification.get('recall_counts'), ensure_ascii=False)} |",
        f"| 检索样例 | {(verification.get('search') or {}).get('query')} → {(verification.get('search') or {}).get('top_results', [{}])[0].get('title', '')} |",
        f"| 证据文件 | `docs/runs/demo_summary.json` |",
        "",
        "## 三、如何更新",
        "",
        "```bash",
        "# 1. 让同学在 http://127.0.0.1:8000 使用（每人用唯一昵称）",
        "# 2. 导出真实统计（自动排除 demo-/pytest- 账号）",
        "python tools/export_usage.py",
        "# 3. 在 Qoder Sites 重新发布网站，使 usage.js / verification.js 生效",
        "```",
        "",
        "所有数字来自 seekdb 的 `attempts`、`sessions`、`usage_events`、`feedback` 表；不手工填写。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    result = export()
    print(json.dumps(result, ensure_ascii=False, indent=2))
