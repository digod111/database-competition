# stepfun — 端侧迷你知识库（seekdb × 阶跃星辰赛道）

浏览器本地完成文档与查询向量化（Transformers.js，WebGPU 优先 / WASM 自动降级），
由 **seekdb 一条 SQL 完成向量 + 全文混合检索** 的迷你知识库应用。

- 完整流程：导入文本文档 → 浏览器本地 embedding → 写入 seekdb → 查询本地向量化 → seekdb 混合检索 → 展示排序结果与原文。
- 推理全部在用户设备浏览器内完成，**无任何云端推理 API 调用**；模型权重从本地服务加载。
- 模型：`Xenova/all-MiniLM-L6-v2`（官方 ONNX 导出，含 fp32 / fp16 / INT8-q8 三套官方量化权重）。
- 数据库：seekdb（Docker 本地 server，127.0.0.1:2881，库 `stepfun_app`），通过官方 **seekdb-js** SDK（npm 包 `seekdb`）接入。

## 目录结构

```
stepfun/
├── app/                  # 前端（Vite + TypeScript + Transformers.js）
│   └── src/{main,embed,chunk,eval,perfbench,perf,api}.ts
├── server/               # 极薄转发层（Express + seekdb-js，无检索逻辑，仅转发）
├── scripts/
│   ├── init_seekdb.sh    # 启动 seekdb 容器 + 建库建表（幂等）
│   ├── reset_seekdb.sh   # 删除数据卷并重新初始化
│   ├── schema.sql        # 表结构：VECTOR(384) + HNSW(vsag, cosine) + FULLTEXT(IK)
│   ├── gen_corpus.py     # 确定性生成 200 篇中英混合语料 + 40 条标注查询
│   ├── topics.json       # 语料话题数据（40 个话题，中英双语事实）
│   └── download_models.mjs  # 下载 fp32/fp16/q8 官方权重（HF 失败自动回退 hf-mirror）
├── data/
│   ├── corpus/           # 200 篇语料（.txt，由 gen_corpus.py 生成，已提交）
│   ├── corpus.json       # 语料元数据
│   ├── queries.json      # 40 条标注查询（评测 Recall@K / nDCG@K 用）
│   └── eval-results/     # 评测与性能原始数据（真实运行落盘，已提交）
├── docs/                 # 技术说明 / 性能报告 / 验证样例 / 开发记录 / 证据截图
└── models/               # 模型权重（gitignored，由 download_models.mjs 下载）
```

## 版本

| 组件 | 版本 |
|---|---|
| Node.js | ≥ 20（实测 v24.21.0） |
| 浏览器 | Chrome / Edge 最新版（实测 Chrome 157） |
| Transformers.js (`@huggingface/transformers`) | 4.3.2（onnxruntime-web 1.31） |
| seekdb | `oceanbase/seekdb:latest`（实测 5.7.25-OceanBase seekdb-v1.4.0.0） |
| seekdb-js（npm `seekdb`） | 1.4.0（server 模式，mysql2 3.x） |
| 模型 | Xenova/all-MiniLM-L6-v2（fp32 / fp16 / q8 官方权重，384 维） |

## 快速启动（从干净环境复现）

前置：Docker（引擎可用）、Node.js ≥ 20、Python 3（生成语料用，语料已提交可跳过）。

```bash
cd stepfun

# 0) 安装依赖（npm 缓存可自定；seekdb 包因上游 peerDeps 打包缺陷需 --legacy-peer-deps）
npm run setup

# 1) 启动 seekdb（Docker，端口 2881；已存在则直接复用）
npm run db:init            # 重置用: npm run db:reset

# 2) 下载模型权重（HF 直连失败自动回退 hf-mirror.com；约 153 MB，缓存于 stepfun/models/）
npm run models:download

# 3) 生成语料（可复现：固定 seed；data/ 下已有生成结果，可跳过）
npm run corpus:gen

# 4) 启动（转发层 8080 + 前端开发服务器 5173）
npm run dev
```

浏览器打开 http://localhost:5173 （生产模式：`npm run build` 后仅启动 `npm run server`，访问 http://localhost:8080 ）。

