# 证据索引

所有记录来自 2026-11-10 的真实运行；不含真实 API Key/密码。

| 文件 | 证明什么 |
| --- | --- |
| `answers.md` | 四轮真实输出（报名/赊酒/恶作剧/新会话召回），含状态栏 |
| `seekdb_state.txt` | `dify_app` 实际行：players（阿远/30 铜板/欠 12/戒心 3）、pranks、drinks、events |
| `powercontext_memory.txt` | scope `忘忧酒馆 / player_01`；FTS 命中赊账与盐罐恶作剧两条记忆 |
| `dify_node_evidence.txt` | 每轮 scope/memory/search/工具/remember 的 HTTP 状态码；第 4 轮 memory_query、记忆命中、最终 answer |
| `chat_ui_snapshot.txt` | Dify 公开 Web App 的实际页面快照（阿九翻旧账 + 状态栏） |
| `seekdb_fulltext.txt` | IK 全文索引 DDL 与 `MATCH ... AGAINST` 实测结果 |
| `powercontext_restart.txt` | PowerContext 进程重启后仍能检索旧记忆（M2 阶段） |
| `reset_smoke.md` | 重置后同一问题变“客官面生”，证明记忆真实 |
| `memory_only_recall.md` | 清空 seekdb 的 events/pranks/账本后，阿九仍能说出盐罐恶作剧——证明记忆来自 PowerContext |
| `memory_only_sessionA_console.log`、`memory_only_recall_console.log` | 上述隔离测试的原始输出 |
| `import_test.md` | 导出的 `.ifpkg` 重新导入并跑通 |
| `dify_setup.txt` | Dify 1.19.0 / 插件 0.0.26 / 两个模型 / SSRF 放行 / 容器访问宿主机记录 |
| `final_demo_console.log` | 最终四轮 demo 的控制台原始输出 |
| `import_test_console.log` | 导入包测试原始输出 |
| `reset_smoke_console.log` | 重置对照原始输出 |

说明：Qoder 内置浏览器当前没有可见 surface（0x0），无法导出 PNG 截图；因此 Web UI 以可访问性
快照文本 + Dify 节点/HTTP 日志作为证据。评审按 `deploy/README.md` 与
`docs/verification_sample.md` 可自行复现并截图。
