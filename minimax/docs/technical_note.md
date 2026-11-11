# 一页技术说明

## 1. 目标与结论

本作品实现一个 SDD 工程上下文模块：给定一个开发任务，模块从 seekdb 中检索适用的规格、代码和测试，结合 PowerContext 中保存的任务进展，形成上下文包；再把上下文交给 MiniMax 生成最小代码修改，并用目标测试与回归测试验证。示例任务是在 `TaskService.list_tasks` 中增加 `min_priority` 筛选。

验证结果：改前目标测试 7 个因 `min_priority` 未实现而失败（`TypeError`），回归测试 4 个通过；MiniMax 修改后目标测试 7 个、回归测试 4 个、全量测试 11 个全部通过；代码差异只涉及 `src/taskboard/service.py`。

## 2. 模块结构

```
任务 JSON + 工程目录
        │
        ├─ chunker.py ── 规格按 REQ 切分；Python 用 AST 按符号切分；测试按函数切分
        │
        ├─ ingest.py ─── 写入 seekdb.sdd_materials（kind/path/symbol/content/keywords）
        │
        ├─ retrieve.py ─ 任务词项 + 必需来源锚点 → seekdb SQL 检索 → 规格/代码/测试
        │
        ├─ state.py ──── PowerContext：SDD_STATE 任务状态（显式 Memory + PreparedContext）
        │
        ├─ contextpack.py ─ 组装上下文包（来源、状态、查询 ID、Scope）
        │
        ├─ minimax_client.py ─ MiniMax /chat/completions → 结构化 JSON 编辑
        │
        ├─ patch.py ──── 精确 search/replace、改前/改后 diff、文件哈希
        └─ runner.py ─── pytest 执行与日志（命令、cwd、退出码、stdout/stderr）
```

CLI 入口为 `python -m sddctx`，提供 `ingest`、`build`、`state`、`demo`、`db`、`server`、`reset` 子命令。每次 `build` 输出 `docs/runs/context_pack*.json`，并在 `docs/runs/db_queries.jsonl` 记录实际 SQL、参数、返回行数与耗时。

## 3. 上下文选择方式

1. **分块**：Markdown 规格按 `REQ-*` 条目切分，保留所属小节；Python 代码用 `ast` 提取模块、类、方法，符号形如 `TaskService.list_tasks`；测试提取文件头（imports/helper）和每个 `test_*` 函数。
2. **导入**：分块写入 seekdb 的 `sdd_materials` 表，字段包括 `kind`、`path`、`symbol`、`content`、`keywords`、`content_hash`。重新导入用 `REPLACE INTO` 覆盖，保证规格更新后不会返回旧内容。
3. **检索**：任务标题、描述、验收项、必须保持项、测试名和代码符号经中英文词项扩展后，按 kind 分别执行 seekdb SQL；排序权重为 path/symbol 命中 > keywords 命中 > content 命中。任务显式列出的 `REQ-*`、代码符号和测试名作为必需锚点，从 seekdb 精确取回；缺失则直接报错，避免编造来源。
4. **状态**：PowerContext 中保存 `SDD_STATE {json}` 形式的 Memory，包含已完成、待办、验证结果、上下文包 ID、变更文件。后续 `build` 先 `search_memory` 再 `prepare_context`，把有界历史状态放入上下文包；模块重启后通过新进程读取同一 Scope。

## 4. 三项技术的分工

| 技术 | 实际承担的作用 | 可核对证据 |
| --- | --- | --- |
| MiniMax | 读取任务+规格+代码+测试上下文，生成 `service.py` 的最小修改；真实 HTTP 调用 `/chat/completions` | `docs/runs/minimax_request_*.json`、`minimax_response_*.json`、`docs/minimax_dev_record.md`、`artifacts/minimax_edits.json` |
| seekdb | 保存规格/代码/测试分块与索引；每次上下文构造实际执行 SQL 检索 | `docs/runs/db_queries.jsonl`、`docs/runs/context_pack*.json` 中的 `database.queries` |
| PowerContext | 保存任务进展与验证结果；模块重启后读取并用于后续上下文 | `docs/runs/powercontext_state.jsonl`、`docs/runs/04_state_after_restart.log`、`05_context_pack_resumed.json` |

## 5. 验证结果

| 检查 | 结果 |
| --- | --- |
| 改前目标测试 | 7 failed；原因是 `TypeError: ... unexpected keyword argument 'min_priority'` |
| 改前回归测试 | 4 passed |
| 改后目标测试 | 7 passed |
| 改后回归测试 | 4 passed |
| 改后全量测试 | 11 passed |
| 代码差异 | 仅 `src/taskboard/service.py` |
| 规格更新检查 | 重新导入后上下文包含 `REQ-FILTER-005` |
| 状态复用 | 新进程读取到 `context_built` 状态，`status.completed` 进入后续上下文 |

## 6. 取舍与失败尝试

- **seekdb 嵌入式 vs Docker**：最初尝试 `pylibseekdb` 的 Windows 轮子，`pylibseekdb.open` 返回 `seekdb_open failed: code=-1`，且官方要求只列 Linux/macOS。改为 Docker `oceanbase/seekdb:latest` server 模式，连接 `127.0.0.1:2881`，稳定可用。代码仍保留 `SEEKDB_MODE=embedded` 分支，Linux/macOS 可选用。
- **词法检索 vs 向量检索**：本题没有强制向量检索；为了减少额外 embedding 依赖并保证可复现，使用 seekdb SQL 词法匹配 + 必需锚点。检索结果、SQL 和行数都有日志。若升级为向量/混合检索，只需替换 `db.search_materials`，上下文包和状态层不需要改。
- **基础 Memory vs Full memory**：PowerContext 未配置生成/embedding 模型，使用显式 Memory 保存状态，避免模型抽取的不确定性；`prepare_context` 仍能返回有界历史上下文。题面只要求状态可保存、重启可读取并用于后续处理，这一档位满足要求。
- **回放 vs 实时调用**：提交材料保留一次真实 MiniMax 调用的请求/响应与开发记录；`demo` 默认回放同一份编辑，便于评审在无 Key 环境下复现测试结果。回放不会伪称是新的模型调用，记录中 `mode=replay` 会明确标注。
- **精确编辑**：要求 MiniMax 返回 `search/replace`，并在应用前校验 `search` 在文件中恰好出现一次；校验失败直接报错，不猜测、不静默改写。这样保证 MiniMax 响应与最终 diff 可对应。

## 7. 限制

- 示例工程是小型 Python 项目；模块的分块和检索策略面向中小型仓库，尚未针对超大仓库做索引/性能优化。
- 词法检索对同义词覆盖有限，当前通过任务词项扩展和必需锚点补足；复杂自然语言任务可能需要更强的语义检索。
- PowerContext 与 Windows 的组合官方标注为 experimental；本作品在 Windows 11 + Python 3.13 + Docker 上验证通过，Linux 更推荐。
- MiniMax 的模型名、区域端点由 `.env` 配置；不同 Token Plan 可用模型可能不同，运行说明中已给出覆盖方式。
