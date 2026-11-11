# 验证样例：两轮复习 + 重启召回 + 检索证据

本样例用于评审按步骤复现主流程，并核对 seekdb 与 PowerContext 是否实际发挥作用。

- 适用版本：`wengu` 0.1.0
- 前置条件：seekdb Docker 已启动；Python 依赖已安装；PowerContext Server 可由程序自动启动。
- 自动化入口：`python -m wengu demo`（推荐）
- 证据目录：`docs/runs/`

## 1. 初始数据

### 1.1 种子知识库

| 内容 | 数量 | 来源 |
| --- | --- | --- |
| 课程 | 3 门（数据结构、操作系统、计算机网络） | `seed/notes/` |
| 笔记片段 | 35 段 | 按 Markdown `##` 小节切块 |
| 题目 | 27 道（全部为单选题） | `seed/questions.json` |
| B+树相关题 | 3 道 | `Q-DS-006`、`Q-DS-007`、`Q-DS-008` |

初始化命令：

```bash
python -m wengu init
```

预期：`materials: 35`、`questions: 27`、`unresolved_sources: []`。

### 1.2 学习者初始状态

`python -m wengu demo` 会创建一个全新的演示用户（`demo-<时间戳>`），因此：

- seekdb 中该用户没有 `question_state`、`attempts`、`sessions` 记录。
- PowerContext 中该用户使用全新的 Scope，没有历史记忆。
- 学习偏好初始为：每日 6 题、解析风格「简洁」。

> 演示命令只清理 `demo-*` 账号和旧的证据日志，不会删除真实用户数据。旧日志会移动到 `docs/runs/archive_<时间戳>/`。

## 2. 输入与操作

### 2.1 第一轮：无历史记忆

```bash
python -m wengu demo
```

demo 内部执行的第一轮等价于：

1. 选择课程「数据结构」开始复习。
2. 系统生成 6 题计划。本次运行的计划为：

   ```
   Q-DS-005 哈希冲突处理
   Q-DS-001 快速排序
   Q-DS-002 快速排序
   Q-DS-003 归并排序
   Q-DS-004 堆排序
   Q-DS-006 B+树插入与分裂
   ```

3. 模拟学习者作答：
   - **跳过** `Q-DS-005`（不答，留作未完成交接）。
   - **故意答错** `Q-DS-006`（选择错误选项）。
   - 其余 4 题答对。
4. 结束本轮。

预期第一轮：完成 5 题、答对 4 题；薄弱点 `B+树插入与分裂`；未完成 `Q-DS-005`。

### 2.2 重启 PowerContext Server

```bash
python -m wengu pc restart
```

demo 会自动执行该操作，并记录重启前/后的健康状态。随后 demo 用**新的 Python 进程**执行：

```bash
python -m wengu recall --user <demo_user> --json docs/runs/05_recall_after_restart.json
```

这一步是“应用进程 + 上下文服务”双重启后的召回验证。

### 2.3 第二轮：记忆影响计划

再次选择「数据结构」开始复习。预期计划为 6 题：

```
Q-DS-006 B+树插入与分裂     ← 薄弱点知识点加权
Q-DS-005 哈希冲突处理        ← 上次未完成交接补做
Q-DS-007 B+树与B树的区别     ← 同章节扩展加权
Q-DS-008 B+树范围查询        ← 同章节扩展加权
Q-DS-001 / Q-DS-002          ← 巩固已学
```

6 题全部答对并结束本轮。

### 2.4 检索样例

```bash
python -m wengu search "B+树 插入 分裂" --course 数据结构
```

或在网页「资料库」输入同样查询。

## 3. 预期结果

### 3.1 第一轮

| 项目 | 预期 | 证据 |
| --- | --- | --- |
| 计划题数 | 6 | `docs/runs/01_session1_plan.json` |
| B+树题数 | 1 | 同上 `bplus_items` |
| 作答/答对 | 5 / 4 | `docs/runs/02_session1_answers.json`、`03_session1_finish.json` |
| 薄弱点 | `B+树插入与分裂`，掌握度 0.325 | `03_session1_finish.json`、`powercontext_memory.jsonl` |
| 未完成交接 | `Q-DS-005` | `03_session1_finish.json` |
| 记忆写入 | session_summary + weak_point + handoff 各 1 条 artifact | `03_session1_finish.json` 的 `memory` 字段 |

