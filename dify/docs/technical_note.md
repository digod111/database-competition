# 技术说明：忘忧酒馆 · 记仇掌柜

## 1. 架构

```text
Dify 1.19.0 Chatflow（Web App / 控制台）
  ├─ Code 节点（sandbox 8194）：清洗输入、解析 JSON、整理记忆、清理 <think>
  ├─ LLM 节点：deepseek-flash（意图/槽位）、deepseek-v4-pro（阿九回复）
  ├─ IF/ELSE + 5 个工具节点 + Variable Aggregator
  ├─ HTTP Request（经 ssrf_proxy）
  │    ├─ FastAPI 后端 host.docker.internal:8010
  │    │    └─ PyMySQL → seekdb 2881 / dify_app
  │    └─ PowerContext host.docker.internal:18082（SQLite，Bearer token）
  └─ Answer
```

- **Dify**：输入清洗、意图/槽位模型调用、条件判断、工具选择、结果聚合、角色回复、最终输出。
- **seekdb**：应用数据服务。玩家账本、酒单库存、事件流水、恶作剧台账、谣言全文检索。
- **PowerContext**：跨会话记忆。每个 `player_id` 一个 scope；`remember` 写入，`memory/search` 召回。
- **FastAPI 后端**：只做数据读写与检索，不做意图分类/对话编排。

## 2. 版本

| 组件 | 版本/方式 |
| --- | --- |
| Dify | 1.19.0，官方 docker compose 自托管 |
| Dify 模型插件 | DeepSeek `0.0.26`（`langgenius/deepseek`） |
| 模型 | `deepseek-flash`（意图）、`deepseek-v4-pro`（回复） |
| seekdb | `oceanbase/seekdb:latest`，实测 `5.7.25-OceanBase seekdb-v1.4.0.0` |
| PowerContext | `powercontext[cli,server]==1.5.0`，SQLite，本地 HTTP 18082 |
| 后端 | Python 3.13、FastAPI、uvicorn、PyMySQL、python-dotenv |
| Dify 内部向量库 | `pgvector/pgvector:pg16`（本作品不使用知识库/embedding） |

## 3. Dify Chatflow 节点

共 22 个节点（导出 DSL 0.7.0）：

```text
start → clean_input → scope → parse_scope → state → parse_state
      → classify → parse_intent → memory → parse_context
      → branch(if-else) ─┬→ act_order  ─┐
                         ├→ act_gossip ─┤
                         ├→ act_prank  ─┼→ aggregate(variable-aggregator)
                         ├→ act_settle ─┤
                         └→ act_chat   ─┘
      → parse_action → reply → clean_reply → remember → answer
```

| 节点 | 作用 |
| --- | --- |
| `scope` | `POST /v1/scopes`，按 `player_id` 幂等创建/复用 PowerContext scope（key `tavern-{player_id}-v2`） |
| `state` | `GET /api/state`，读取 seekdb 玩家账本、酒单、最近事件 |
| `classify` | 单次 LLM 调用输出 `intent/drink/pay_mode/query/prank/nickname/remember/memory_query` |
| `parse_intent` | 去掉 `<think>`/代码围栏，容错解析 JSON；非法 intent 回退 `chat`；空 `memory_query` 回退 `账 酒 恶作剧 传闻` |
| `memory` | `POST /v1/memory/search`，用 `memory_query` 做 FTS 检索（`mode=fts`，limit 8） |
| `parse_context` | 从 `hits[].memory.text` 提取 active 记忆，拼成提示词中的“召回记忆” |
| `branch` | IF/ELSE：order / gossip / prank / settle / ELSE(chat) |
| 5 个工具节点 | 分别调用后端 `/api/order`、`/api/gossip`、`/api/prank`、`/api/settle`、`/api/chat` |
| `aggregate` | 只把实际执行分支的 HTTP `body` 合并成 `output` |
| `parse_action` | 解析动作结果/错误/后置状态栏，生成 `memory_text`（动作记忆或 `remember` 或对话兜底） |
| `reply` | 阿九人设 + 后置状态 + 召回记忆 + 动作事实；`max_tokens=1500` |
| `clean_reply` | 去掉 `<think>`，空回复兜底 |
| `remember` | `POST /v1/memory/remember`，`kind=fact` 写入本轮记忆 |

### 为什么用 `memory/search` 而不是 `context/prepare`

开发中实测：`/v1/context/prepare` 对“整句用户输入”的召回不稳定（新写入的记忆在下一轮可能返回 `empty`），
而 `/v1/memory/search` 用短关键词（由 LLM 产出的 `memory_query`）命中稳定、可解释，且响应中直接带
`artifact_id / text / score`，便于评审核对。`context/prepare` 的开发期验证保留在
`evidence/powercontext_restart.txt`（M2 阶段），最终应用采用 search + remember 的显式闭环。

## 4. seekdb 数据模型

`data/schema.sql`：

| 表 | 关键字段 | 作用 |
| --- | --- | --- |
| `drinks` | name/price/stock/effect/hidden | 酒单与库存；`狐尾酒` 为隐藏酒 |
| `players` | player_id/nickname/coins/debt/grudge/reputation/visits/last_session_id | 玩家账本 |
| `events` | player_id/session_id/event_type/detail | 审计流水（enter/order/prank/chat/rumor/profile/settle） |
| `pranks` | player_id/description/grudge_delta | 恶作剧台账 |
| `rumors` | title/content/tags/heat + `FULLTEXT INDEX ... WITH PARSER ik` | 谣言全文检索 |

