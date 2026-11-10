# 温故 · 复习教练 — Qoder 开发记录

> 本记录按时间顺序整理本作品在 Qoder 中完成的需求拆解、技术验证、编码、调试、测试与发布过程。
> 截图点为建议截图位置，便于提交时附上 Qoder 对话或运行画面。
>
> - 开发者 Qoder 账号 UID：**1661591978811838**
> - 开发入口：Qoder Desktop（CN）
> - 开发环境：Windows 11 + Git Bash + Python 3.13.9；seekdb Docker 服务端；PowerContext 1.5.0
> - 项目目录：`qoder作答/wengu/`，收尾复制到 `powercontext/qoder/`

## 0. 项目信息

- 作品名称：温故 · 复习教练（英文标识 `wengu`）
- 一句话：把课程笔记和题库变成会记住你薄弱点的复习教练。
- 赛道：2026 OceanBase 数据库大赛 · Qoder 生态赛道
- 技术分工：
  - seekdb：笔记/题库存储、中文全文检索（ngram）、课程/章节/知识点结构化过滤、复习状态与使用日志。
  - PowerContext：跨会话学习者记忆（偏好、薄弱点、会话摘要、未完成交接），重启后召回并改变下一轮复习计划。
  - Qoder：需求拆解、技术探针、代码实现、调试、测试、文档、Qoder Sites 发布。

## 1. 需求阅读与方向确认（2026-11-10 17:10–17:23）

Qoder 完成的工作：

1. 阅读官方题面 `赛题/Qoder.markdown`，逐条拆出硬性验收：
   - 项目主要开发工作在 Qoder 完成，保留可核对记录；
   - seekdb / PowerContext 至少一种进入核心功能并留下可核对记录；
   - 用 Qoder Sites 发布网站，未登录也能打开；
   - 提交网站、仓库、运行说明、验证样例、开发记录（含 Qoder 账号 UID）。
2. 阅读既有工程 `powercontext/minimax`，确认可复用的工程经验：
   - seekdb 走 MySQL 协议、服务端模式；
   - PowerContext Server 的启动参数与 Windows 后台进程写法；
   - 查询/记忆日志按 JSONL 组织；
   - `context/prepare` 与 `memory/search` 的实际返回结构。
3. 环境实测：
   - seekdb 容器 `seekdb-minimax` 运行中，`127.0.0.1:2881`，版本 `5.7.25-OceanBase seekdb-v1.4.0.0`；
   - 新建独立数据库 `qoder_app`，不触碰 `test` 库；
   - venv 具备 fastapi/uvicorn/jinja2/pymysql/requests/pytest 等依赖。
4. 技术探针：
   - 在临时库 `qoder_probe` 验证 `FULLTEXT ... WITH PARSER ngram` 对中文有效；
   - `MATCH ... AGAINST` 能返回相关度分数，并能与 `course = ?` 结构化条件组合；
   - 探针库已删除。
5. 方向对比：学习复习教练 / 科研文献助手 / 周报生成器；从技术闭环、真实使用可能性、完成度考虑，推荐并确认「温故 · 复习教练」。

**[截图点 S1]** 与 Qoder 讨论三个候选方向、确认选择「温故 · 复习教练」的对话界面。

## 2. 方案定稿（2026-11-10 17:23）

Qoder 输出并确认 MVP：

- 主流程：导入笔记/题库 → 混合检索 → 今日复习 → 判分与原文引用 → 写入记忆 → 重启召回 → 下一轮计划被记忆影响。
- 支持本机多用户（唯一昵称），便于后续收集真实使用数据。
- 复习算法采用轻量 SM-2（正确/模糊/错误三档），先保证可解释、可测试。
- 不做向量检索主路径：当前没有可靠、可离线复现的 embedding 来源；seekdb 使用真实中文全文索引 + 结构化过滤 + 业务重排，并在文档中说明升级路径。
- 应用形态：本地 Web 应用（FastAPI + Jinja2 + 原生 JS）+ CLI + 一键 demo 证据脚本。

**[截图点 S2]** 实施计划与 MVP 范围确认的 Qoder 输出。

## 3. 工程骨架与种子知识库（2026-11-10 17:23–17:28）

Qoder 完成：

