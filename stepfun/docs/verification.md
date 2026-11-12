# 验证样例（初始状态 → 输入 → 预期 → 实际）

所有用例在真实运行环境中执行（seekdb v1.4.0.0 + Chrome 157 / 内置 Chromium 浏览器），
截图证据见 `docs/evidence/`，界面状态快照见 `docs/evidence/verify-ui-state.json`。

## 用例 1：完整流程（导入 → 本地向量化 → 写入 → 混合检索 → 展示）

- 初始状态：seekdb 已初始化，集合 `verify-main` 为空；模型未加载；后端默认 WebGPU（已探测就绪）。
- 输入：点击「一键导入示例语料(200篇)」；查询 `HNSW 索引是如何组织向量并完成近邻搜索的？`，Top-K=5，混合模式。
- 预期：200 篇语料在浏览器内分块（201 块）并本地向量化后写入 seekdb；查询本地向量化后由 seekdb 一条 SQL 返回排序结果与原文；界面显示后端 webgpu、精度 q8 与各阶段耗时。
- 实际：
  - 导入日志：`导入完成: 200 文档 -> 201 块 | 向量化 13.64 s (14.7 块/s, webgpu) | 写入 seekdb 390.2 ms`（验证环境为 SwiftShader 软渲染，慢于真实 GPU 环境）；集合芯片 `verify-main (200 文档 / 201 块)`。
  - 检索：`查询向量化(本地, webgpu/q8): 129.1 ms | seekdb 检索(hybrid, 一条 SQL): 27.1 ms | 端到端: 161.7 ms`；Top3 含 HNSW 相关文档（t01-c2 相似度 0.567）并高亮展示原文。
  - 网络审计：页面全部请求均为 localhost（模型/页面/API），无任何外部域名。
  - 截图：`docs/evidence/verify-import-search.png`。

## 用例 2：增量导入 + 索引刷新后可检索 + 集合状态一致

- 初始状态：用例 1 完成，`verify-main` 含 200 文档/201 块。
- 输入：导入 2 个新文件 `增量验证-指南.txt`、`增量验证-笔记.md`（内容含「seekdb 增量导入」「两级 HNSW 索引」）；查询 `seekdb 增量导入 索引刷新`。
- 预期：写入后无需手动刷新即可检索到新文档；集合计数 202 文档/203 块。
- 实际：
  - 集合芯片 `verify-main (202 文档 / 203 块)`；SQL 复核：新文档已入库且 `MATCH('增量验证')` 命中。
  - 检索 Top1 = `增量验证-指南:0`（相似度 0.794）、Top2 = `增量验证-笔记:0`（0.757）——**新导入文档立即排第一、二位**。
  - 截图：`docs/evidence/verify-incremental.png`。

## 用例 3：WebGPU 不可用 → 自动降级 WASM + 界面提示

- 初始状态：系统 Chrome 157 以 `--disable-gpu --disable-software-rasterizer` 启动（`navigator.gpu` 存在但 adapter 创建失败——即“声明支持但实际不可用”的最坏情况）。
- 输入：默认配置（q8，优先 WebGPU）发起检索。
- 预期：预探测发现 adapter 不可用 → 跳过 WebGPU 直接以 WASM 加载 → 界面明确提示降级 → 检索正常完成。
- 实际：
  - 状态芯片：`推理后端: WebGPU 不可用，将用 WASM / 实际 wasm`。
  - 日志：`⚠ WebGPU 不可用，已自动降级到 WASM（界面提示生效）`、`模型加载完成: 实际后端 wasm`。
  - 检索完成：`查询向量化(本地, wasm/q8) ... seekdb 检索(hybrid, 一条 SQL): 184.0 ms`。
  - 截图：`docs/evidence/evidence-wasm-fallback.png`。

## 用例 4：质量评测（真实管线跑标注查询集）

- 初始状态：任意；配置 q8 + WebGPU，评测集合 `verify-eval`。
- 输入：点击「运行质量评测」（语料 200 篇、查询 40 条、K=10）。
- 预期：评测集合重建（reset→本地向量化→写入 seekdb），逐查询经混合/纯向量/精确三口径计算 Recall@K 与 nDCG@10，中位数汇总并落盘原始数据。
- 实际：`混合检索 R@1 1.000 · R@3 0.667 · R@5 0.450 · R@10 0.600 · nDCG@10 0.605；精确排序 R@5 0.400 · nDCG@10 0.576；原始数据已保存 data/eval-results/verify-eval-*.json`。
  - 截图：`docs/evidence/verify-eval.png`。

## 用例 5：可复现性（干净环境一键复现）

- 输入：按 README「快速启动」顺序执行 `npm run setup` → `npm run db:init` → `npm run models:download` → `npm run dev`。
- 实际：从空数据卷创建容器（seekdb-v1.4.0.0）、建库建表成功（`docs, kb_state`）；模型下载脚本在 HF 直连失败时自动回退 hf-mirror 成功（日志记录于开发记录）；语料生成确定性输出 200 篇（重复运行结果一致，seed=42）。
- 补充：`npm run db:reset` 已验证可从零重建全部数据结构。

## 验收自检清单（对应题面 3.1）

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 完整流程可跑（导入→向量化→写 seekdb→查询→混合检索→展示） | ✅ | 用例 1、截图 |
| 2 | 文档与查询 embedding 均浏览器本地计算，无云端推理调用 | ✅ | 网络审计全 localhost；权重本地加载 |
| 3 | INT8/FP16 量化有据（来源/方式/体积/压缩比/质量变化） | ✅ | README、docs/tech-note.md、docs/perf-report.md §1-2 |
| 4 | WebGPU 优先显式配置；WASM 自动降级且界面提示；两路径实测 | ✅ | 用例 3 + 主测矩阵（两后端 5 组配置） |
| 5 | 向量/原文/元数据在 seekdb；混合检索一条 SQL，无客户端拼合 | ✅ | `scripts/schema.sql`、README SQL、用例 1 |
| 6 | 增量导入 + 刷新后可检索；集合/会话状态正确 | ✅ | 用例 2（202/203 计数一致） |
| 7 | 性能报告：固定条件/分阶段/首次 vs 预热/中位数/优化前后 | ✅ | docs/perf-report.md + data/eval-results/ 原始数据 |
| 8 | 无真实凭据；未改其他赛道目录；未 commit/push；干净环境可复现 | ✅ | .gitignore、用例 5、git 操作由用户执行 |
