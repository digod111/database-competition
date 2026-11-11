# DeepSeek 插件离线包

`deepseek-0.0.26.difypkg` 是从 Dify Marketplace 安装到本地 1.19.0 实例的官方插件包，用于 Marketplace 不可达时的离线安装。包内不含任何 API Key。

## 安装

1. 打开 Dify → **Plugins / 插件** → **安装插件** → **通过本地包安装**。
2. 选择 `deepseek-0.0.26.difypkg`。
3. 安装完成后进入 **Settings → Model Provider → DeepSeek**，填入你自己的 DeepSeek API Key。
4. 启用 `deepseek-flash` 和 `deepseek-v4-pro` 两个模型。

插件标识：

```text
langgenius/deepseek:0.0.26@bdf47cb41e9062bba00d26625d2a795c1874011d8ce64bff738c44b5a31787ea
```

DSL 的 `dependencies` 已声明同一插件；如果导入时提示缺少依赖，可先安装此离线包再重新导入。
