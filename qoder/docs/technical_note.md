# 技术说明：温故 · 复习教练

## 1. 目标与边界

温故要解决的是“复习时找不到原文、不知道自己薄弱在哪、每次复习都从零规划”的问题。它把课程笔记/题库、检索、答题状态和跨会话记忆串成一个可复现的本地闭环。

设计边界：

- **本地优先**：单机运行，seekdb 与 PowerContext 都在本机；不做云端账号体系。
- **可复现优先**：不依赖外部 LLM/embedding API；检索、判分、调度、记忆都是确定性的，评审可以按命令复现。
- **证据优先**：每次数据库查询、每次记忆写入/召回、每次 PowerContext 重启都落 JSONL，网页“技术证据”页可直接查看。

## 2. 总体架构

```
FastAPI Web (Jinja2 + 原生 JS)
   ├── CoachService
   │     ├── 计划生成（到期/薄弱/新题/交接 + 记忆加权）
   │     ├── 判分与 SM-2 轻量调度
   │     ├── 解析组装（引用 seekdb 原文）
   │     └── 会话总结 + 记忆写回
   ├── SeekDBRepository (PyMySQL)
   │     ├── materials / questions
   │     ├── question_state / attempts / sessions / session_summaries
   │     └── users / usage_events / feedback
   └── PowerContextClient (HTTP)
         ├── /v1/scopes（按用户幂等创建）
         ├── /v1/memory/remember（显式写入四类记忆）
         ├── /v1/memory/search（结构化召回）
         └── /v1/context/prepare（组装带 citation 的上下文包）
```

核心代码：

| 文件 | 职责 |
| --- | --- |
| `wengu/db.py` | 建表、种子导入、检索 SQL、状态/会话/统计、JSONL 查询日志 |
| `wengu/mastery.py` | 判分、掌握度、间隔重复（SM-2 变体） |
| `wengu/memory.py` | Scope 管理、四类记忆编码、召回、`context/prepare` 解析、记忆日志 |
| `wengu/coach.py` | 计划评分与选题、答题、解析、会话结束、偏好 |
| `wengu/web.py` | 页面路由与 JSON API |
| `wengu/server.py` | PowerContext Server 启停、按端口找 pid、重启 |
| `wengu/demo.py` | 两轮复习 + 重启召回 + 检索 + 自检的证据脚本 |

## 3. seekdb 数据模型

### 3.1 表

| 表 | 关键字段 | 用途 |
| --- | --- | --- |
| `materials` | course/chapter/title/heading/content/keywords/knowledge_points/source_path/search_text | 笔记片段。`search_text` 是标题+章节+关键词+知识点+正文的拼接，供全文索引使用 |
| `questions` | course/chapter/knowledge_point/type/difficulty/stem/options_json/answer_json/explanation/source_material_id | 题库；记录来源片段 |
| `users` | id/display_name/created_at/last_seen_at | 本机多用户（昵称区分） |
| `question_state` | user_id/question_id/mastery/ease/interval_days/due_at/correct_count/wrong_count/streak | 每题逐用户掌握度与下一次到期时间 |
| `attempts` | user_id/question_id/session_id/answer/correct/quality/elapsed_ms | 答题流水 |
| `sessions` | user_id/status/planned_count/answered_count/correct_count/plan_json/memory_recall_json/influences_json | 复习会话与计划快照 |
| `session_summaries` | session_id/user_id/summary_json | 会话总结页读取 |
| `usage_events` | user_id/event/detail_json | 进入、检索、开始/结束会话、偏好、反馈等真实使用事件 |
| `feedback` | user_id/display_name/rating/comment | 用户反馈；页面匿名展示 |

### 3.2 中文全文索引

```sql
FULLTEXT INDEX ft_material_search (search_text) WITH PARSER ngram
```

seekdb（OceanBase 5.7.25 兼容）提供 ngram 解析器，无需额外中文分词服务。实测“B+树 插入 分裂”能把插入分裂小节排到第一，并给出可比较的相关度。

### 3.3 检索路径

`db.search_materials(query, course, chapter, knowledge_point, limit)`：

1. 若 query 为空：按结构化条件浏览。
2. 否则执行：

```sql
SELECT ..., MATCH(search_text) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score
FROM materials
WHERE MATCH(search_text) AGAINST (%s IN NATURAL LANGUAGE MODE)
  AND course = %s
  AND chapter = %s
  AND knowledge_points LIKE %s
ORDER BY score DESC, course, title
LIMIT %s;
```

3. 若无命中，回退到 `LIKE` 加权匹配（标题 6 分、关键词 3 分、正文 1 分），并在日志中标记 `_like_fallback`。
4. 每次执行写入 `docs/runs/db_queries.jsonl`：`query_id`、purpose、SQL、参数、行数、前 10 个 ID、前 5 个相关度、耗时。
5. 答题后的解析引用调用 `search_for_question`，以“知识点 + 题干前 50 字”检索同课程笔记，返回最多 2 条原文片段。