- 建立 `wengu/` 工程结构：包代码、模板、静态资源、种子数据、测试、文档证据目录。
- 编写 `config.py`、`text.py`：环境变量、路径、时间/ID、JSONL、Markdown frontmatter、按 `##` 分块、查询词切分、安全高亮。
- 编写 3 门计算机专业课种子笔记（11 个 Markdown 文件，35 个片段）和 27 道单选题（含解析、知识点、来源路径）。
- 种子内容全部自编，避免第三方版权材料。

关键产物：

- `seed/notes/ds/*.md`、`seed/notes/os/*.md`、`seed/notes/net/*.md`
- `seed/questions.json`

## 4. seekdb 数据层（2026-11-10 17:28–17:30）

Qoder 完成：

- 设计 9 张表：`materials`、`questions`、`users`、`question_state`、`attempts`、`sessions`、`session_summaries`、`usage_events`、`feedback`。
- `materials.search_text` 聚合标题/章节/关键词/知识点/正文，建立 `FULLTEXT ... WITH PARSER ngram` 索引。
- 实现 Markdown 分块导入、题库校验、来源片段解析、`REPLACE INTO` 幂等导入。
- 实现全文检索 + 结构化过滤 + `LIKE` 回退；每次查询写入 `docs/runs/db_queries.jsonl`（SQL、参数、行数、ID、相关度、query_id、耗时）。
- 实测：35 个片段、27 道题；查询 `B+树 插入 分裂` 首条为「B+树的插入与分裂」，相关度 2.694；答题解析引用相关度 10.349。

**[截图点 S3]** seekdb 建库、导入、`MATCH ... AGAINST` 查询结果与 `query_id` 的终端输出。

## 5. PowerContext 记忆闭环（2026-11-10 17:28–17:32）

Qoder 完成：

- 探针 `/v1/scopes`、`/v1/memory/remember`、`/v1/memory/search`、`/v1/context/prepare` 的真实返回：
  - Scope 幂等键重复创建返回同一个 `scope_id`；
  - remember 返回 `artifact_id`；
  - search 返回 `hits`、`score`、`matched_by`；
  - `context/prepare` 在查询与记忆相关时返回带 citation 的上下文包，不相关时为 `empty`。
- 实现四类记忆：`profile`、`weak_point`、`session_summary`、`handoff`，文本前缀加 JSON，便于结构化解析。
- 实现 Scope 代际（`wengu-u<hash>-g<generation>`）与 `runtime/scopes.json`，支持“重置记忆”。
- 实现召回：四类前缀搜索 + 最新值聚合 + `context/prepare` + citation 解析 + 召回日志。
- 实现 PowerContext Server 的 start/stop/status/restart，Windows 下按 pid 或端口查找进程。
- 实测：写入 4 类记忆后召回计数 `profile=1, weak_point=1, session_summary=1, handoff=1`，`context/prepare` 返回 2 条 citation；重启 Server 后仍能召回。

**[截图点 S4]** 四类记忆写入、召回计数、`context/prepare` citation，以及 `pc restart` 后仍能召回的终端输出。

## 6. 教练逻辑与 Web 界面（2026-11-10 17:32–17:40）

Qoder 完成：

- 调度：到期队列、薄弱队列、新题、交接候选；指定课程时只取该课程，未指定时做同课程/同章节多样性惩罚。
- 记忆加权：精确薄弱点 +9+错误次数；同章节扩展 +4；交接补做 +7；profile 控制题量与解析详略。
- 判分：单选自动判分；简答自评；SM-2 轻量调度与掌握度更新。
- 解析：seekdb 检索原文，按「简洁」返回首句 + 1 条出处，按「详细」返回完整解析 + 2 条出处。
- 会话总结：写入 session_summary、weak_point（含恢复后的掌握度）、handoff（open/completed）。
- Web 页面：欢迎/今日/资料库/答题/总结/教练记忆/使用统计/技术证据；本机多用户；偏好设置；反馈表单。
- 交互：答题选项、键盘 1–4/Enter、判分反馈、证据卡片、下一题、结束本轮、偏好保存、反馈提交、清空数据。

调试与修复（Qoder 根据运行结果定位并修改）：

