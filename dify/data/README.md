# 数据与初始化

## 表

| 表 | 说明 |
| --- | --- |
| `drinks` | 酒单/价格/库存/效果；`hidden=1` 的狐尾酒在低戒心+高声望时可见 |
| `players` | 玩家账本：昵称、铜板、欠账、戒心、声望、到访次数、最近 session |
| `events` | 事件流水（enter/order/prank/chat/rumor/profile/settle） |
| `pranks` | 恶作剧台账（描述 + 戒心增量） |
| `rumors` | 谣言库，`FULLTEXT INDEX ft_rumors (title, content, tags) WITH PARSER ik` |

## 初始化 / 重置

```bash
# 读取 backend/.env 的 SEEKDB_*，创建 dify_app、重建表、写入种子
python data/init_db.py

# 语义相同，打印更多提示
python data/reset_db.py
```

- 会 **DROP** `dify_app` 中的业务表，不影响同实例的 `test` 或其他库。
- 种子：5 种酒、10 条谣言、玩家 `player_01`（30 铜板）。
- 重置后建议同时执行 `python scripts/reset_demo.py`（它会重置 PowerContext 记忆 + seekdb）。

## 核对

```bash
# 在 seekdb 容器或任意 MySQL 客户端执行
mysql -h127.0.0.1 -P2881 -uroot < data/verify.sql
```

或直接查看 `docs/evidence/seekdb_state.txt` 的实际结果。
