# MiniMax 赛道：规有所依 —— SDD 上下文管理

本目录是第六届 OceanBase 数据库大赛 MiniMax 赛道的提交材料。实现一个面向规格驱动开发（Spec-Driven Development）的工程上下文模块：根据开发任务从 seekdb 中检索适用的规格、代码和测试，把上下文交给 MiniMax 完成一次真实的代码修改，用 PowerContext 保存任务状态并在模块重启后复用。

## 目录

```
minimax/
├── sddctx/                     # 上下文模块源码
│   ├── cli.py                  # CLI 入口
│   ├── chunker.py              # 规格/代码/测试分块
│   ├── db.py                   # seekdb 存储与 SQL 检索
│   ├── retrieve.py             # 任务驱动的资料选择
│   ├── contextpack.py          # 上下文包组装
│   ├── minimax_client.py       # MiniMax OpenAI 兼容接口
│   ├── patch.py                # 精确 search/replace 与 diff
│   ├── runner.py               # pytest 执行与日志
│   ├── state.py                # PowerContext 状态读写
│   └── powercontext_client.py  # PowerContext HTTP 客户端
├── workspace/
│   ├── taskboard_before/       # 改前快照（目标测试失败）
│   └── taskboard/              # 当前工作区（MiniMax 修改后）
├── tasks/add_min_priority.json # 验证样例任务
├── artifacts/
│   ├── minimax_edits.json      # MiniMax 返回并应用的编辑
│   ├── changes.diff            # 改前/改后统一 diff
│   └── file_manifest.json      # 文件哈希清单
└── docs/
    ├── verification_sample.md  # 验证样例说明
    ├── technical_note.md       # 一页技术说明
    ├── minimax_dev_record.md   # MiniMax 开发记录
    └── runs/                   # 运行日志、查询记录、状态记录
```

## 技术栈与版本

| 组件 | 版本/方式 |
| --- | --- |
| Python | 3.11+；实测 3.13.9（Windows 11） |
| seekdb | `oceanbase/seekdb:latest`，镜像 digest `sha256:4db4961869d554313520d6081fca29265ba6da6db7313311bd8acbed771a956b`，运行版本 `5.7.25-OceanBase seekdb-v1.4.0.0` |
| PowerContext | `powercontext[cli,server]==1.5.0`，本地 SQLite 后端 |
| MiniMax | OpenAI 兼容 `POST /v1/chat/completions`；默认模型 `MiniMax-M3`，可用 `MINIMAX_MODEL` 覆盖 |
| 测试 | pytest 9.2.0 |

> 以下命令默认在 `minimax/` 目录执行。如果从仓库根目录执行，把 `python -m sddctx` 换成 `python -m minimax.sddctx`。

## 一、准备环境

### 1. 启动 seekdb（Docker，推荐）

```bash
docker volume create seekdb-minimax-data
docker run -d --name seekdb-minimax \
  -p 2881:2881 -p 2886:2886 \
  -v seekdb-minimax-data:/var/lib/seekdb \
  oceanbase/seekdb:latest
```

默认连接信息：`127.0.0.1:2881`，用户 `root`，空密码，数据库 `test`。首次启动需要等待几十秒，直到 MySQL 协议就绪。可用下面的命令确认：

```bash
python -m sddctx db check
```

预期能看到 seekdb 版本和材料数量。若容器启动失败，先查看 `docker logs seekdb-minimax`；数据目录由 Docker volume 持久化。

### 2. 安装 Python 依赖

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# 或 .venv/bin/python -m pip install -r requirements.txt  # Linux/macOS
```

如果依赖解析较慢，可使用 `uv pip install -r requirements.txt`。嵌入式 seekdb 是可选路径，见 `requirements-embedded.txt`；Windows 下 `pylibseekdb` 运行时不可用，本作品默认使用 Docker server 模式。

### 3. 配置 MiniMax

```bash
cp .env.example .env
```

在 `.env` 中填写：

```dotenv
MINIMAX_API_KEY=<你的 MiniMax Token Plan API Key>
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MINIMAX_MODEL=MiniMax-M3
```

- 中国区 Key 使用 `https://api.minimaxi.com/v1`；国际区 Key 使用 `https://api.minimax.io/v1`。
- 如果 Token Plan 可用模型不是 `MiniMax-M3`，把 `MINIMAX_MODEL` 改成实际模型名（如 `MiniMax-M2.5`）。
- 若调用返回 `HTTP 402 insufficient_balance_error`，说明 Key 已通过认证但账户无可用额度；请改用比赛领取的 Token Plan 订阅 Key，或为按量付费账户充值。
- `.env` 已在 `.gitignore` 中，不会提交真实凭据。

### 4. 启动 PowerContext

```bash
python -m sddctx server start
```

该命令会在 `minimax/.powercontext` 下启动本地 SQLite Server，监听 `http://127.0.0.1:18080`，并等待健康检查通过。也可以手动在另一个终端运行：

```bash
POWERCONTEXT_HOME=./.powercontext \
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1 \
POWERCONTEXT_SERVER_HTTP_PORT=18080 \
POWERCONTEXT_SERVER_ACCESS_MODE=disabled \
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false \
powercontext server run
```

## 二、执行验证样例

### 真实 MiniMax 调用（推荐，生成提交证据）

```bash
# 从干净状态开始：恢复改前快照、清空 seekdb 材料、停掉本地 PowerContext 并清理旧日志
# （保留 artifacts/minimax_edits.json 供离线回放）
python -m sddctx reset --hard --clean-logs

python -m sddctx demo --live-minimax
```

`demo --live-minimax` 会依次完成：

