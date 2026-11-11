# DSL 说明

| 文件 | 用途 | 导入方式 |
| --- | --- | --- |
| `tavern-chatflow.yml` | Dify 0.7.0 Chatflow DSL（由 Dify 1.19.0 导出） | Studio → 导入 DSL 文件 |
| `tavern-chatflow.ifpkg` | 同一应用的 Dify 应用包（内含 `app.yaml` + `manifest.yaml`） | Studio → 导入 DSL 文件（选择 `.ifpkg`） |

两个文件内容等价，任选一个导入；导入后是一个 `advanced-chat` 应用，共 22 个节点。

## 导入顺序

1. 先完成 Dify 1.19.0 部署（`../deploy/README.md`）。
2. 安装 DeepSeek 插件 0.0.26（Marketplace，或 `../plugins/deepseek-0.0.26.difypkg` 离线包），填入你自己的 DeepSeek API Key，启用 `deepseek-flash`、`deepseek-v4-pro`。
3. 导入 DSL。
4. 打开应用 → 发布。
5. 在 Web App 页面获取 `http://localhost/chat/<webapp-code>`；短码是导入后新生成的。

DSL 的 `dependencies` 已声明：

```yaml
- type: marketplace
  value:
    marketplace_plugin_unique_identifier: langgenius/deepseek:0.0.26@bdf47cb41e9062bba00d26625d2a795c1874011d8ce64bff738c44b5a31787ea
```

## 重新绑定服务地址/端口

DSL 中有 8 个 HTTP 节点：

| 节点 | 默认 URL |
| --- | --- |
| `scope` | `http://host.docker.internal:18082/v1/scopes` |
| `memory` | `http://host.docker.internal:18082/v1/memory/search` |
| `remember` | `http://host.docker.internal:18082/v1/memory/remember` |
| `state` | `http://host.docker.internal:8010/api/state?...` |
| `act_order` | `http://host.docker.internal:8010/api/order` |
| `act_gossip` | `http://host.docker.internal:8010/api/gossip` |
| `act_prank` | `http://host.docker.internal:8010/api/prank` |
| `act_settle` | `http://host.docker.internal:8010/api/settle` |
| `act_chat` | `http://host.docker.internal:8010/api/chat` |

（`act_*` 5 个工具节点 + `state` 共 6 个后端节点。）

- 改后端端口：编辑 `state` 与 5 个 `act_*` 节点的 URL。
- 改 PowerContext 端口：编辑 `scope`、`memory`、`remember` 节点 URL。
- 改 PowerContext token：编辑 `scope`、`memory`、`remember` 节点的 `Authorization: Bearer ...` Header，
  并与 `scripts/powercontext_ctl.py` 的 `--token` 保持一致。
- Linux 上 `host.docker.internal` 需要 `deploy/docker-compose.override.yaml` 的 `extra_hosts`。

## 其他

- 开始节点变量：`player_id`，默认 `player_01`；这是 seekdb 玩家键和 PowerContext scope 键。
- LLM 节点 provider：`langgenius/deepseek/deepseek`；模型：`deepseek-flash`、`deepseek-v4-pro`。
- DSL 只含演示占位 token `tavern-demo-token`，**不含任何真实 API Key**。
- 应用 Web App 的短码不写进 DSL，导入后由 Dify 生成。
