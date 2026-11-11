# Dify 1.19.0 自托管部署（忘忧酒馆）

本目录只放**部署输入**。Dify 的 compose 与 envs 使用官方 1.19.0 文件，版本固定，避免把官方大文件复制进赛道目录。

## 1. 获取官方 Dify 1.19.0 的 docker 文件

```bash
# Linux / macOS / Git Bash（国内网络）
bash deploy/fetch_dify.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File deploy/fetch_dify.ps1
```

脚本会把 `dify-1.19.0/docker/*` 解压到 `deploy/dify/`。若脚本不可用，可手动下载：

- GitHub: `https://github.com/langgenius/dify/archive/refs/tags/1.19.0.tar.gz`
- 国内代理: 在上面 URL 前加 `https://gh-proxy.com/`
- 解压后把 `dify-1.19.0/docker/` 的内容放到 `deploy/dify/`

## 2. 配置并启动

```bash
cd deploy/dify
cp ../.env.example .env
# 生成 sandbox token，填到 .env 的 DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN
openssl rand -hex 32
```

`.env` 的关键项（见 `../.env.example`）：

- `COMPOSE_PROFILES=postgresql,pgvector`、`VECTOR_STORE=pgvector`：元数据库和 Dify 内部向量库都用官方自带镜像；本作品不使用知识库/embedding。
- `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal`：允许 Dify HTTP Request 节点访问宿主机上的后端（8010）与 PowerContext（18082）。这是**必须**的，否则请求会被 SSRF 代理拒绝。
- `DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN`：1.19 默认启动 Agent local sandbox，必须设置。

启动：

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.override.yaml up -d
docker compose ps --all
```

预期：`init_permissions` 退出码 0；`api`、`worker`、`plugin_daemon`、`db_postgres`、`redis`、`pgvector`、`sandbox`、`nginx` 正常运行。

> `docker-compose.override.yaml` 为 Linux 补充 `host.docker.internal:host-gateway`。Docker Desktop（Windows/macOS）通常已内置该域名，如遇解析问题可去掉 override 中的 `extra_hosts`。

## 3. 首次安装 Dify

1. 打开 `http://localhost/install`，创建管理员账号（自己设置，材料中不提供真实密码）。
2. 登录后进入 **Plugins / 插件**，从 Marketplace 安装 **DeepSeek** 插件，版本 **0.0.26**（插件标识：`langgenius/deepseek:0.0.26@bdf47cb41e9062bba00d26625d2a795c1874011d8ce64bff738c44b5a31787ea`）。若 Marketplace 不可达，改用 `../plugins/deepseek-0.0.26.difypkg` 本地安装。
   - DSL 的 `dependencies` 已声明该插件；导入 DSL 时若提示缺少依赖，也可在提示中安装。
   - 插件安装默认由容器访问 `https://marketplace.dify.ai`；无外网时改用本地包。安装依赖 Python 包时如很慢，可在 `.env` 设置 `PIP_MIRROR_URL=https://pypi.tuna.tsinghua.edu.cn/simple` 后 `docker compose up -d` 重建插件容器。
3. **Settings → Model Provider → DeepSeek**，填入你自己的 DeepSeek API Key。
4. 在 DeepSeek 模型列表中**启用** `deepseek-flash` 和 `deepseek-v4-pro`（本作品分别用于意图解析/角色回复）。
   - 如果导入 DSL 后 LLM 节点显示模型不可用，回到这里检查插件版本、API Key 和两个模型的启用状态。

## 4. 导入应用并发布

1. 进入 **Studio → 创建应用 → 导入 DSL 文件**，选择 `../dsl/tavern-chatflow.yml`（或 `tavern-chatflow.ifpkg`）。
2. 打开应用，确认开始节点参数 `player_id` 默认 `player_01`。
3. 点击 **发布 / Publish**。
4. 在应用 **Web App** 页面找到公开访问链接：`http://localhost/chat/<webapp-code>`。`<webapp-code>` 是导入后新生成的短码，不是应用 UUID。
5. 打开链接 → “新对话设置”中保留 `player_01` → 点击 **开始对话** → 按 `docs/verification_sample.md` 体验。

## 5. 镜像清单（实测通过）

| 镜像 | 用途 |
| --- | --- |
| `langgenius/dify-api:1.19.0` | api / worker / worker_beat |
| `langgenius/dify-web:1.19.0` | Web |
| `langgenius/dify-sandbox:0.2.15` | Code 节点沙箱 |
| `langgenius/dify-plugin-daemon:0.6.14-local` | 插件/模型运行时 |
| `langgenius/dify-agent-backend:1.19.0` | Agent 后端（1.19 默认依赖） |
| `langgenius/dify-agent-local-sandbox:1.19.0` | Agent local sandbox |
| `postgres:15-alpine`、`redis:6-alpine`、`nginx:latest` | 元数据库/缓存/网关 |
| `pgvector/pgvector:pg16` | Dify 内部向量库（本作品不使用知识库） |
| `ubuntu/squid:latest`、`busybox:latest` | SSRF 代理 / 初始化 |

国内拉取 Docker Hub 镜像可配置 registry mirror；本次实测 `docker.m.daocloud.io`、`docker.1panel.live` 可用。`cr.weaviate.io` 不在本部署中使用（未启用 weaviate profile）。

## 6. 网络与端口

- Dify Web：`http://localhost`（或 `EXPOSE_NGINX_PORT`）
- FastAPI 后端：宿主机 `8010`（Dify 容器用 `host.docker.internal:8010`）
- PowerContext：宿主机 `18082`（Dify 容器用 `host.docker.internal:18082`）
- seekdb：宿主机 `2881`（仅 FastAPI 后端直连，不暴露给 Dify 容器）
- 如果修改后端/PowerContext 端口，需要同步修改 DSL 中所有 HTTP 节点 URL（`scope`、`state`、`memory`、`remember`、5 个工具节点）。

## 7. 常见问题

- **HTTP 节点 403/超时**：检查 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal` 是否进入 `ssrf_proxy` 容器；`docker compose exec ssrf_proxy env | grep SSRF` 应能看到该值。
- **PowerContext 401**：DSL 的 `Authorization` 使用演示占位 token `tavern-demo-token`；`scripts/powercontext_ctl.py` 默认使用同一值。改 token 时两边一起改。
- **scope 409**：PowerContext 的 scope 幂等键与参数不一致。DSL 使用 `tavern-{player_id}-v2`；换一个 generation 或重置 PowerContext home 即可。
- **Web App 打不开**：确认已发布；访问链接使用 Web App 短码，不是应用 UUID；`/install` 只用于首次建管理员。
- **端口 80 被占用**：在 `.env` 设置 `EXPOSE_NGINX_PORT=8080`，重新 `docker compose up -d`，访问 `http://localhost:8080`。
