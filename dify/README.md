# 忘忧酒馆 · 记仇掌柜

> 2026 OceanBase 数据库大赛 · 生态赛道 Dify 赛题  
> Dify 1.19.0 + seekdb + PowerContext

一个雨夜酒馆的互动小游戏：掌柜阿九会记住你的名字、你点的酒、你赊的账，以及你每一次恶作剧。
**新开一个会话，他还会翻旧账。**

## 一、玩法与趣味点

- 你推门进店，阿九（半妖狐狸掌柜）嘴硬心软、记性极好。点酒、赊账、打听谣言、搞恶作剧，
  每一笔都会落到 seekdb 的账本/事件/恶作剧台账里，状态栏实时变化：
  `【铜板 30｜欠账 12｜戒心 3（白眼）｜声望 1】`
- 跨会话记忆由 PowerContext 保存：你叫阿远、你欠十二铜板、你把盐罐换成糖罐……下一次新对话，
  阿九会主动点破。
- 戒心会分级（平静 → 白眼 → 记仇 → 通缉），影响赊账、隐藏酒（狐尾酒）和对话态度；
  也可以结账还债、把戒心降下来。
- 2-3 分钟能体验完整闭环：报名 → 赊酒 → 恶作剧 → 新会话被翻旧账。

建议开场（Dify Web App 的推荐问题也包含）：

```text
我叫阿远，记住这个名字。
给我来一杯断肠烧，先赊着，回头一起结。
我把你吧台的盐罐换成糖罐了。
# 新对话
老板，还认得我吗？上次的账和恶作剧还记得吗？
```

完整可复验样例：见 [`docs/verification_sample.md`](docs/verification_sample.md)。

## 二、技术分工

| 组成 | 在作品中的职责 | 可核对记录 |
| --- | --- | --- |
| **Dify 1.19.0** | 核心流程编排：输入清洗、意图/槽位模型调用、PowerContext/seekdb 检索与工具调用、IF/ELSE 条件、5 个工具分支 + 变量聚合、角色回复、最终输出 | 22 节点 Chatflow DSL；`docs/evidence/dify_node_evidence.txt` 节点 HTTP 状态码 |
| **seekdb** | 应用数据服务：玩家账本、酒单库存、事件流水、恶作剧台账、谣言中文全文检索（IK） | `data/schema.sql`；`docs/evidence/seekdb_state.txt` 实际行；`seekdb_fulltext.txt` |
| **PowerContext 1.5.0** | 跨会话记忆：每个 `player_id` 一个 scope；`remember` 写入、`memory/search` 召回；新会话仍能翻旧账 | `docs/evidence/powercontext_memory.txt`；`powercontext_restart.txt` |
| FastAPI 后端 | 仅做 seekdb 数据读写/检索与统一 JSON，不承担意图分类/对话编排 | `backend/app.py` |
| DeepSeek | 意图/槽位 JSON（`deepseek-flash`）与阿九角色回复（`deepseek-v4-pro`） | Dify 插件 0.0.26；`docs/evidence/dify_setup.txt` |

> Dify 在 1.16+ 已原生支持 seekdb 向量库，但本作品刻意把 seekdb 放在**业务数据/检索**层：
> 账本、库存、恶作剧、谣言全文都由一条真实 SQL 链路承载，而不是只当元数据库。
> Dify 自身元数据库使用官方自带 Postgres，内部向量库使用 pgvector（本作品不使用知识库/embedding）。

## 三、架构与数据流

```text
Dify 1.19.0 Chatflow（公开 Web App / 控制台预览）
  ├─ Code 节点（sandbox）：清洗、JSON 解析、记忆整理、去 <think>
  ├─ LLM：deepseek-flash（意图+memory_query） / deepseek-v4-pro（阿九）
  ├─ IF/ELSE：order / gossip / prank / settle / chat
  ├─ 5 个工具 HTTP 节点 → Variable Aggregator
  ├─ HTTP /v1/scopes、/v1/memory/search、/v1/memory/remember
  │        └─ PowerContext 18082（SQLite + Bearer token）
  └─ HTTP /api/state、/api/order、/api/gossip、/api/prank、/api/settle、/api/chat
           └─ FastAPI 8010 → seekdb 2881 / dify_app
```

每轮关键顺序（22 节点 DSL 0.7.0）：

`start → clean_input → scope → parse_scope → state → parse_state → classify → parse_intent
→ memory(search) → parse_context → branch → 工具 → aggregate → parse_action → reply
→ clean_reply → remember → answer`

- LLM 一次输出 `intent / drink / pay_mode / query / prank / nickname / remember / memory_query`；
- `memory_query` 是 1-4 个短关键词（如“阿远 欠账 恶作剧 盐罐”），用于 PowerContext FTS；
- 动作结果统一由后端返回 `{ok, result_text, memory, state, error}`；Dify 用 `ok` 和错误文案
  让阿九按失败原因回应，不假装成功。

## 四、版本与依赖

