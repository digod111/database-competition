# 提交清单

> 生成时间：2026-11-10 18:20  
> 作品：温故 · 复习教练（2026 OceanBase 数据库大赛 · Qoder 生态赛道）

## 一、必填材料

| 材料 | 内容 | 状态 |
| --- | --- | --- |
| 项目网站网址 | https://wengu-review-coach-qia8h1pwid5.qoder.zone | ✅ 已发布，public，无痕验证通过 |
| 代码仓库 | https://github.com/digod111/database-competition（分支 `2026-competition-ecosystem`，作品位于 `qoder/`） | ✅ 已推送 |
| Qoder 账号 UID | 1661591978811838 | ✅ 已写入开发记录 |
| 运行说明 | 仓库根目录 `README.md` | ✅ |
| 验证样例 | `docs/verification_sample.md` | ✅ |
| Qoder 开发记录 | `docs/qoder_dev_record.md`（含截图点 S1–S10） | ✅ |
| 使用效果材料 | `docs/usage_report.md`；当前真实用户 0，演示证据单独列出 | ⏳ 建议使用后更新 |

## 二、资产路径（复制到 `powercontext/qoder/` 后）

```
qoder/
├── README.md                     # 运行说明
├── requirements.txt
├── requirements-dev.txt          # 可选：重新生成截图
├── .env.example
├── .gitignore
├── pytest.ini
├── wengu/                        # 应用源码
├── templates/                    # 页面模板
├── static/                       # 应用样式与 JS
├── seed/                         # 3 门课笔记 + 27 题（自编）
├── tests/                        # 22 单元 + 3 集成测试
├── tools/                        # demo 截图、使用数据导出
├── docs/
│   ├── verification_sample.md
│   ├── technical_note.md
│   ├── qoder_dev_record.md
│   ├── usage_report.md
│   ├── submission_checklist.md   # 本文件
│   ├── screenshots/              # 应用与网站截图
│   └── runs/                     # demo 生成的全部证据
└── website/
    ├── index.html
    ├── styles.css
    ├── app.js
    ├── assets/
    ├── data/
    └── dist/                     # Qoder Sites 发布输出
```

## 三、验收对照

| 验收项 | 证据 |
| --- | --- |
| 网站未登录可打开 | public 站点 + `docs/screenshots/public-site-incognito-final.png`；无痕 HTTP 200 |
| 项目可按说明运行 | README 六步启动；`python -m wengu demo` 一条命令复现主流程 |
| Qoder 开发可核对 | `docs/qoder_dev_record.md`，含需求拆解、探针、编码、调试、测试、发布和截图点 |
| seekdb 实际作用 | 35 片段/27 题入库；`MATCH ... AGAINST` 检索；`db_queries.jsonl` 记录 SQL/参数/相关度/query_id；答题解析引用原文 |
| PowerContext 实际作用 | 四类记忆写入/召回；`context/prepare` citation；重启后新进程召回；B+树题 1→3；交接 completed |
| 验证样例 | `docs/verification_sample.md` + `docs/runs/demo_summary.json`（23/23） |
| 使用效果 | 真实数据为 0，网站如实显示“收集中”；演示数据单独标注，未冒充真实用户 |

## 四、提交前最后检查

- [ ] 把 `qoder作答/wengu/` 内容复制到 `powercontext/qoder/`（不要复制 `.env`、`.powercontext/`、`.runtime/`、`__pycache__/`、`.pytest_cache/`、`docs/runs/archive_*/`、`website/.qoder.site`）。
- [ ] 确认 `powercontext/minimax/` 与其他赛道目录未被修改。
- [x] Qoder 账号 UID 已填写：1661591978811838（见 `docs/qoder_dev_record.md` 顶部）。
- [ ] 运行 `python -m pytest -q`，确认 22 passed / 3 skipped。
- [ ] 运行 `python -m wengu demo`，确认 23/23；如重新运行，证据会覆盖，旧日志归档到 `docs/runs/archive_*`。
- [ ] 如需更新网站：修改 `website/` 后重新发布；使用真实数据前先运行 `python tools/export_usage.py`。
- [ ] 不要暂停或删除已发布的 Qoder Site，评审结束前保持可访问。
- [ ] 检查仓库中没有真实 API Key、密码、`.env` 或本地数据库文件。

## 五、提交平台字段建议

- 项目网站网址：`https://wengu-review-coach-qia8h1pwid5.qoder.zone`
- 代码仓库：`https://github.com/digod111/database-competition`（分支 `2026-competition-ecosystem`，作品位于 `qoder/`）
- Qoder 账号 UID：1661591978811838
- 其余材料：上传/指向 `powercontext/qoder/docs/` 下的文件；验证证据在 `docs/runs/`，截图在 `docs/screenshots/`