### 3.4 计划候选与结构化查询

`coach.build_plan` 从 seekdb 取四类候选：

- `list_due_questions`：`due_at <= now`，按掌握度/到期排序。
- `list_weak_questions`：`mastery < 0.6`，按掌握度升序、错误次数降序。
- `list_new_questions`：`question_state` 中不存在的题。
- 未完成交接：来自 PowerContext handoff 记忆中的题目 ID。

指定课程时只保留该课程的候选；未指定时用轻量多样性惩罚（同课程 -0.7、同章节 -0.35）避免整轮只练一门。

## 4. 判分与调度

### 4.1 判分

- 单选题：选项下标等于 `answer` 即正确，quality=4；否则 quality=0。
- 简答题：学习者自评 correct→4、partial→3、wrong→0。
- 只有 quality≥3 计入“通过”，但简答“模糊”不计入正确率，只影响调度。

### 4.2 SM-2 轻量变体

```
quality < 3:
    streak = 0
    interval = 1 天
    ease = max(1.3, ease - 0.2)

quality >= 3:
    streak += 1
    interval = 1 天（首次）/ 3 天（第二次）/ round(interval × ease)（之后）
    ease = max(1.3, ease + 0.1 - (5-quality)×0.08)

mastery = clamp(0.65×old_mastery + 0.35×target, 0, 1)
target = {3: 0.55, 4: 0.80, 5: 1.0}
due_at = now + interval
```

初始 mastery=0.5，首次答错约 0.325，首次答对约 0.605。该模型只用于可解释的复习节奏，不宣称教育测量有效性。

## 5. PowerContext 记忆模型

### 5.1 Scope

- 每个用户一个幂等 Scope：`wengu-u<sha10>-g<generation>`。
- `runtime/scopes.json` 保存 user→scope/generation 映射。
- “清空我的学习数据”会递增 generation，创建新 Scope；旧 Scope 记忆仍在磁盘，但不再召回。

### 5.2 四类 Atomic Memory

| kind | 文本编码 | 内容 | 写入时机 |
| --- | --- | --- | --- |
| `profile` | `PROFILE {json}` | 显示名、每日目标、解析风格 | 首次进入、偏好保存 |
| `weak_point` | `WEAK_POINT {json}` | 知识点、课程、累计错误/总次数、掌握度、题目 ID | 会话结束（有错或曾薄弱） |
| `session_summary` | `SESSION {json}` | 会话 ID、答题/正确、正确率、薄弱点、摘要 | 每次结束 |
| `handoff` | `HANDOFF {json}` | 会话 ID、剩余题、下次重点、状态 open/completed | 每次结束（有剩余或上次有交接） |

### 5.3 召回

`memory.recall(user_id)`：

1. 对每个前缀（`PROFILE` / `WEAK_POINT` / `SESSION` / `HANDOFF`）调用 `/v1/memory/search`，只保留 `state=active` 且前缀匹配的记录。
2. profile 取最新 `updated_at`；weak_point 按知识点取最新；session 取最近 5 条；handoff 取最新。
3. 调用 `/v1/context/prepare`，查询由“薄弱知识点 + 薄弱点 + 复习计划”构成；返回带 citation 的上下文包，并解析出 artifact_id、kind、content。
4. 把召回计数、解析结果、prepared citation 写入 `powercontext_memory.jsonl`。

### 5.4 记忆如何影响计划

| 记忆 | 计划中的效果 |
| --- | --- |
| profile | 每日题量、解析详略；作为 `profile` influence 展示 |
| weak_point（精确知识点） | 对应题 +9+min(4, 错误次数) 分；influence 显示受影响的题 |
| weak_point（同章节扩展） | 同章节其他题 +4 分；influence 单列“同章节扩展”，避免把同章节题误说成同一知识点 |
| handoff | 未完成题 +7 分；influence 显示“补做上次未完成” |
| prepared_context | 作为计划依据展示 citation 数量；不直接改分数，避免把“上下文组装”夸大成“自动决策” |

计划快照写入 `sessions.plan_json` 和 `docs/runs/coach_plans.jsonl`，包含每个候选项的 score/sources/reasons/memory_ids 和 influences。

## 6. 重启后的召回闭环

`python -m wengu demo` 的实际顺序：

1. 第一轮结束 → 写入 session_summary、weak_point、handoff。
2. `server.restart()`：停止 PowerContext 进程（按 pid 或端口找），重新启动，健康检查通过。
3. 用 `subprocess.Popen` 启动**新的 Python 进程**执行 `python -m wengu recall`；日志记录 `subprocess_pid`、`return_code`、完整召回 JSON。
4. 第二轮开始 → 召回同一 Scope 的记忆 → 计划从 1 道 B+树题变为 3 道，并补做 1 道未完成题。
5. 第二轮结束 → 写回新的 session_summary、weak_point（掌握度 0.325→0.491）和 completed handoff。
6. 再次用新进程召回，验证掌握度变化与交接完成。

