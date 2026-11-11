# 导出包重新导入验证

## 操作

1. 从 Dify 导出最终应用：`GET /console/api/apps/4594c150-14c8-46a6-8bc7-fe9dfa02b573/export?include_secret=false`，得到 `tavern-chatflow.ifpkg`（本次 7.4 KB）。
2. 用 multipart 重新导入为新应用：
   `POST /console/api/apps/imports`，`file=tavern-chatflow.ifpkg`。
3. 返回：

```json
{"status":"completed","app_id":"3ac2dfb8-fd7b-4363-88bb-ea4b12470bb5","app_mode":"advanced-chat","current_dsl_version":"0.7.0","imported_dsl_version":"0.7.0","error":"","warnings":[]}
```

4. 对新应用执行 draft run，新玩家 `import_test`，输入 `来一杯忘忧酿`。

## 实际输出

```
阿九把忘忧酿推到你手边，杯底星屑沉着一线光。
“新客？第一杯算你识货。”
他靠回柜边，眼神往雨里一瞥：“苦在头，甜在后。别急着咽。”

【铜板 22｜欠账 0｜戒心 0（平静）｜声望 1】
```

节点全部 `succeeded`（start/clean_input/scope/parse_scope/state/parse_state/classify/parse_intent/memory/parse_context/branch/act_order/aggregate/parse_action/reply/clean_reply/remember/answer）。

## 结论

- 导出的 `.ifpkg` 可独立导入并直接运行；模型/插件依赖在同一 Dify 工作区配置好后即可复现。
- 新应用的 Web App 短码会重新生成，评审按 `deploy/README.md` 第 4 节获取即可。
- 原始日志：`import_test_console.log`。