1. 在改前快照上运行目标测试，确认失败原因是 `TypeError: ... unexpected keyword argument 'min_priority'`；运行回归测试，确认通过。
2. 把规格、代码、测试导入 seekdb；构造第一版上下文包。
3. 把任务状态写入 PowerContext；在**新进程**中读取状态并重新构造上下文包。
4. 把上下文交给 MiniMax，得到结构化 JSON 编辑；只允许修改 `src/taskboard/service.py`。
5. 应用编辑，运行目标测试、回归测试和全量测试。
6. 生成 `artifacts/changes.diff`，并把最终测试结果写回 PowerContext。
7. 临时给规格追加 `REQ-FILTER-005`，重新导入并构造上下文，确认新规格被采用；随后恢复原规格并重新导入。

### 离线回放（已有 MiniMax 证据时）

```bash
# reset 会保留 artifacts/minimax_edits.json，随后 demo 用它回放
python -m sddctx reset --hard --clean-logs
python -m sddctx demo
```

回放读取 `artifacts/minimax_edits.json`，不重新调用模型。它用于评审在无 Key 环境下复现代码修改和测试结果；MiniMax 的真实参与以 `docs/minimax_dev_record.md`、`docs/runs/minimax_request_*.json`、`docs/runs/minimax_response_*.json` 为准。

## 三、分步命令

```bash
# 导入规格/代码/测试到 seekdb
python -m sddctx ingest

# 构造上下文包
python -m sddctx build --task tasks/add_min_priority.json

# 查看 PowerContext 中的最新任务状态
python -m sddctx state show --task TASK-001 --json

# seekdb 检查
python -m sddctx db check

# PowerContext Server 状态
python -m sddctx server status

# 恢复改前快照并清空材料表
python -m sddctx reset

# 同时删除本地 PowerContext 数据和运行状态
python -m sddctx reset --hard

# 额外清理旧运行证据，便于重新生成一套干净材料
python -m sddctx reset --hard --clean-logs
```

## 四、预期结果

| 检查 | 预期 |
| --- | --- |
| 改前目标测试 | 7 failed，失败原因为 `min_priority` 未实现（`TypeError`） |
| 改前回归测试 | 4 passed |
| 改后目标测试 | 7 passed |
| 改后回归测试 | 4 passed |
| 改后全量测试 | 11 passed |
| 代码差异 | 仅 `src/taskboard/service.py` |
| 上下文来源 | `REQ-FILTER-001..004`、`REQ-COMPAT-001..003`、`TaskService.list_tasks`、5 个目标测试 |
| 规格更新检查 | 重新导入后上下文包含 `REQ-FILTER-005` |
| PowerContext | 模块重启后 `found=true`，状态被带入后续上下文 |
| seekdb | `docs/runs/db_queries.jsonl` 记录 SQL、参数、返回行数和 query_id |

## 五、证据文件

| 文件 | 内容 |
| --- | --- |
| `docs/runs/01_before_target_tests.log` | 改前目标测试失败日志（命令、cwd、退出码、stdout/stderr） |
| `docs/runs/02_before_regression_tests.log` | 改前回归测试通过日志 |
| `docs/runs/03_context_pack_empty_state.json` | 首次构造的上下文包 |
| `docs/runs/04_state_after_restart.log` | 新进程读取 PowerContext 状态 |
| `docs/runs/05_context_pack_resumed.json` | 重启后构造的上下文包（含状态） |
| `docs/runs/06_after_target_tests.log` | 改后目标测试通过日志 |
| `docs/runs/07_after_regression_tests.log` | 改后回归测试通过日志 |
| `docs/runs/08_after_all_tests.log` | 改后全量测试通过日志 |
| `docs/runs/09_spec_update_check.log` | 规格更新并重新导入后的上下文检查 |
| `docs/runs/db_queries.jsonl` | seekdb 查询记录（SQL、参数、行数、耗时） |
| `docs/runs/powercontext_state.jsonl` | PowerContext 写入与读取记录 |
| `docs/runs/minimax_request_*.json` | 发给 MiniMax 的任务与上下文（不含 API Key） |
| `docs/runs/minimax_response_*.json` | MiniMax 原始响应 |
| `docs/minimax_dev_record.md` | 人工可读的 MiniMax 开发记录 |
| `artifacts/changes.diff` | 改前/改后统一 diff |

## 六、已知限制

- 资料检索使用 seekdb 中的词法匹配（SQL `LIKE` + 权重排序）加任务必需来源锚点，没有引入额外的 embedding 模型。这样可以在没有额外推理依赖的情况下稳定复现；升级到向量/混合检索时，只需替换 `db.search_materials` 的查询方式。
- PowerContext 使用基础 Memory（显式写入/召回），没有配置生成与 embedding 模型，因此不做自动记忆抽取；任务状态由模块显式保存，符合题面对“状态复用”的要求。
- MiniMax 的实时调用需要参赛者自己的 Token Plan Key；仓库不包含凭据。没有 Key 时可用 `python -m sddctx demo` 回放已记录的编辑。
- Windows 下 PowerContext 官方支持为 experimental；本作品在 Windows 11 + Python 3.13 + Docker seekdb 上验证通过。Linux/macOS 更推荐。
- `workspace/taskboard` 是最终工作区，`workspace/taskboard_before` 是改前快照；`demo` 每次都会从改前快照重置，以保证可重复。

## 七、提交说明

- 所有材料位于 `minimax/`，基于 `oceanbase/powercontext` 的 `2026-competition-ecosystem` 分支开发。
- 不要提交 `.env`、`.venv/`、`.powercontext/`、`.runtime/`、`__pycache__/`。
- 提交前确认 `artifacts/minimax_edits.json`、`docs/minimax_dev_record.md` 和 `docs/runs/minimax_*.json` 来自同一次真实 MiniMax 调用，并与 `artifacts/changes.diff` 对应。