1. 指定课程时，跨课程多样性惩罚把重点课程题目挤掉 → 改为指定课程严格筛选、无惩罚；未指定时才做多样性。
2. handoff 只对到期/薄弱题加分，未完成的新题拿不到交接加权 → 扩展为对所有候选加一次 handoff 分，并去重。
3. 薄弱点记忆缺少课程字段 → `session_attempts` 查询补充 `course/chapter`，记忆写入完整。
4. `set_preferences` 首次使用会写两次 profile → 改为一次写入最终值。
5. 薄弱点加权把“同知识点”和“同章节”混在一条影响里 → 拆成 `weak_point` 与 `weak_chapter` 两条 influence。
6. 仪表盘“最近检索”被页面自身读表查询淹没 → 增大读取窗口并按目的过滤。
7. 记忆页“最近记忆写入”混入其他测试用户和频繁 recall → 按当前用户和写入类 action 过滤。
8. 全页截图时吸顶导航重复 → 截图脚本临时关闭 sticky；并在反馈面板单独截图。
9. 统计字段 Decimal/字符串混用 → 统一转 int/float。

**[截图点 S5]** 答题页「本轮计划如何被记忆影响」+ 答错后的 seekdb 原文引用。

## 7. 一键 demo 与证据生成（2026-11-10 17:42–17:44）

Qoder 完成 `wengu/demo.py`：

- 归档旧 `docs/runs` 日志（移动到 `archive_<时间戳>`，不删除真实使用日志）。
- 重启 PowerContext，初始化 schema，导入种子，清理旧 demo 用户。
- 第一轮：指定「数据结构」，6 题；跳过 1 题、故意答错 B+树题、其余答对；结束写记忆。
- 重启 PowerContext Server；用 `subprocess.Popen` 启动**新 Python 进程**执行 `recall`，记录真实 `subprocess_pid`、返回码和召回 JSON。
- 第二轮：同课程；验证 B+树题 1→3、补做交接题、答题 6/6、掌握度 0.325→0.491、handoff completed。
- 再次用新进程召回；检索 `B+树 插入 分裂`；导出统计；跑 23 项自检。
- 生成 `01_session1_plan.json` 至 `12_usage_stats.json`、`demo_summary.json`、`db_queries.jsonl`、`powercontext_memory.jsonl`、`powercontext_restart.jsonl` 等。

实测输出：

```
第一轮：['Q-DS-005','Q-DS-001','Q-DS-002','Q-DS-003','Q-DS-004','Q-DS-006']，B+树 1 道
第二轮：['Q-DS-006','Q-DS-005','Q-DS-007','Q-DS-008','Q-DS-001','Q-DS-002']，B+树 3 道
重启：before=True → after=True
检索：B+树 / B+树的插入与分裂（score=2.6944）
校验：23/23 通过
```

**[截图点 S6]** `python -m wengu demo` 的完成输出与 `23/23 通过`。

## 8. 自动化测试与截图（2026-11-10 17:44–17:53）

Qoder 完成：

- 22 个单元测试：
  - `test_text.py`：frontmatter、分块、词项切分、HTML 转义与高亮。
  - `test_mastery.py`：初始状态、首次正确/错误、第二次正确、恢复、判分。
  - `test_seed.py`：35 个片段、27 道题、来源解析、B+树题覆盖。
  - `test_memory_codec.py`：前缀解码、prepared citation 解析、active hit 过滤。
  - `test_coach.py`：profile 默认值/边界、简洁/详细解析。
- 3 个集成测试（`WENGU_INTEGRATION=1`）：
  - seekdb 全文检索 + 答题状态；
  - PowerContext 四类记忆 roundtrip；
  - FastAPI `/health` 与 `/welcome`。
- 实测：`22 passed, 3 skipped`；集成测试 `3 passed`。
- 使用 Playwright + 本机 Chrome 截取 11 张页面截图，存入 `docs/screenshots/`；覆盖欢迎、今日、资料库、答题、解析、总结、记忆、证据、统计。

**[截图点 S7]** `pytest` 输出 `22 passed` 与集成测试 `3 passed`。

## 9. 文档与真实使用数据（2026-11-10 17:53–18:05）

Qoder 完成：