| 组件 | 版本/方式 |
| --- | --- |
| Dify | **1.19.0**，官方 docker compose 自托管（见 `deploy/`） |
| Dify 模型插件 | DeepSeek **0.0.26**（`langgenius/deepseek`，Marketplace） |
| 模型 | `deepseek-flash`（意图/槽位）、`deepseek-v4-pro`（角色回复） |
| seekdb | `oceanbase/seekdb:latest`，实测 `5.7.25-OceanBase seekdb-v1.4.0.0` |
| PowerContext | `powercontext[cli,server]==1.5.0`，SQLite 后端，HTTP 18082 |
| 后端 | Python 3.11+（实测 3.13）、FastAPI 0.162.1、uvicorn 0.54.0、PyMySQL 1.2.x、python-dotenv |
| Dify 内部向量库 | `pgvector/pgvector:pg16`（不使用知识库，无需 embedding） |

## 五、目录

```text
dify/
├── README.md
├── .gitignore
├── requirements.txt            # 完整 Python 依赖（后端 + PowerContext）
├── dsl/
│   ├── tavern-chatflow.yml     # Dify 0.7.0 DSL（可直接导入）
│   ├── tavern-chatflow.ifpkg   # Dify 应用包（可导入；内含 app.yaml/manifest.yaml）
│   └── README.md
├── plugins/                    # DeepSeek 插件离线安装包
│   ├── deepseek-0.0.26.difypkg
│   └── README.md
├── backend/                    # FastAPI + PyMySQL
│   ├── app.py  db.py  run.py
│   ├── requirements.txt  .env.example
│   ├── start.sh  start.ps1  README.md
├── data/                       # seekdb 建库、种子、重置、核对
│   ├── schema.sql  seed.sql  init_db.py  reset_db.py  verify.sql  README.md
├── deploy/                     # Dify 部署输入（官方 1.19.0 compose 另行下载）
│   ├── .env.example  docker-compose.override.yaml
│   ├── fetch_dify.sh  fetch_dify.ps1  README.md
├── scripts/
│   ├── powercontext_ctl.py     # PowerContext 启停/重置
│   └── reset_demo.py           # 一键重置 seekdb + 记忆 home
└── docs/
    ├── verification_sample.md  # 初始状态/输入/预期/实际/重置
    ├── technical_note.md       # 架构、节点、数据、失败处理、限制
    ├── dev_record.md           # 开发记录与踩坑
    └── evidence/               # 实际运行证据（无真实凭据）
```

## 六、从零复现

### 0. 前置

- Docker（含 Compose v2.35+）、Python 3.11+（推荐 3.13）、Git Bash/PowerShell。
- 端口：80（Dify）、8010（后端）、18082（PowerContext）、2881（seekdb）。
  如 8000 被占用，本作品默认后端为 8010；改端口时需同步 DSL。
- 可访问 DeepSeek API；Dify Marketplace 可选（已附离线插件包），国内拉镜像建议配置 registry mirror。
- 宿主机内存建议 ≥ 8GB 可用给 Docker；Dify 1.19 默认栈约 3-4GB。

### 1. 启动 seekdb 并初始化 `dify_app`

```bash
docker run -d --name seekdb-tavern -p 2881:2881 oceanbase/seekdb:latest
# 若已有 seekdb 在 2881，可直接复用，不要动 test 库
```

```bash
cd powercontext/dify
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp backend/.env.example backend/.env
# 按需修改 SEEKDB_HOST/PORT/USER/PASSWORD/DATABASE（默认 127.0.0.1:2881 root 空密码 dify_app）
python data/init_db.py
```

预期：`initialized dify_app: drinks=5 rumors=10 players=1`。

### 2. 启动 FastAPI 后端

```bash
cd backend
bash start.sh        # Linux/macOS/Git Bash
# Windows PowerShell: .\start.ps1
```

验证：`curl http://127.0.0.1:8010/health` → `{"ok":true,"db":"seekdb",...}`。

### 3. 启动 PowerContext

```bash
# powercontext[cli,server]==1.5.0 已包含在根 requirements.txt
python scripts/powercontext_ctl.py start
```

默认 `.runtime/powercontext`、端口 18082、`POWERCONTEXT_SERVER_ACCESS_MODE=enforced`、
token `tavern-demo-token`。验证：`curl http://127.0.0.1:18082/health/live` → 200。

> DSL 中的三个 PowerContext HTTP 节点（scope/memory/remember）使用同一演示 token。改 token 时，
> 在 `scripts/powercontext_ctl.py` 与 Dify 节点 Header 中一起改。生产部署必须换强 token。

### 4. 部署 Dify 1.19.0

严格按 [`deploy/README.md`](deploy/README.md)：

