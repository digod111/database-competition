# 清空 seekdb 运行数据后仍能召回（记忆归属）

## 目的

证明“盐罐换糖罐”的信息确实来自 PowerContext，而不是 seekdb 的事件/账本或写死的提示词。

## 操作

1. 在交付版默认 PowerContext（`.runtime/powercontext`）+ 已重置的 seekdb 上运行会话 A：
   - `我叫阿远，记住这个名字。`
   - `给我来一杯断肠烧，先赊着。`
   - `我把你吧台的盐罐换成糖罐了。`
2. **直接清空 seekdb 的运行状态**（保留 PowerContext 记忆）：

```sql
DELETE FROM events WHERE player_id='player_01';   -- 删 9 行
DELETE FROM pranks WHERE player_id='player_01';   -- 删 1 行
UPDATE players SET coins=30, debt=0, grudge=0, reputation=0
WHERE player_id='player_01';                      -- 回到 30/0/0/0
```

3. 新会话输入：

```text
上次我干了什么恶作剧？还记得吗？
```

## 实际输出

```text
阿远，你还有脸问。
把吧台的盐罐换成糖罐，害我卤味里撒错了料。
账本上可记着呢，戒心加二。

【铜板 30｜欠账 0｜戒心 0（平静）｜声望 0】
```

## 核对

- seekdb `pranks`、`events` 已无任何恶作剧/订单记录；`players` 欠账/戒心均为 0。
- PowerContext `memory/search` 本轮命中 1 条：

```text
阿远对阿九恶作剧：把吧台的盐罐换成糖罐。阿九戒心+2，当前戒心3。
```

- Dify `memory` 节点 HTTP 200、`hits=1`；`parse_context` 把该条作为“召回记忆”；`reply` 才生成
  “盐罐换糖罐”的回复。状态栏显示的是**当前** seekdb 状态（30/0/0）。
- 结论：恶作剧细节只能来自 PowerContext 的长期记忆；seekdb 只提供当前账本/库存。

## 原始日志

- `memory_only_sessionA_console.log`
- `memory_only_recall_console.log`
