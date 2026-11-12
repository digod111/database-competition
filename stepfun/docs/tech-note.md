# 一页技术说明：端侧 AI 推理性能优化与 seekdb 混合检索

## 1. 模型与量化

- **模型**：`Xenova/all-MiniLM-L6-v2`（`sentence-transformers/all-MiniLM-L6-v2` 的官方 ONNX 导出，Apache-2.0），384 维，mean-pooling + L2 归一化。来源与版本在 `scripts/download_models.mjs` 中固定（repo `Xenova/all-MiniLM-L6-v2`，`resolve/main` 分支）。
- **量化方案**：采用官方 **INT8（q8 动态量化）** 权重为主方案，同时实测 FP16 与 FP32 基线，三套权重来自同一官方仓库，无需自量化（获取脚本含 HF→hf-mirror 自动回退）。

| 精度 | 文件 | 实测体积 | 压缩比 |
|---|---|---|---|
| FP32 | onnx/model.onnx | 90,387,606 B | 1.00x |
| FP16 | onnx/model_fp16.onnx | 45,297,825 B | 2.00x |
| INT8(q8) | onnx/model_quantized.onnx | 22,972,370 B | 3.93x |

- **质量变化**（200 篇语料、40 条标注查询、同一评测口径）：精确排序口径下 fp32 与 q8 的 Recall@5 均为 0.400、nDCG@10 为 0.538 vs 0.576/0.530（|Δ|≤0.04）；混合/ANN 口径 Recall@K 完全一致。**无显著质量下降**，量化收益为体积 3.93x、WASM 端到端提速约 1.4x。

## 2. 端侧运行与降级

- **运行时**：Transformers.js（`@huggingface/transformers` 4.3.2，底层 onnxruntime-web 1.31），feature-extraction pipeline，`device`/`dtype` 均显式配置。
- **WebGPU 优先**：加载前先探测 `navigator.gpu.requestAdapter()`（3s 超时；`navigator.gpu` 存在但 adapter 为 null 时 ORT WebGPU 初始化会挂起而非抛错，因此必须显式预探测），探测通过才以 `device:'webgpu'` 加载；pipeline 初始化抛错同样进入降级。
- **WASM 自动降级**：任一步失败即改以 `device:'wasm'` 重载，界面顶栏常驻「推理后端」状态芯片与日志明确提示（“⚠ WebGPU 不可用，已自动降级到 WASM”）。
- **验证**：真实 Chrome 157 两条路径均实测通过（WebGPU 正常环境 + `--disable-gpu` 无 GPU 环境自动降级），截图见 docs/evidence/。
- 本地推理证据：页面网络请求审计全为 localhost（模型权重/页面/API），无任何外部域名调用。

## 3. seekdb 使用

- **接入**：官方 seekdb-js（npm `seekdb` 1.4.0）server 模式连接本地 Docker seekdb（127.0.0.1:2881，`stepfun_app` 库）。浏览器无法直连 MySQL 协议，故设极薄 Node 转发层（`server/`）：只做参数化 SQL 转发与静态资源服务，**不含任何检索/排序/向量逻辑**。
- **表与索引**（`scripts/schema.sql`）：`docs(kb, id, doc_name, chunk_idx, content TEXT, embedding VECTOR(384))`，主键 `(kb,id)`，`FULLTEXT INDEX WITH PARSER ik`（中文 IK 分词）+ `VECTOR INDEX (DISTANCE=cosine, TYPE=hnsw, LIB=vsag)`。
- **混合检索**：一条 SQL 完成全文过滤 + 向量 ANN 排序（见 README），参数化防注入；`ORDER BY dist APPROXIMATE` 走 HNSW。纯向量模式与混合模式可切换对照。
- **增量写入与刷新**：批量（64 行/批）`INSERT ... ON DUPLICATE KEY UPDATE`；实测 seekdb 两级 HNSW 使新写入立即可检索（写入后立即查询命中，无需显式刷新）。集合状态存 `kb_state` 表，连续导入/切换集合后计数与检索结果一致（实测 200→202 篇）。

## 4. 状态与优化

- **状态管理**：`kb` 集合隔离（`main` 主库 / `eval-*` 评测 / `perf-*` 性能），界面显示当前集合、文档数、块数、后端、精度；模型按「精度×后端」懒加载并缓存，切换配置自动重载。
- **优化点与实测**（同机同语料固定条件，详见 docs/perf-report.md 原始数据）：
  1. **写入批量化**：逐条 114.7 块/s → 64 条/批 1002.3 块/s（**8.7x**）。
  2. **模型缓存**：首次加载 10.97s（含 23.7MB 本地下载）→ 预热后 1.63s（**6.7x**；另测无头 Chrome：16.13s → 1.21s，13.4x）。
  3. **embedding 批处理**：WebGPU q8 下 batch 1→16 吞吐 4.6→6.4 篇/s（+39%）；WASM 下无显著差异（瓶颈在计算不在调度）。
  4. **后端/精度选择**（设备相关）：本机 AMD gcn-5 无 INT8 硬件加速，fp32/WebGPU 查询向量化 14.5ms 为最快配置（q8/WebGPU 142.7ms）；WASM 上 q8(44.6ms) 优于 fp32。
- **性能对比结论**：INT8(q8)+WASM 为“体积最小 + 兼容性最好”组合（端到端 69ms，体积 21.9MB）；FP32+WebGPU 在本机延迟最低（端到端 50ms）但体积 4x；推荐默认 q8+WebGPU（自动降级 WASM），设备不支持 INT8 加速时切 FP32 可显著提速。