- `README.md`：项目简介、痛点、功能、架构、版本、目录、快速开始、验证、技术作用、测试、限制、提交说明。
- `docs/verification_sample.md`：初始数据、输入、步骤、预期结果、seekdb/PowerContext 核对方法。
- `docs/technical_note.md`：数据模型、检索 SQL、SM-2、记忆模型、计划加权、重启闭环、证据、取舍与局限。
- `tools/export_usage.py`：从 seekdb 导出真实使用统计，默认排除 `demo-` / `pytest-` 验证账号；同时生成网站 `usage.js` / `verification.js`。
- `docs/usage_report.md`：真实使用数据 + 验证样例数据分栏；当前真实数据为 0（只有 demo 账号），待同学使用后重新导出。
- 清理测试用户，只保留演示账号；修正 `.gitignore`，让 demo 证据可随仓库提交、运行时数据不入库。

**[截图点 S8]** （建议）真实同学使用后的统计页或 `docs/usage_report.md`。

## 10. Qoder Sites 发布（2026-11-10 18:05–18:19）

Qoder 完成：

- 用 `sites-building` 创建 `website/` 单页站点：项目简介、痛点、核心功能、seekdb/PowerContext 分工、应用截图、23 项验证证据、真实使用数据（如实显示“收集中”）、Qoder 开发记录、运行说明、代码仓库。
- 初始化设计工作台（`.温故 · 复习教练.qoder.site`），声明并实现 Nudge 变量：appearance / radius / spacing / typography / motion。
- 用 Playwright + 本机 Chrome 做桌面（1440px）与移动端（390px）验证：
  - 所有图片加载成功；
  - 无控制台错误、无失败请求；
  - 无横向溢出；
  - 导航菜单、灯箱、复制按钮、滚动高亮可用。
- 用 `sites-hosting` 发布，站点地址：
  **https://wengu-review-coach-qia8h1pwid5.qoder.zone**
- 可见范围从 private 改为 **public**（`get_site_access` → `update_access_policy`，`confirmPublic=true`）。
- 用全新无痕浏览器上下文打开公网 URL 验证：HTTP 200、未登录、标题/正文/图片正常、无控制台错误。截图：`docs/screenshots/public-site-incognito-final.png`。
- 二次发布补充公开基础仓库链接（`oceanbase/powercontext`），保留 private 仓库 `qoder/` 的评审访问说明；重新发布后再次无痕验证通过。

**[截图点 S9]** Qoder Sites 创建与发布界面（对应 action `de525455-…`）。

**[截图点 S10]** 无痕窗口打开 https://wengu-review-coach-qia8h1pwid5.qoder.zone 的截图（已保存 `public-site-incognito-final.png`）。

## 11. 提交资产整理（2026-11-10 18:21）

- 已将 `qoder作答/wengu/` 的源码、种子、文档、截图、证据、网站工程复制到 `powercontext/qoder/`。
- 保留：`README.md`、`requirements.txt`、`requirements-dev.txt`、`.env.example`、`.gitignore`、`wengu/`、`templates/`、`static/`、`seed/`、`tests/`、`tools/`、`docs/`、`website/`（含 `index.html`、`styles.css`、`app.js`、`assets/`、`data/`、`dist/`）。
- 排除：`.env`、`.powercontext/`、`.runtime/`、`__pycache__/`、`.pytest_cache/`、`docs/runs/archive_*/`、网站本地 `.qoder.site` 描述文件、`jieba.cache`、`pc-migration-bundle-*`。
- 目标目录共约 130 个文件、12MB；未执行 `git commit` / `git push`，提交由参赛者完成。
- 提交清单见 `docs/submission_checklist.md`。

## 12. 提交清单

| 项目 | 内容 |
| --- | --- |
| 项目网站 URL | https://wengu-review-coach-qia8h1pwid5.qoder.zone |
| 代码仓库 | 大赛私有仓库 `powercontext/qoder/`（评审可见）；基础仓库 https://github.com/oceanbase/powercontext |
| Qoder 账号 UID | 1661591978811838 |
| 运行说明 | `powercontext/qoder/README.md` |
| 验证样例 | `powercontext/qoder/docs/verification_sample.md` |
| 开发记录 | 本文件 |
| 使用效果 | `powercontext/qoder/docs/usage_report.md` |
| 证据 | `powercontext/qoder/docs/runs/`、`docs/screenshots/` |