- 中文全文索引使用 seekdb 的 `ik` 解析器；`MATCH(title,content,tags) AGAINST(...)`。
- 后端 `RUMOR_MIN_SCORE=0.5` 过滤低分噪音，无有效命中时回退到分词 `LIKE`（`fallback=true`）。
- `data/init_db.py` 可重复执行：重建表、恢复种子；`data/verify.sql` 用于人工核对。
- seekdb 只作为**应用数据服务**，不是 Dify 的元数据库；Dify 元数据库使用自带 Postgres。

## 5. PowerContext 记忆设计

- **scope 隔离**：一个 `player_id` 一个 scope；由 Dify 的 `scope` 节点调用 `POST /v1/scopes` 创建，
  `idempotency_key=tavern-{player_id}-v2`，相同参数重复调用幂等返回同一个 scope。
- **写入**：每轮结束后 `remember` 节点写入 `kind=fact` 的记忆，例如：
  - `阿远在忘忧酒馆点了断肠烧，赊账 12 铜板；当前铜板30、欠账12、戒心1。`
  - `阿远对阿九恶作剧：把吧台的盐罐换成糖罐。阿九戒心+2，当前戒心3。`
- **召回**：每轮由 LLM 根据用户消息 + seekdb 状态生成 `memory_query`（如 `阿远 欠账 恶作剧 盐罐`），
  `/v1/memory/search?mode=fts` 返回命中；命中文本进入分类提示词与回复提示词。
- **重置**：PowerContext 1.5 的 scope 不支持 DELETE；重置通过停止服务、替换 `POWERCONTEXT_HOME`
  并重启实现（`scripts/powercontext_ctl.py reset`），旧 home 保留为 `.bak.<timestamp>`。
- **鉴权**：`POWERCONTEXT_SERVER_ACCESS_MODE=enforced` + `POWERCONTEXT_SERVER_AUTH_TOKEN`；
  DSL 中的 `tavern-demo-token` 是演示占位值，换 token 时需同步修改 DSL 的三个 HTTP 节点头。

## 6. 网络与 Dify 配置

- Dify HTTP Request 节点默认经 `ssrf_proxy`，必须放行宿主机域名：
  `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal`。
- `host.docker.internal` 在 Docker Desktop 自动可用；Linux 由 `deploy/docker-compose.override.yaml`
  的 `extra_hosts: host.docker.internal:host-gateway` 提供。
- 端口：Dify 80、后端 8010、PowerContext 18082、seekdb 2881。
  后端 8000 在本机被其他服务占用，因此选用 8010；DSL 中所有后端 URL 使用 8010。
- Dify 元数据库 Postgres、内部向量库 pgvector 均为官方 compose 自带；`COMPOSE_PROFILES=postgresql,pgvector`，
  未启用 weaviate/collaboration。Agent backend/local sandbox 是 1.19 默认依赖，会随栈启动。
- Dify 1.19 的模型输出可能带 `<think>` 推理块；分类与回复的 Code 节点会剥离，回复 `max_tokens=1500`
  避免推理占满导致空回复；`clean_reply` 还有空回复兜底。

## 7. 错误处理

- 后端业务失败统一返回 HTTP 200 + `{"ok": false, "error": {"code", "message"}}`，Dify 用
  `parse_action.ok` 与错误文案让阿九按失败原因回应，不假装成功。
- 意图 JSON 解析失败回退 `chat`；`memory_query` 为空回退固定关键词；scope 创建失败时
  `scope_id` 为空，记忆写入会失败但工作流仍可用 seekdb 状态完成回复（不崩）。
- 谣言检索低分过滤 + LIKE 回退；库存不足/赊账上限/通缉等都有明确错误码。
- LLM 空回复由 `clean_reply` 兜底；HTTP 节点超时/重试配置为 10s 连接、60s 读取、不自动重试。

## 8. 已知限制

- 记忆召回是**关键词 FTS**，不是语义向量召回；换一种完全不同的说法可能召不回旧事。
  但本作品用 LLM 生成 `memory_query` + 固定兜底词，覆盖“记得我吗/上次/旧账/恶作剧”等常见问法。
- `player_id` 是记忆与账本的唯一键；多人体验需要各自使用不同 `player_id`。
- PowerContext scope 不支持删除；重置靠替换 home（脚本已提供）。
- Dify Web App 的短码在每次导入后重新生成，不能用本仓库证据中的固定短码。
- 演示 token/本地空密码仅用于单机体验，公网部署必须替换为强 token/密码并加 HTTPS。
- 未使用 Dify 知识库与 embedding；如果后续要加知识库，可另配 embedding 服务，不影响本作品主链路。

## 9. 数据与来源

- 酒单、谣言、玩家账本、恶作剧文案均为本作品自编，无第三方版权数据。
- Dify compose 来自官方 `langgenius/dify` 1.19.0；DeepSeek 插件来自 Dify Marketplace；
  PowerContext 来自 `oceanbase/powercontext`；seekdb 镜像来自 `oceanbase/seekdb`。
- 官方 `api/tests/fixtures/workflow/basic_chatflow.yml` 仅用于确认 DSL 节点字段格式，未复用其业务内容。