### 3.2 重启与召回

| 项目 | 预期 | 证据 |
| --- | --- | --- |
| 重启前健康 | `true` | `docs/runs/04_powercontext_restart.json` |
| 重启后健康 | `true`，新 pid | 同上 |
| 新进程 | 记录真实 `subprocess_pid` | `docs/runs/05_recall_after_restart.log` |
| 召回计数 | `profile=1, weak_point=1, session_summary=1, handoff=1, prepared_citations>=1` | `docs/runs/05_recall_after_restart.json` |
| 交接内容 | `remaining=["Q-DS-005"]`，状态 `open` | 同上 |

### 3.3 第二轮

| 项目 | 预期 | 证据 |
| --- | --- | --- |
| 计划题数 | 6 | `docs/runs/06_session2_plan.json` |
| B+树题数 | 3（第一轮 1 道 → 第二轮 3 道） | 同上 `bplus_items` |
| 记忆影响 | 至少包含 `weak_point`、`weak_chapter`、`handoff`、`profile`、`prepared_context` | 同上 `influences` |
| 知识点加权 | `Q-DS-006` 被 weak_point 影响 | 同上 |
| 同章节加权 | `Q-DS-007`、`Q-DS-008` 被同章节影响 | 同上 |
| 交接补做 | `Q-DS-005` 在计划中 | 同上 |
| 作答/答对 | 6 / 6 | `07_session2_answers.json`、`08_session2_finish.json` |
| 薄弱点恢复 | B+树插入与分裂掌握度 0.325 → 0.491 | `09_recall_after_session2.json` |
| 交接完成 | handoff `status=completed`，`remaining=[]` | 同上 |

### 3.4 检索

| 项目 | 预期 | 证据 |
| --- | --- | --- |
| 首条结果 | `B+树 / B+树的插入与分裂` | `docs/runs/10_search_demo.json` |
| 首条相关度 | 约 2.6944（以实际运行为准） | 同上 |
| 出处 | `notes/ds/03_btree.md` | 同上 |
| SQL 含全文检索 | SQL 包含 `MATCH ... AGAINST` | `docs/runs/11_search_query_record.json` |
| query_id | 与检索响应一致 | `10_search_demo.json` + `11_search_query_record.json` |

### 3.5 自检

| 项目 | 预期 |
| --- | --- |
| demo 自检 | 23/23 通过 |
| 汇总文件 | `docs/runs/demo_summary.json` |
| 真实统计（本 demo 用户） | 11 次答题、10 次正确、正确率 90.91%、2 轮会话 |

## 4. 如何直接核对 seekdb

### 4.1 查看 SQL 查询记录

打开 `docs/runs/db_queries.jsonl`，搜索 `"purpose": "library_search"`，应看到类似：

```json
{
  "purpose": "library_search",
  "sql": "SELECT ... MATCH(search_text) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score ...",
  "params": ["B+树 插入 分裂", "B+树 插入 分裂", "数据结构", "10"],
  "row_count": 5,
  "scores": [2.69445, 1.221584, ...],
  "query_id": "q_..."
}
```

`purpose` 为 `explanation_evidence` 的记录对应答题后解析引用；`due_questions` / `weak_questions` / `new_questions` 对应第二轮计划的候选项。

### 4.2 直接查询 seekdb

