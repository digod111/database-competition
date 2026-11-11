# 开发记录（2026-11-10）

## 环境

- Windows 11，Git Bash，16GB 内存（Docker Desktop 可用 8GB）
- Python 3.13.9（项目 venv）
- Docker 29.9.2 / Docker Compose 5.6.1
- seekdb `5.7.25-OceanBase seekdb-v1.4.0.0`（容器 `seekdb-minimax`，127.0.0.1:2881，root/空密码）
- PowerContext 1.5.0（SQLite；18082）
- Dify 1.19.0（自托管）

## 时间线

### 1. 环境检查与选型

- 读取题面：Just for Fun，Dify 必选，seekdb/PowerContext 至少一种。
- 发现 Dify 1.19 官方 compose 已原生带 seekdb 向量库 profile（1.16+ 就有），不需要适配分支；
  但本作品按“seekdb 做应用数据服务”设计，避免把 seekdb 只当元数据库的弱结合。
- 网络：Docker Hub 直连超时；`docker.m.daocloud.io`、`docker.1panel.live` 可用；`gh-proxy.com`、
  `hf-mirror.com` 可用；DeepSeek Key 有效，模型 `deepseek-flash`/`deepseek-v4-pro`。

### 2. Dify 镜像拉取与 OOM

- `langgenius/dify-api:1.19.0` 3.8GB、`dify-plugin-daemon` 2.52GB 等镜像较大；拉取峰值把两个
  seekdb 容器 OOM（exit 137）。恢复 `seekdb-minimax`，后续改为逐个镜像拉取、优先显式 daocloud 源。
- 宿主机可用内存一度低至 0.6GB；PowerContext 启动出现 `MemoryError`。停止并发拉取后恢复到 1.6GB，
  重试成功；后续重置/启动均在服务空闲时进行。

### 3. Dify 1.19 配置差异

- 1.19 把配置拆到 `docker/envs/**/*.defaults`，根 `.env` 只放必要输入。
- 采用 `COMPOSE_PROFILES=postgresql,pgvector`、`VECTOR_STORE=pgvector`，避开 cr.weaviate.io 和 collaboration。
- HTTP Request 节点默认走 `ssrf_proxy`，私网被拒；设置
  `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal` 后，Dify 容器可访问宿主 8010/18082。
- 登录接口的密码字段是 Base64 编码（`libs/encryption.py`），不是 RSA；用 Base64 登录 console API，
  cookie + `X-CSRF-Token` 调后续接口。
- DeepSeek 插件 0.0.26 从 Marketplace 安装成功；凭据写入后 PATCH `/models/enable` 启用
  `deepseek-flash`、`deepseek-v4-pro`。

### 4. DSL 与联调

- 先用官方 `basic_chatflow.yml` 学习 0.3.1 节点字段，导入最小 Start→LLM→Answer 验证模型连通。
- 完整 DSL 用 Python 生成，导入 0.7.0 成功；节点 21→22 个。
- 首次运行在 variable-aggregator 报 `advanced_settings.groups Field required`；补 `groups: []` 后通过。
- Dify draft run 的 JSON body 不能带 `files: []`（会 400），最终脚本不发送该字段。
- scope 幂等键冲突：M2 测试用过 `tavern-player_01-v1` 且 summary 不同，Dify 传同键得到 409，
  `scope_id` 为空导致 remember 422。改用 `v2` + 统一 summary，并在每次导入后检查 scope 201/200。
- `/context/prepare` 对整句输入不稳定（第 2/3/4 轮多次返回 empty）；改为由 LLM 输出短
  `memory_query`，Dify 调 `/v1/memory/search`（FTS），命中稳定。流程因此重排为
  state→parse_state→classify→parse_intent→memory search→parse_context→branch。
- 回复曾出现“账清了”但状态栏欠账 12；在系统提示词加“当前欠账以状态为准，欠账>0 不得说账清了”。
- `deepseek-v4-pro` 一次回复被推理吃掉 max_tokens（500），返回空字符串；提高到 1500，并在
  `clean_reply` 加空回复兜底。
- 公开 Web App：`/chat/{app_uuid}` 404；正确链接是 `/chat/{site.code}`（短码）。页面快照可见
  阿九准确召回；当前内置浏览器无可见 surface，无法导出 PNG，保留可访问性快照文本。

### 5. 交付与复验

- 导出 `tavern-chatflow.ifpkg` 与 `app.yaml`；再次 multipart 导入为新应用并跑通。
- `scripts/powercontext_ctl.py reset` + `data/init_db.py` 后，同问题变“客官面生”，证明记忆真实。
- 最终证据：四轮回复、seekdb 行、PowerContext scope/search 命中、Dify 节点状态码、导入/重置测试。

## 未采用/放弃的方案

- 用 seekdb 作为 Dify 向量库：1.19 原生支持，但需要额外 embedding 才能建知识库；本作品主故事
  不依赖知识库，改用 seekdb 账本/全文检索 + PowerContext 记忆，避免 embedding 依赖和额外服务。
- 用 Qoder CN 当模型 API：Qoder CN 是订阅制 IDE/Agent，不提供外部应用可用的 OpenAI 兼容 Key；
  最终使用已验证的 DeepSeek Key（材料不包含 Key）。
- 变量聚合器多组模式：单组 `output` 足够；`advanced_settings.groups=[]` 即可。
- 用 Dify Cloud：无法访问本机 8010/18082/2881，不满足技术结合的可验证性。

## 经验

- Dify 1.19 的 envs 分层、SSRF 放行、Web App 短码是三个最容易踩的复现点。
- 记忆类作品要把“本轮状态”和“历史记忆”分开注入，并让状态栏/数据库成为可核对的不变量。
- PowerContext 的 `prepare` 适合“组装上下文”，但在短查询/即时召回场景，显式 `search` 更可控。
- 大镜像拉取会挤压内存，先把服务停掉/逐个拉取，能避免 seekdb/PowerContext 被 OOM。