**浏览器要求**：Chrome/Edge 最新版。WebGPU 可用时自动使用（界面提示），不可用时自动降级 WASM（界面明确提示）。

## 使用流程

1. **导入**：右侧「一键导入示例语料(200篇)」或选择本地 .txt/.md 文件；文档在浏览器内分块（~500 字符）并本地向量化，批处理（batch=16）后写入 seekdb，进度条与耗时实时显示。
2. **检索**：输入查询 → 浏览器本地向量化 → seekdb 一条 SQL 完成 `MATCH 全文过滤 + cosine_distance ANN 排序` → 展示排序结果、原文与关键词高亮，显示各阶段耗时。
3. **评测**：「运行质量评测」在独立集合中导入语料并跑 40 条标注查询，输出 Recall@K / nDCG@K（混合检索、纯向量 ANN、精确排序三种口径），原始数据自动落盘 `data/eval-results/`。
4. **性能**：「运行性能测试」固定语料与查询重复 5 次，报告首次 vs 预热中位数，支持 batch=1/16/64 对照。

## 量化方案（模型来源与压缩比）

模型 `Xenova/all-MiniLM-L6-v2`（`sentence-transformers/all-MiniLM-L6-v2` 的官方 ONNX 导出），
同一仓库提供官方预量化权重，**无需自量化**：

| 精度 | 文件 | 实测体积 | 压缩比（vs fp32） |
|---|---|---|---|
| FP32（基线） | `onnx/model.onnx` | 90,387,606 B (86.2 MiB) | 1.00x |
| FP16 | `onnx/model_fp16.onnx` | 45,297,825 B (43.2 MiB) | 2.00x |
| INT8（q8，动态量化） | `onnx/model_quantized.onnx` | 22,972,370 B (21.9 MiB) | **3.93x** |

检索质量对比（同一 200 篇语料 + 40 条标注查询，详见 [docs/perf-report.md](docs/perf-report.md)）：
**量化前后 Recall@K 无变化；nDCG@10 变化 |Δ| ≤ 0.04，在 HNSW 近似噪声范围内，无显著质量下降。**

## seekdb 数据层与混合检索

表结构（`scripts/schema.sql`）：`docs(kb, id, doc_name, chunk_idx, content, embedding VECTOR(384), PK(kb,id), FULLTEXT INDEX ... WITH PARSER ik, VECTOR INDEX ... DISTANCE=cosine TYPE=hnsw LIB=vsag)`。

混合检索由 **seekdb 一条 SQL** 完成（无客户端两路拼合）：

```sql
SELECT id, doc_name, chunk_idx, content, cosine_distance(embedding, ?) AS dist
FROM docs
WHERE kb = ? AND MATCH(content) AGAINST(?)
ORDER BY dist APPROXIMATE
LIMIT ?;
```

- 向量为浏览器端 mean-pooling + L2 归一化输出，与 cosine 度量匹配。
- 增量导入：批量 `INSERT ... ON DUPLICATE KEY UPDATE`；seekdb 两级 HNSW（增量+快照）使新写入立即可检索（实测验证，见 docs/verification.md）。
- 集合/会话状态：`kb` 列隔离集合（主库 `main`、评测集合 `eval-*`、性能集合 `perf-*`），`kb_state` 表记录各集合文档/块数，界面实时展示。

## 已知限制

- all-MiniLM-L6-v2 词表以英文为主：**纯中文短查询**（如 4 字短语）语义排序信号弱（全文 MATCH 仍能命中兜底）；含英文/拼音术语或成句的中文查询效果良好。改进方向：替换 bge-small-zh-v1.5 等中文模型（接口已预留，仅需换权重与维度）。
- 实测设备（AMD gcn-5 / Vega 级 GPU）无 INT8 硬件加速：q8 在 WebGPU 上反而慢于 fp32（kernel 退化），WASM 上 q8 快于 fp32；现代 GPU（RDNA3/Ada 等）通常相反。报告按本机实测如实记录。
- 无头浏览器 / SwiftShader 软渲染环境下 WebGPU 可用但极慢，属环境特性。

## 验收自检清单

见 [docs/verification.md](docs/verification.md) 与 [docs/perf-report.md](docs/perf-report.md)；全部条目均有真实运行数据或截图支撑。
