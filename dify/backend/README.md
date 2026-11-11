# 忘忧酒馆数据服务

FastAPI + PyMySQL，向 Dify Chatflow 提供 seekdb 中的玩家账本、酒单、恶作剧、事件流水和谣言全文检索。

## 启动

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt          # 仅后端
# 完整复现（后端 + PowerContext）：pip install -r ../requirements.txt
cp .env.example .env
# 按需修改 SEEKDB_* 与 BACKEND_PORT（默认 8010）
bash start.sh          # Linux/macOS
# 或 PowerShell: .\start.ps1
```

健康检查：`curl http://127.0.0.1:8010/health`

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/state` | 玩家状态、酒单、最近事件、状态栏 |
| POST | `/api/order` | 点酒（现金/赊账），扣库存、记账 |
| POST | `/api/gossip`、`/api/rumors/search` | seekdb 谣言全文检索 |
| POST | `/api/prank` | 恶作剧，提升戒心并记录 |
| POST | `/api/settle` | 还债 |
| POST | `/api/profile` | 更新昵称 |
| POST | `/api/chat` | 记录闲聊并返回当前状态 |
| GET | `/api/events` | 查询事件流水（证据用） |

所有业务失败返回 HTTP 200 + `{"ok": false, "error": {...}}`，方便 Dify 用条件分支处理。
