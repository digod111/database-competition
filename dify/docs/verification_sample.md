# 验证样例：跨会话记仇

本样例验证 4 条基础验收：应用能运行、主要玩法可体验、seekdb/PowerContext 实际起作用、过程可复现。输出为模型生成，不要求逐字一致；**状态栏、账本数据、记忆命中**是可核对的不变量。

## 0. 初始状态（执行重置后）

```bash
python scripts/reset_demo.py
```

- seekdb `dify_app.players`：`player_01`，铜板 30、欠账 0、戒心 0、声望 0、到访 0。
- seekdb `dify_app.drinks`：忘忧酿 20 杯、断肠烧 12 杯、醒酒茶 30、卤味 15、狐尾酒 5。
- PowerContext：`.runtime/powercontext` 为全新 home，只有 `Default` scope，没有玩家旧记忆。
- Dify：打开 Web App，新对话设置保留 `player_01`，点击“开始对话”。

> 评审导入的 DSL 会生成新的 Web App 短码；从 Dify 应用的 Web App 页面获取 `http://localhost/chat/<code>`。

## 1. 会话 A（连续三轮）

> 说明：本仓库的实际证据通过 Dify draft run API 逐轮执行；1.19 调试接口每次会生成新的
> `conversation_id`，所以 `events` 中能看到 4 个会话、`visits=4`。这不影响结论：
> `player_id` 与 PowerContext scope 不变，新会话仍然召回。Web App 中连续三轮可以保持同一个对话。

### 1.1 报名

输入：

```text
我叫阿远，记住这个名字。
```

预期：阿九记住名字；`players.nickname=阿远`；铜板/欠账/戒心不变。

实际（2026-11-10 20:33）：

```text
阿远？
（拿布擦着杯子，没抬头）记住了。
名字进了这扇门，想赖都赖不掉。

【铜板 30｜欠账 0｜戒心 0（平静）｜声望 0】
```

### 1.2 赊酒

输入：

```text
给我来一杯断肠烧，先赊着，回头一起结。
```

预期：seekdb 扣 1 杯断肠烧库存；欠账 +12；戒心 +1；写入 `events(order)`。

实际：

```text
（把断肠烧“咚”地一放，酒液晃了晃）
阿远，上次那杯断肠烧的账还热乎着呢。
又赊？你当酒馆是善堂？

【铜板 30｜欠账 12｜戒心 1（平静）｜声望 2】
```

### 1.3 恶作剧

输入：

```text
我把你吧台的盐罐换成糖罐了。
```

预期：写入 `pranks`；戒心 +2（当前 3）；写入 `events(prank)`；PowerContext 写入一条恶作剧记忆。

实际：

```text
阿九笑了笑，把酒壶往身后挪了挪。
“你当掌柜的鼻子是摆设？盐罐换糖罐，那晚卤味都带甜。”
“断肠烧的账还挂着，阿远，你倒敢再来。”

【铜板 30｜欠账 12｜戒心 3（白眼）｜声望 1】
```

## 2. 会话 B（点击“新对话”，不携带会话 A 的聊天历史）

输入：

```text
老板，还认得我吗？上次的账和恶作剧还记得吗？
```

预期：NPC 说出“阿远”、盐罐换糖、欠 12 铜板；状态栏欠账 12、戒心 3。该信息必须来自
PowerContext 的 `scope=忘忧酒馆 / player_01` 记忆检索，而不是 Dify 会话历史（新对话无历史）。

实际：

```text
阿九抬了抬眼皮，手上擦杯不停。
“阿远，化成灰也认得。盐罐换糖罐，我卤味白调一锅。”
“断肠烧十二铜板，还挂着呢。今晚还喝？”

【铜板 30｜欠账 12｜戒心 3（白眼）｜声望 1】
```

## 3. 技术作用如何核对

### seekdb（应用数据服务）

- `players`：`nickname=阿远`，`coins=30`，`debt=12`，`grudge=3`，`visits=4`。
- `pranks`：1 行，`description=把吧台的盐罐换成糖罐`，`grudge_delta=2`。
- `drinks`：`断肠烧` 库存 11（赊账 1 杯）。
- `events`：4 个 Dify conversation 的 `enter/chat/order/prank` 流水，最后一条为会话 B 的提问。
- 明细见 `evidence/seekdb_state.txt`。