1. `bash deploy/fetch_dify.sh`（或 PowerShell）下载官方 1.19.0 docker 文件到 `deploy/dify/`；
2. 复制 `deploy/.env.example` → `deploy/dify/.env`，生成 `DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN`；
3. `docker compose -f docker-compose.yaml -f ../docker-compose.override.yaml up -d`；
4. 打开 `http://localhost/install` 创建管理员；
5. 安装 DeepSeek 插件 **0.0.26**（Marketplace 或 `plugins/deepseek-0.0.26.difypkg` 离线包），
   填入**你自己的** DeepSeek API Key，启用 `deepseek-flash` 与 `deepseek-v4-pro`；
6. Studio → 导入 DSL 文件 → `dsl/tavern-chatflow.yml`（或 `.ifpkg`）→ 打开应用 → **发布**；
7. 在应用 Web App 页面获取公开链接 `http://localhost/chat/<webapp-code>`。

> Dify HTTP Request 节点必须能访问宿主机：
> `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal` 已在 `deploy/.env.example` 中；
> Linux 还需 `docker-compose.override.yaml` 的 `host.docker.internal:host-gateway`。

### 5. 体验

打开 Web App → 新对话设置保留 `player_01` → 开始对话 → 按
[`docs/verification_sample.md`](docs/verification_sample.md) 输入四句话（中间点“新对话”）。
也可以用 Dify 控制台的 Chatflow 预览运行。

### 6. 重置

```bash
python scripts/reset_demo.py
```

- 重建 seekdb `dify_app`（清空玩家/事件/恶作剧，恢复酒单/谣言）；
- 停止 PowerContext，把旧记忆 home 改名为 `.bak.<timestamp>`，用全新 SQLite 启动；
- 之后**新建 Dify 对话**，即回到“阿九不认识你”的初始状态。
- 原始 home 在 `.runtime/` 下，可手动删除备份。

## 七、验证与证据

| 验收项 | 证据 |
| --- | --- |
| DSL 可导入、可运行 | `dsl/tavern-chatflow.yml` / `.ifpkg`；`docs/evidence/import_test.md` 导出包重新导入并跑通 |
| 主要玩法可体验 | `docs/verification_sample.md` 四轮实际输出；`docs/evidence/answers.md`；Web UI 快照 `chat_ui_snapshot.txt` |
| seekdb 实际作用 | `docs/evidence/seekdb_state.txt`（players/pranks/drinks/events 实际行）；`seekdb_fulltext.txt`（IK 全文检索） |
| PowerContext 实际作用 | `docs/evidence/powercontext_memory.txt`（scope + search 命中 2 条旧记忆）；`powercontext_restart.txt`（进程重启后仍可召回）；`memory_only_recall.md`（清空 seekdb 事件/恶作剧/账本后仍能说出盐罐恶作剧） |
| Dify 节点执行记录 | `docs/evidence/dify_node_evidence.txt`（scope/memory/action/remember 的 HTTP 状态码；第 4 轮 memory_query 与命中） |
| 重置可复现 | `docs/evidence/reset_smoke.md`（重置后同问题变“客官面生”） |

## 八、已知限制

- 记忆召回基于 PowerContext **FTS 关键词**，不是向量语义召回；换完全不同的说法可能召不回。
  作品用 LLM 生成 `memory_query` + 固定兜底词缓解。
- `player_id` 是账本与记忆的唯一键；多人体验请各自使用不同 `player_id`（如 `player_02`）。
- PowerContext 1.5 的 scope 不支持删除，重置通过替换 home 实现（脚本已提供）。
- Dify Web App 短码在每次导入后重新生成，请以自己实例的 Web App 页面为准。
- 本作品不使用 Dify 知识库/embedding；Dify 的 pgvector 仅作为内部向量库配置存在。
- 演示用 PowerContext token、seekdb 空密码、Dify 本地账号仅用于单机体验；公网部署必须替换并加 HTTPS。
- 宿主机内存紧张时，Dify 1.19 全栈 + 两个 Python 服务可能吃紧；建议先启动 Dify，再启动后端/PowerContext。

## 九、凭据与数据

- 仓库不包含任何真实 API Key、密码；`backend/.env`、`deploy/dify/.env`、`.runtime/` 已加入 `.gitignore`。
- `.env.example` 与 DSL 中只出现**占位值**（`tavern-demo-token`、`REPLACE_WITH_OPENSSL_RAND_HEX_32`、空密码）。
- DeepSeek Key 只填在 Dify 自己的工作区配置中，不进入 DSL/日志/仓库。
- 酒单、谣言、玩家数据均为本作品自编；未复用第三方版权数据。

## 十、参考与来源

- Dify 官方仓库与 1.19.0 docker compose：<https://github.com/langgenius/dify>
- DeepSeek 模型插件（Dify Marketplace）：`langgenius/deepseek` 0.0.26
- PowerContext：<https://github.com/oceanbase/powercontext>（接口文档见 powercontext.oceanbase.io）
- seekdb：<https://github.com/oceanbase/seekdb>
- 仅参考了 Dify 官方 `api/tests/fixtures/workflow/basic_chatflow.yml` 的 DSL 节点字段格式；
  未复用其业务内容或公开工作流。
