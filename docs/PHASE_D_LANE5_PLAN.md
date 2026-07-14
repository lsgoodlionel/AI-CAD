# Phase D 泳道 5 — 前沿技术升级（研究型 · 独立节奏）

> 版本 V1.0 ｜ 2026-07-14 ｜ 承接 `docs/PHASE_D_BLUEPRINT.md` 泳道5（D-16~20）
>
> 泳道 1~4 已全部落地并合并 main（PR #12）。泳道 5 是**研究型**升级，节奏独立、多卡外部条件。
> 本文对每个工作块给出：**现状 / 方向 / 需你提供 / 需确认 / 缺什么 / 能否现在开始**。
> 全部升级都走本项目已建立的**「可插拔后端 + 有序回退 + 优雅降级」范式**，集成风险低。

## 0. 已确认的「可插拔缝」（决定集成风险 = 低）

| 升级 | 现有缝 | 替换方式 |
|---|---|---|
| D-16 OCR 底座 | `core/model3d/ocr/types.py::OcrBackend` Protocol（`recognize()`）+ service 有序回退（paddle→rapid→mock） | 加一个实现 Protocol 的新后端 + 插入回退链 |
| D-17 规范导入 | `services/regulation_importer.py::extract_text_from_pdf`（pymupdf4llm→pymupdf 降级） | 加一个 extractor 函数 + 重排优先级 |
| D-18 合规审查 | `core/ai_review/{kg_engine,rag_engine}.py`（各自 `BaseEngine.analyze`） | 加一层双路召回融合，不改两引擎本体 |
| D-19 符号 spotting | `core/model3d/spotting/types.py::SpottingBackend` Protocol（CADTransformer/VecFormer stub 已在） | 权重释放即填充 stub |

---

## D-16 — OCR 底座升级（PaddleOCR-VL / PP-StructureV3）

- **现状**：当前 `paddle_backend`（PP-OCR 2.x 系）+ `rapid_backend` 回退（aarch64 上 paddle SIGSEGV，实际走 RapidOCR）。已在建模主链 `_section_levels_ocr_fallback` 真实喂剖面标高。
- **前沿**：PaddleOCR-VL **1.6**（2026-05，OmniDocBench 96.3%，Apache 2.0）+ PP-StructureV3（版面/表格→Markdown/JSON，带单元格坐标）。中文/复杂版面显著优于 2.x。
- **方向**：新增 `paddleocr_vl_backend` 实现 `OcrBackend`，用 PP-StructureV3 做**图签/说明页整页结构化**（比逐 token 更适合读标题栏/图框元数据）；离线 A/B 评测对比现后端，胜出才切默认回退顺序。
- **需你提供**：① 是否允许在**部署镜像**装 paddleocr 3.x（体积/aarch64 兼容需实测，可能仍需 RapidOCR 兜底）；② 一小组**脱敏真实图纸**做评测集（歌剧院 2309 张里抽 20~50 张覆盖平面/剖面/图签，本地已有）。
- **需确认**：默认后端是否切 VL，还是仅在「图签结构化」这一子任务用 VL、逐 token 仍用轻量后端（省算力）。
- **缺什么**：CAD 图纸专项准确率无公开基准 → 必须自建评测（标高/轴号/图名识别 Recall + 置信标定）。
- **能否现在开始**：✅ **可以**——先建**离线 OCR 后端评测基座**（对现有 OcrResult 结构，比多后端在评测集上的标高/轴号/图名命中率），并预写 `paddleocr_vl_backend` 适配器（deps 缺失时优雅降级，同现有 backend 范式）。评测跑通、且镜像可装后再切默认。

## D-17 — 规范导入升级（MinerU 2.5 / docling）

- **现状**：`extract_text_from_pdf` 用 pymupdf4llm→pymupdf 降级，喂 NLP 提取流水线（Haiku 分类→Sonnet 深提取→AGE/Chroma）。
- **前沿**：MinerU 2.5（OpenDataLab，CJK 版面/表格最强，**AGPL-3.0** → 商用需 license 审计，离线工具链使用不必然传染服务端，仍须过门禁）；docling（IBM，**MIT**，LangChain/LlamaIndex 原生集成，兼容我们的 LangChain 栈）。
- **方向**：加 `extract_with_docling`（首选，license 干净）或 `extract_with_mineru`（CJK 表格更强，过 license）作为 extract_text 前段候选；对**规范 PDF→条文抽取质量**做离线 A/B（抽取召回率 + 表格结构保真），胜出才切。
- **需你提供**：① MinerU 若采用，**过 `PHASE_C_LICENSE_AUDIT` 同款审计**的授权决定（AGPL 商用风险你拍板）；② 一组**代表性规范 PDF**（含带表格/多栏的强条文）做评测。
- **需确认**：优先 docling（MIT 稳）还是 MinerU（CJK 强但 AGPL）——建议先 docling。
- **缺什么**：规范抽取质量的**金标准标注**（哪些条文/表格算抽对）。
- **能否现在开始**：✅ **可以**——docling 是 MIT，可先建离线 A/B 评测 + `extract_with_docling` 适配器（deps 缺失降级到现有 pymupdf4llm）。