这满足“保存 → 重启 → 召回 → 影响后续结果”的闭环要求，而不是只测连通。

## 7. 证据与日志

| 文件 | 内容 |
| --- | --- |
| `docs/runs/db_queries.jsonl` | 每条 seekdb SQL、参数、行数、ID、相关度、耗时、query_id |
| `docs/runs/powercontext_memory.jsonl` | Scope 创建、remember、recall、prepared citation、artifact_id |
| `docs/runs/powercontext_restart.jsonl` | stop/start/restart 前后健康、pid |
| `docs/runs/coach_plans.jsonl` | 每次计划生成：题量、focus、items、score/sources、influences |
| `docs/runs/coach_answers.jsonl` | 每题作答、掌握度、下一次到期、证据 query_id 与分数 |
| `docs/runs/coach_sessions.jsonl` | 会话开始/结束、薄弱点、记忆 artifact |
| `docs/runs/demo_summary.json` | 23 项自检、两轮计划对比、召回、检索、统计与文件清单 |
| `docs/usage_report.md` | 真实使用统计（排除 demo-/pytest-），由 `tools/export_usage.py` 生成 |

## 8. 关键设计取舍

### 8.1 为什么不用向量检索

当前环境没有可靠、可离线复现的 embedding 来源。为了不让“向量”成为不可运行的装饰，主路径使用真实的 seekdb 中文全文索引 + 结构化过滤 + 业务重排：

- 检索是真实的：SQL 里有 `MATCH ... AGAINST`，有相关度排序，有 query_id。
- 结果是可解释的：每条解析都带原文片段和来源路径。
- 升级路径清楚：未来可在 `materials` 增加 `VECTOR` 列和 HNSW 索引，把 `search_materials` 改为全文+向量混合召回，上层教练逻辑不变。

### 8.2 为什么没有 LLM

题目解析使用“预写解析 + seekdb 检索原文”，而不是自由生成。这样：

- 无 API Key 也能完整复现；
- 解析内容可追溯到笔记；
- 不会出现模型编造知识点的问题。

如果未来接入 LLM，可以只把“解析改写/追问”作为可选层，核心检索与记忆仍由 seekdb/PowerContext 提供。

### 8.3 为什么做多用户

真实使用效果需要同学参与。应用用“唯一昵称”区分本机学习者，每个用户独立：

- seekdb：`users`、`question_state`、`attempts`、`sessions`、`usage_events`、`feedback` 都按 user_id 隔离。
- PowerContext：每个用户独立 Scope。
- 统计：`tools/export_usage.py` 默认排除 `demo-` / `pytest-`，只导出真实用户数据。

### 8.4 安全与隐私

- 无公网部署、无真实凭据；`.env` 使用占位值并加入 `.gitignore`。
- 用户输入通过 Jinja 自动转义；`snippet_html` 由 `html.escape` 后再加 `<mark>`，避免 XSS。
- 所有 SQL 使用参数化查询；数据库名做白名单校验。
- 反馈页面匿名展示，不显示昵称；导出脚本不包含显示名。

## 9. 局限与后续

- 中文 ngram 全文检索对关键词有效，对语义改写有限；可升级为向量+全文混合。
- 掌握度是启发式，不是经过验证的学习模型；可替换为更完整的间隔重复或 IRT。
- 本地单机、无鉴权，不应直接暴露公网；多人远程使用需要额外认证与 HTTPS。
- PowerContext Windows 支持为 experimental；Linux/macOS 更推荐。
- 真实使用数据需要时间积累；`tools/export_usage.py` 已提供可重复的导出流程，网站发布前重新导出即可。

## 10. Qoder 开发对应关系

| 阶段 | Qoder 完成的工作 | 证据 |
| --- | --- | --- |
| 需求拆解 | 阅读题面、拆硬性验收、对比三个方向 | `docs/qoder_dev_record.md` 第 1 节 |
| 技术探针 | seekdb ngram 中文全文检索、PowerContext API 返回结构 | 开发记录第 1–2 节 |
| 编码 | 数据层、教练逻辑、记忆闭环、Web UI、CLI、demo | `wengu/`、`templates/`、`static/` |
| 调试 | 焦点课程选题、记忆分项、截图吸顶、查询过滤等问题修复 | 开发记录第 4–6 节 |
| 测试 | 22 个单元测试 + 3 个集成测试 + demo 23 项自检 | `tests/`、`docs/runs/demo_summary.json` |
| 发布 | Qoder Sites 网站与公开访问验证 | 网站工程 `website/`、开发记录第 8 节 |
