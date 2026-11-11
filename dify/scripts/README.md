# 脚本

## powercontext_ctl.py

```bash
python scripts/powercontext_ctl.py start      # 启动（默认 18082，home=.runtime/powercontext）
python scripts/powercontext_ctl.py stop
python scripts/powercontext_ctl.py status
python scripts/powercontext_ctl.py reset      # 停止 → 旧 home 改名 .bak.<时间戳> → 全新启动
```

- 默认 token `tavern-demo-token`；与 DSL 的三个 PowerContext 节点头一致。
- `--home/--port/--token/--log` 均可覆盖。
- Windows 用 `taskkill /T` 杀进程树；Linux 用进程组。

## reset_demo.py

```bash
python scripts/reset_demo.py
```

1. `powercontext_ctl.py reset`：清空 PowerContext 记忆（替换 home）；
2. `data/init_db.py`：重建 seekdb `dify_app` 并恢复种子；
3. 检查后端 `http://127.0.0.1:8010/health`（未启动会警告，不影响前两步）。

之后在 Dify 中**新建对话**，`player_id` 默认 `player_01`，即回到初始状态。