## D-18 — 合规审查 GraphRAG 化

- **现状**：KG 引擎（Apache AGE Cypher，条文图谱 IF-THEN 推理）与 RAG 引擎（Chroma 向量 Top-K + LangGraph）**各自独立**跑，orchestrator 并行但不融合。
- **前沿**：GraphCompliance / SIERA（Neo4j+ReAct）等示范「条文图 + 向量双路召回 + agent 多步核查」提升合规推理。
- **方向**：加一层 **GraphRAG 融合召回**——同一查询走「条文图谱结构召回」+「向量语义召回」双路，合并去重后交 LLM 多步核查；不改 KG/RAG 引擎本体，作为新的编排层（可灰度开关，关闭时恒等回到现并行）。
- **需你提供**：合规审查的**离线评测集**（一批图纸问题 + 人工判定的合规结论）用于对比「纯 KG / 纯 RAG / GraphRAG 融合」的精度/召回。
- **需确认**：融合仲裁策略（图谱强命中优先？双路都命中才高置信？）——建议沿用 fusion 引擎同款「规则/结构强命中不被覆盖」哲学。
- **缺什么**：评测集是最大缺口（合规判定需专家标注）。
- **能否现在开始**：🟡 **部分**——可先写**融合层设计 + 评测口径**，并搭 harness 骨架（复用 `core/model3d/eval` 的度量范式）；真实结论待评测集。

## D-19 — VecFormer / 符号 spotting 天花板跟踪

- **现状**：spotting 主力 CADTransformer（MIT，权重已释放）；VecFormer（Apache 2.0，FloorPlanCAD PQ **91.1**）**权重仍未公开释放**（2026-07 复查：HuggingFace 无明确权重）。stub 已就位。
- **新增跟踪对象**：**CADSpotting**（arXiv 2412.07377，大规模 CAD 鲁棒 panoptic spotting）——评估其许可与权重可用性。
- **方向**：维持每月复查（`PHASE_C_VECFORMER_WATCH.md`）；任一（VecFormer/CADSpotting）释放可商用权重即按 `SpottingBackend` 适配。
- **需你提供**：无（纯跟踪）。
- **能否现在开始**：✅ **已做本轮复查**（见 watch 文档更新）。

## D-20 — C-09 符号识别微调（M1 终评）

- **现状**：数据切分（C-07）、COCO 导出（C-16 审校飞轮）、评测基座（C-14）全就绪；**卡 GPU + 脱敏训练数据 + 权重**。
- **需你提供**：① **GPU 环境**（本地/云）；② **脱敏标注数据**（审校飞轮已在产出金标签，需累积到量）；③ 训练时窗。
- **能否现在开始**：❌ **阻塞**——外部条件不满足；一旦 GPU+数据到位，按 C-08 adapter + C-14 harness 直接跑出 M1 终评数字。

---

## 建议的启动顺序（能推进的先推）

1. **D-16 OCR 评测基座 + PaddleOCR-VL 适配器**（seam 就绪、license 干净、真实图已有）——**本轮可开工**。
2. **D-17 docling 适配器 + 规范抽取 A/B**（MIT、栈兼容）——需你给规范 PDF 样本即可开工。
3. **D-18 GraphRAG 融合层设计 + harness 骨架**——设计可先行，结论待评测集。
4. **D-19** 持续跟踪（已复查）。
5. **D-20** 待 GPU/数据解锁。

## 需要你拍板的 4 件事

1. 部署镜像是否允许装 paddleocr 3.x（体积/aarch64）——决定 D-16 默认后端能否切。
2. D-17 优先 docling(MIT) 还是评估 MinerU(AGPL，需你授权过 license 门禁)。
3. 提供 D-16/D-17/D-18 的评测样本（图纸/规范 PDF/合规判定）——评测无样本无从判优劣。
4. D-20 的 GPU + 脱敏数据时窗。
