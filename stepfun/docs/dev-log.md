# 开发记录

## 时间线（2026-11-11 ~ 11-12）

1. **环境勘测**：WebGPU 可用（AMD gcn-5）；HF 直连不通、hf-mirror 直连可用；端口 2881/5173/8080 空闲；
   seekdb 镜像在本地；seekdb-js npm 包实为 `seekdb@1.4.0`（Node-only，mysql2），浏览器无法直连 → 确定“极薄转发层”架构。
2. **M1 环境脚本**：`gen_corpus.py` 确定性生成 200 篇语料 + 40 条标注查询；`init/reset_seekdb.sh` + `schema.sql`
   （踩坑：`precision` 为保留字改名 `model_dtype`；vsag 拒绝全同值向量，真实 embedding 不受影响）；`download_models.mjs`
   实测 3 套官方权重体积（86.2/43.2/21.9 MiB）。
3. **M2 最小闭环**：Express 转发层（seekdb SDK `execute()` 参数化 SQL）+ Vite/TS 前端（Transformers.js 4.3.2）。
   踩坑与修复：SDK 无浏览器构建（确认架构）；`@seekdb/default-embed` 打包缺陷导致 npm 报 `workspace:*`
   （`--legacy-peer-deps` 规避，本项目不使用该包）；vector 数组需服务端序列化为 `[x,y,...]` 字面量；
   后端/精度下拉缺 change 监听导致状态不更新；doc_count 每批重复累加（仅首批计数）。
4. **M3 量化对比与降级**：5 组配置评测矩阵 + 精确排序口径（剥离 HNSW 噪声）。关键修复：`navigator.gpu` 存在但
   `requestAdapter()` 返回 null 时 ORT WebGPU 初始化**挂起不抛错** → 增加 3s 超时预探测，探测失败直接走 WASM
   （真实 Chrome `--disable-gpu` 实测验证降级生效）。
5. **M4 性能测量**：batch 1/16/64 对照、逐条 vs 批量写入（8.7x）、冷/热模型加载（6.7x/13.4x）、分阶段计时 5 次重复。
6. **M5 文档与验收**：README/技术说明/性能报告/验证样例/证据截图；生产模式构建验证；验收自检全绿。

## 关键决策与理由

- **架构**：浏览器无法直连 seekdb（MySQL 协议、SDK 无浏览器构建），故加极薄 Node 转发层；检索逻辑全部在
  seekdb 单条 SQL，转发层只做参数化 SQL 与静态资源服务，不触碰任何向量/排序计算（题面合规）。
- **模型**：选 all-MiniLM-L6-v2 官方 q8：零自量化风险 + 官方 fp32/fp16/q8 同仓库可比；中文语义弱由 IK 全文兜底，
  并在报告中如实披露（纯中文短查询限制 + bge-small-zh 改进方向）。
- **量化证据**：同语料同查询集、每精度独立 seekdb 集合，混合/纯向量/精确三口径 Recall@K/nDCG@10 —— 精确口径
  专门隔离量化影响与 HNSW 构图噪声。
- **评测数据落盘**：评测/性能结果经 `/api/kb/evallog` 写 `data/eval-results/`（提交进仓库），逐查询明细可复核。
- **已知坑记录**：seekdb 容器为 MySQL 5.7.25-OceanBase 兼容协议；`MATCH...AGAINST` 参数化可用；多语句经
  `docker exec -i mysql < file` 时偶发无输出，拆单条执行规避；Windows 控制台中文乱码仅为显示问题。

## 环境备注

- 主测：Qoder 内置浏览器（AMD gcn-5 真实 GPU）；降级路径与复现截图：系统 Chrome 157（headless，SwiftShader 软渲染，
  仅作功能证据，性能以主测环境为准）。
- seekdb 容器 `seekdb-stepfun`（数据卷 `seekdb-stepfun-data`，与其它赛道隔离），库 `stepfun_app`。
- 临时材料（测试脚本、日志、截图原始件）在仓库外 `D:\projects\新建文件夹\stepfun作答\`。
