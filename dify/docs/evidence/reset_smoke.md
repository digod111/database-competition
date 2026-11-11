# 重置后首次进店（验证 reset 与“记忆真实存在”）

## 操作

1. `python scripts/powercontext_ctl.py reset`：停止 PowerContext，把旧 home 改名为 `.bak.<时间戳>`，用全新的 SQLite 启动。
2. `python data/init_db.py`：重建 `dify_app`，恢复酒单/谣言，玩家 `player_01` 回到 30 铜板、0 欠账、0 戒心。
3. 在 Dify Web App 新建对话，`player_01`，输入：
   `老板，还认得我吗？上次的账和恶作剧还记得吗？`

## 实际输出

```
擦着杯子，抬眼看你一眼。
客官面生，头回来就装熟？
账本上没你的名，恶作剧更没记。
要喝酒坐下，不喝别挡门。

【铜板 30｜欠账 0｜戒心 0（平静）｜声望 0】
```

## 结论

- 旧 scope/旧记忆被完全替换，同一个问题不再召回“阿远/盐罐/欠账”。
- 对比 `answers.md` 中重置前的输出（阿远、盐罐换糖、欠 12 铜板），可确认跨会话回忆来自 PowerContext 记忆，而不是写死的提示词或 Dify 会话历史。
- 原始控制台日志：`reset_smoke_console.log`。