```bash
python - <<'PY'
import json, pymysql

summary = json.load(open("docs/runs/demo_summary.json", encoding="utf-8"))
user_id = summary["demo_user"]
connection = pymysql.connect(
    host="127.0.0.1", port=2881, user="root", password="",
    database="qoder_app", charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)
with connection.cursor() as cursor:
    cursor.execute("SELECT COUNT(*) AS n FROM materials")
    print("materials:", cursor.fetchone())
    cursor.execute("SELECT COUNT(*) AS n FROM questions")
    print("questions:", cursor.fetchone())
    cursor.execute(
        "SELECT question_id, mastery, wrong_count, correct_count, due_at "
        "FROM question_state WHERE user_id = %s ORDER BY question_id",
        (user_id,),
    )
    print("states:")
    for row in cursor.fetchall():
        print(" ", row)
    cursor.execute("SELECT COUNT(*) AS n FROM attempts WHERE user_id = %s", (user_id,))
    print("attempts:", cursor.fetchone())
    cursor.execute(
        "SELECT id, status, planned_count, answered_count, correct_count "
        "FROM sessions WHERE user_id = %s ORDER BY started_at",
        (user_id,),
    )
    print("sessions:", cursor.fetchall())
PY
```

预期能看到 B+树插入与分裂的掌握度约 0.49、`wrong_count=1`，以及 2 轮已完成会话。

## 5. 如何直接核对 PowerContext

### 5.1 用新进程召回

```bash
python -m wengu recall --user <demo_user>
```

预期输出包含：

```json
{
  "counts": {
    "profile": 1,
    "weak_point": 1,
    "session_summary": 1,
    "handoff": 1,
    "prepared_citations": 2
  },
  "weak_points": [{"knowledge_point": "B+树插入与分裂", "mastery": 0.491, ...}],
  "handoff": {"status": "completed", ...},
  "prepared": {"status": "ready", "citations": [...]}
}
```

### 5.2 查看记忆日志

`docs/runs/powercontext_memory.jsonl` 中：

- `action=remember`：四类记忆的写入，含 `artifact_id`、`kind`、`text`。
- `action=recall`：召回计数、召回的偏好/薄弱点/会话/交接，以及 `context/prepare` 的 citation。
- `action=reset_scope`/`ensure_scope`：Scope 生命周期。

`docs/runs/powercontext_restart.jsonl` 中：

- `action=stop`：停止 pid、停止后健康状态。
- `action=restart`：重启前后健康状态与 pid。

### 5.3 网页核对

启动 `python -m wengu server`，用 demo 用户进入：

- 「教练记忆」页：四类记忆、artifact ID、`context/prepare` 查询与 citation。
- 「技术证据」页：`FULLTEXT` 查询、记忆读写、重启记录。
- 「今日」页：当前计划与记忆影响。
- 「本轮总结」：写入的记忆 artifact。

## 6. 手工 UI 复现（不使用 demo 脚本）

1. 启动应用，输入昵称「复现同学」。
2. 选择「数据结构」开始，完成 6 题；其中一题 B+树故意答错，另一题不答。
3. 点击「结束本轮」；在总结页记下薄弱点和未完成题。
4. 执行 `python -m wengu pc restart`。
5. 刷新「今日」，开始新一轮；观察「本轮计划如何被记忆影响」出现薄弱点、同章节、交接和上下文包。
6. 打开「教练记忆」与「技术证据」，核对 artifact ID、召回计数、SQL `MATCH` 与 `query_id`。

## 7. 重置与重复运行

- 重新跑 `python -m wengu demo`：会归档旧日志、创建新的 demo 用户，不影响真实用户。
- 清理某个真实用户：

```bash
python -m wengu reset --user <user_id>
```

该命令删除该用户在 seekdb 中的答题/会话/统计，并切换到新的 PowerContext Scope；旧 Scope 记忆仍在磁盘上，但不会再被召回。

## 8. 常见问题

| 现象 | 处理 |
| --- | --- |
| `无法连接 seekdb` | 确认 `docker ps` 中 seekdb 容器运行，端口 2881 可访问 |
| PowerContext 不可用 | `python -m wengu pc start`；查看 `docs/runs/powercontext_server.log` |
| 端口冲突 | 修改 `.env` 中 `WENGU_PORT` / `POWERCONTEXT_URL` |
| demo 失败 | 查看 `docs/runs/demo_error.json` 与对应阶段的 `docs/runs/*.json` |
| 想保留真实使用日志 | demo 会把旧日志移到 `docs/runs/archive_<时间戳>/`，不会删除 |