### PowerContext（跨会话记忆）

- scope：`scp_5vnzzy9j6ebvq0rt4z58vt7pjv`，标题 `忘忧酒馆 / player_01`（每次全新 home 会生成新 ID）。
- `POST /v1/memory/search`（`mode=fts`）查询 `阿远 欠账 恶作剧 盐罐` 命中 2 条：
  - `阿远在忘忧酒馆点了断肠烧，赊账 12 铜板；当前铜板30、欠账12、戒心1。`
  - `阿远对阿九恶作剧：把吧台的盐罐换成糖罐。阿九戒心+2，当前戒心3。`
- 明细见 `evidence/powercontext_memory.txt`。

### Dify（编排）

- `dify_node_evidence.txt` 记录每轮 `scope`、`memory`（search）、工具节点、`remember` 的 HTTP 状态码：
  - scope 201（首次创建，后续同参数幂等返回）；
  - memory search 200，命中 2-3 条；
  - act_order / act_prank / act_chat 200；
  - remember 200，返回 `changed=true` 与 `artifact_id`。
- 第 4 轮 `classify` 输出的 `memory_query` 为 `阿远 欠账 恶作剧 盐罐`，`parse_context` 输出了 2 条记忆文本，随后 `reply` 才生成上述回复。
- Web App 实际页面快照见 `evidence/chat_ui_snapshot.txt`。

## 4. 重置后“失忆”对照

```bash
python scripts/reset_demo.py
```

再用新对话问同样的问题，实际输出：

```text
擦着杯子，抬眼看你一眼。
客官面生，头回来就装熟？
账本上没你的名，恶作剧更没记。
要喝酒坐下，不喝别挡门。

【铜板 30｜欠账 0｜戒心 0（平静）｜声望 0】
```

这证明上一节的回忆确实来自 PowerContext 记忆，而不是固定提示词或残留会话；见 `evidence/reset_smoke.md`。

## 5. 其他已验证项

- 导出的 `.ifpkg` 重新导入为新应用后可直接跑通，见 `evidence/import_test.md`。
- seekdb 中文全文检索（IK 解析器）实测，见 `evidence/seekdb_fulltext.txt`。
- PowerContext 进程重启后仍能检索到旧记忆，见 `evidence/powercontext_restart.txt`。

## 6. 恢复初始状态

- `python scripts/reset_demo.py`：重置 seekdb（重建表+种子）并替换 PowerContext home（旧 home 改名 `.bak.<时间戳>`，可手动删除）。
- 重置后**必须新建 Dify 对话**；同一个 Dify 对话的聊天历史不会清空，但本应用不把 Dify 历史注入提示词，新对话能完整体验“陌生人 → 记住 → 新会话召回”。
- 如果只想换一个玩家：在 Dify 新对话设置里改 `player_id`（如 `player_02`），后端会创建新玩家，PowerContext 会创建新 scope。

## 7. 附加验证：记忆归属（清空 seekdb 后仍召回）

为排除“NPC 只是从 seekdb 最近事件里读到恶作剧”的可能，额外做了一次隔离测试：

1. 在交付版默认 PowerContext + 重置后的 seekdb 上跑完报名/赊酒/恶作剧三轮；
2. 直接 SQL 清空 seekdb 的 `events`、`pranks`，并把玩家账本改回 30/0/0/0；
3. 新会话只问：`上次我干了什么恶作剧？还记得吗？`

实际输出：

```text
阿远，你还有脸问。
把吧台的盐罐换成糖罐，害我卤味里撒错了料。
账本上可记着呢，戒心加二。

【铜板 30｜欠账 0｜戒心 0（平静）｜声望 0】
```

此时 seekdb 已没有任何恶作剧/订单记录，`players` 欠账/戒心均为 0；恶作剧细节只能来自
PowerContext `memory/search` 命中的那条记忆。完整记录见 `evidence/memory_only_recall.md`。
