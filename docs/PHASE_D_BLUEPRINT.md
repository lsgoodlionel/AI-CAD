# Phase D 蓝图 — 全模块串联 · 操作简化 · 前沿升级

> 版本 V1.0 ｜ 2026-07-13 ｜ 承接 Phase 0~C 全部完成 + `fix/model-3d-quality`（PR #11）
>
> 定位：Phase A/B/C 把「能力」逐块建成了；Phase D 的主题是**把能力串成产品**——
> 打通模块间断点、合并同类入口、给每个专业流程配引导，同时吸收 2025-2026 前沿升级技术底座。
> 全部工作块按 6 条可并行泳道组织，供多 agent / 多人并行开发。

---

## 0. 现状全貌盘点（基于代码实测，2026-07-13）

### 0.1 能力资产（已建成）

| 层 | 资产 | 规模 |
|---|---|---|
| 后端路由 | 21 个 router（main.py 注册） | drawings / review_batches / project_models / model_spotting / model_annotations / model_review / 三审×3 / incentive / economic_calc / regulations / dashboard / admin×6 / auth / projects |
| 服务层 | 33 个 service | 建模链 14 个（model_*）+ 审图/报告/激励/规范 |
| 异步任务 | 6 个 Celery 任务 | ai_review / batch_review / model_build / proposal_notice / regulation_import / regulation_api_sync |
| 数据库 | 25 个 migration | 001 核心业务 → 025 楼层标高人工录入 |
| AI 能力 | 5 引擎审图 + spotting/fusion + OCR 基座 + VLM 语义 | core/ai_review + core/model3d |
| 前端 | 7 组菜单、~20 页面 | dashboard×2 / drawings×4 / model / incentive×2 / admin×5 / help |

### 0.2 模块串联现状（✅ 已通 / 🟡 半通 / ❌ 断裂）

| 链路 | 状态 | 证据 |
|---|---|---|
| 上传 → AI 审图（自动触发） | ✅ | `routers/drawings.py:142` `run_ai_review.delay(drawing_id)` |
| AI 审图 → 三审工作流 | ✅ | DrawingDetail 四面板同页 |
| 单图审图 ↔ 套图审图（跨图分析） | 🟡 | 两套入口、两套报告，用户需理解两个概念 |
| 图纸 → 3D 建模 | ❌ **手动** | 仅 `POST /model/rebuild`，上传/审图完成后不自动触发，无「哪些图变了该重建」提示（rebuild-impact 端点有了但被动查询） |
| OCR 基座 → 楼层/拼接/语义 | 🟡 | `ocr/consume.py` 三条馈线已写好，`model_builder`/`model_story` 已引用；但真实 Paddle 推理待正式 build，且拼接（axis_anchors）与语义树（space_labels）下游只接了一半 |
| 符号 spotting/fusion → 建模主管线 | ❌ | spotting 只在独立 router（`model_spotting`）+ 审校队列消费；`model_builder` 构件识别仍走纯规则，融合结果未回灌建模 |
| 建模 → QTO → 创效提案 | ✅ | `POST /model/quantities/to-proposal` |
| QTO ↔ 钢筋翻样（economic_calc） | ❌ 平行 | 两套算量入口互不知晓：QTO 在模型页，钢筋翻样在图纸详情 EconomicCalcPanel |
| 审校 → 数据飞轮（COCO 导出 → C-09 微调） | 🟡 | 导出链就绪，微调卡 GPU |
| 审图问题 → 创效线索 | ❌ | AI 审图发现的优化空间不会自动生成创效提案建议 |

### 0.3 同类功能重复（合并候选）

1. **上传入口 ×3**：单张 / 批量 / ZIP 整套（DrawingList 弹窗内三种模式）+ 套图审查页又有独立创建入口。
2. **审查概念 ×3**：单图 AI 审图、会审审查（Tab）、套图审查（独立页）——本质都是「问题清单 + 报告」。
3. **算量 ×2**：IFC-QTO（模型页）与钢筋翻样（图纸详情），口径与入口都分裂。
4. **审校队列 ×2**：DrawingAnnotationQueue（符号级）与 SemanticReviewQueue（语义级）在模型页并列两个折叠面板，动作语义高度相似（确认/否定/改类）。
5. **看板 ×2**：集团/项目两个页面，角色判断交给用户选菜单。

### 0.4 操作复杂度实测（关键路径点击数）

以「一套新图 → 看到创效提案」为例，现在需要：
DrawingList 上传（选模式+逐行核对元数据）→ 等审图 → DrawingDetail 看报告 → 回列表建套图批次 → ReviewBatch/Detail 看跨图 → 切到 /model 手动重建（等 10 分钟）→ 展开算量 → to-proposal → 切到 /incentive 走签字流。
**跨 5 个页面、2 次手动异步触发、无任何向导。** 模型页单页 10+ 面板（语义树/单体/楼层/专业/严重度/标记/构件图层/模型质量/语义审查/待人工识别/标高校正），首次使用无从下手。

---

## 1. Phase D 总体架构决策

### D-架构-1：以「项目」为主视角，图纸为从属

现状信息架构以图纸为中心（菜单第一项是图纸列表），但用户心智是「我的项目进行到哪一步了」。
Phase D 新增**项目工作台（Project Hub）**作为默认落地页：单项目一屏呈现
`图纸(N) → 审查(问题 M 个/已闭环 K) → 模型(LOD 等级) → 算量(3 项汇总) → 创效(提案 P 个)` 流水线卡片，
每张卡直达对应模块并携带项目上下文。

### D-架构-2：事件驱动串联（Pipeline Orchestrator）

新增轻量编排层（Postgres 事件表 + Celery chain，**不引入新中间件**）：

```
drawing.uploaded ──► classify(view_type/discipline) ──► ai_review
                                    │
     batch.completed ◄── 自动归入项目套图批次（可关）
                                    │
ai_review.completed ──► model.rebuild_impact 刷新 ──► (阈值超限) 提示/自动增量重建
model.built ──► qto.refresh ──► (节约额 > auto_proposal_min_saving) 创效建议推送
```

原则：**自动打底、人工确认**（沿袭标高录入通道的成功模式）。每个自动环节可在引擎参数页按项目开关。

### D-架构-3：统一领域对象「Finding」

单图 AI 问题、会审发现、跨图问题、语义审查项、符号待审项，统一到一个 Finding 抽象
（source: engine|review|cross|semantic|symbol，同一严重度/状态机/闭环动作），
前端一个「问题中心」组件多处复用。这是合并三个审查入口的数据基础。

---

## 2. 六条并行泳道

> 每条泳道内部串行、泳道之间可并行；标注了跨泳道依赖。工作块编号 D-xx。

### 泳道 1｜项目工作台与流程引导（前端主力）

| # | 工作块 | 内容 | 验收 | 涉及 |
|---|---|---|---|---|
| D-01 | 项目工作台页 | `/projects/:id/hub`：流水线卡片（图纸/审查/模型/算量/创效）+ 阶段状态 + 下一步行动按钮；设为登录默认页 | 关键路径页面跳转 5→2 | 新 `pages/project/Hub/`、复用 dashboard 数据端点 |
| D-02 | 统一上传向导 | 合并单张/批量/ZIP 为一个拖拽入口：自动判文件数/压缩包 → 文件名解析预填（已有 parser）→ 一步确认 → 可勾选「自动建套图批次」 | 上传模式选择消失；元数据核对一屏完成 | `DrawingList` 上传弹窗重构、`/batch` `/import-zip` 复用 |
| D-03 | 流程向导（Steps） | 项目工作台顶部常驻 Steps：上传→审查→建模→算量→创效，当前步高亮 + 每步「去完成」；空状态页全部配引导文案与直达按钮 | 新用户不看手册可走通全流程 | Hub + 各模块空状态 |
| D-04 | 帮助内嵌化 | 现有 `/help` 手册保留；在各页关键控件加 `?` 气泡（内容取自手册锚点），手册加 deep-link | 每个专业面板均有就地解释 | `pages/Help/`、各面板 |

### 泳道 2｜审查中心合并（Finding 统一）

| # | 工作块 | 内容 | 验收 | 涉及 |
|---|---|---|---|---|
| D-05 | Finding 统一模型 | migration 026：finding 视图/表统一五类来源；三个报告口径归一 | 一个 API 可查项目全部问题 | 新 `routers/findings.py`、`services/finding_service.py` |
| D-06 | 审查中心页 | `/projects/:id/review`：Tab= 全部/单图/跨图/会审/语义；问题闭环状态机（待处理→已确认→已整改→已闭环）；套图批次成为筛选维度而非独立页面 | ReviewBatch 独立页下线（路由重定向） | `pages/review/Center/`，吸收 `ReviewBatch/*`、`AIReviewPanel`、`ReviewFindings` |
| D-07 | 审图→创效线索 | Finding 打「有创效潜力」标（规则+LLM 判别，走 ModelRouter），一键转创效提案草稿（复用 to-proposal 模式） | 从问题到提案草稿 ≤2 步 | `finding_service` + incentive |

### 泳道 3｜建模主管线串联（后端主力，依赖最少可先动工）

| # | 工作块 | 内容 | 验收 | 涉及 |
|---|---|---|---|---|
| D-08 | 事件编排层 | migration 027：`pipeline_events` 表 + Celery chain；实现 §1 D-架构-2 的四个自动环节，引擎参数页加开关 | 上传→建议重建全自动（人工确认制） | 新 `core/pipeline/`、`tasks/` |
| D-09 | spotting 融合回灌建模 | `model_builder` 构件识别处接 `fusion` 引擎输出（规则强命中不覆盖原则已有），带 source/confidence 入 scene | 模型构件带识别来源标注；纯规则回退不变 | `services/model_elements.py`、`core/model3d/fusion` |
| D-10 | OCR 三馈线接满 | axis_anchors → `cross_view_registration`（配准锚点补源）；space_labels → `model_semantics`（语义树）；真实 Paddle 推理随正式 build 放开 | 三条 consume 管线均有真实消费者 + 单测 | `ocr/consume.py` 下游、`requirements-ocr.txt` |
| D-11 | LOD300 P2 专项 | 并入 `docs/MODEL_P2_PLAN.md` 阶段 A→E（剖面层高/比例尺/轴网配准/尺寸标注/拓扑），OCR 标高候选（D-10）作为 A 的新证据源 | cross_view_match + scale 门槛点亮（歌剧院实测） | 见 MODEL_P2_PLAN 附录速查 |
| D-12 | 算量中心合并 | QTO 与钢筋翻样统一到 `/projects/:id/quantities`：同一汇总口径（混凝土/模板/钢筋），翻样明细为下钻页；两处旧入口重定向 | 一个算量入口、一套导出 | `model_qto_summary` + `economic_calc` 前端合并 |

### 泳道 4｜模型工作台重构（依赖 D-05 的 Finding 统一）

| # | 工作块 | 内容 | 验收 | 涉及 |
|---|---|---|---|---|
| D-13 | 视图模式化 | 模型页改三模式：**浏览**（语义树/楼层/单体/专业筛选）、**审校**（合并两个审校队列为统一收件箱，按置信度+冲突优先排序）、**算量**（QTO 面板+构件高亮）；面板随模式呈现，`index.tsx`（1100 行）拆分 | 每模式常驻面板 ≤4 个；文件 <400 行/个 | `pages/model/ProjectModel/` 全面重构 |
| D-14 | 审校收件箱 | DrawingAnnotationQueue + SemanticReviewQueue 合并：统一动作（确认/否定/改类/补框）、统一埋点（复用 024 表）、键盘快捷键流水作业 | 单条审校操作 ≤2 击键；埋点口径不变（C-17 看板不受影响） | 两队列组件合并 |
| D-15 | 角色自适应看板 | group/project 两看板按角色合并入口，内容区块化；接入 D-08 管线状态（各项目卡在哪一步） | 菜单只剩一个「看板」 | `pages/dashboard/` |

### 泳道 5｜前沿技术升级（研究型，独立节奏）

| # | 工作块 | 内容 | 依据 |
|---|---|---|---|
| D-16 | OCR 底座升级评估 | PaddleOCR 3.x / PP-StructureV3（中文 Edit-Distance 0.210，显著优于 2.x）替换现 paddle_backend；PaddleOCR-VL-1.5（OmniDocBench 94.5%）做图签/说明页整页结构化 PoC | 现有 `SpottingBackend`/`OcrBackend` Protocol 天然支持热替换 |
| D-17 | 规范导入升级 | 用 MinerU 2.5（CJK 版面/表格最强）或 docling（LangChain 生态）替换 pymupdf4llm 前段，提升规范 PDF→条文抽取质量；先离线 A/B 对比再切 | `services/regulation_importer.py` 流水线前段可插拔 |
| D-18 | 合规审查 GraphRAG 化 | KG 引擎（Apache AGE）+ RAG 引擎融合为 GraphRAG 检索（条文图 + 向量双路召回，参考 GraphCompliance/SIERA 思路）；离线评测集先行 | `core/ai_review/{kg,rag}_engine` 已分别就绪，缺的是双路融合层 |
| D-19 | VecFormer 持续跟踪 | 维持每月复查（`PHASE_C_VECFORMER_WATCH.md`）；权重释放即按预写 adapter 切换（PQ 91.1 vs CADTransformer） | stub 已就位 |
| D-20 | C-09 微调启动 | GPU/脱敏数据就绪即启动：C-07 切分 + C-16 COCO 导出已备齐，跑 C-14 评测基座出 M1 终评数字 | 顺延项，条件触发 |

### 泳道 6｜工程化与验收

| # | 工作块 | 内容 |
|---|---|---|
| D-21 | 路由迁移与兼容 | 旧路由 301 重定向表（review-batches→审查中心、economic-calc→算量中心）；书签/通知内链不断 |
| D-22 | 手册同步 | 按「边开发边更新」约定同步 `MODEL_MANUAL_*` + 新增《项目工作台手册》章节；`/help` deep-link 支持 |
| D-23 | E2E 验收 Demo | `tests/e2e/test_phase_d_demo.py`：合成整套图从上传到创效提案全自动链路断言（页面 ≤2 次跳转、0 次手动触发建模）；沿用 Phase B/C Demo 模式 |
| D-24 | 度量 | 埋点三个北极星指标：①关键路径完成时长 ②建模自动触发采纳率 ③审校单条耗时；接入 C-17 看板 |

---

## 3. 依赖关系与并行建议

```
泳道3(D-08~12 后端串联) ──┐
                          ├──► D-23 E2E 验收
泳道2(D-05~07 Finding) ───┤
   │ D-05 是 D-06/D-13 前置│
泳道1(D-01~04 工作台) ────┤   D-01 仅依赖现有端点，可立即动工
泳道4(D-13~15 模型页) ────┘   D-13 依赖 D-05 + D-14
泳道5(D-16~20 前沿) ─────► 独立节奏，PoC 通过才并入主线
```

**建议首批并行启动**（互不冲突，适合 4 个并行 worktree）：
1. **D-01+D-03** 项目工作台+向导（纯前端新增页）
2. **D-05** Finding 统一模型（后端，migration 026）
3. **D-08** 事件编排层（后端，migration 027）
4. **D-11** LOD300 P2 阶段 A（剖面层高，纯函数可离线验证，MODEL_P2_PLAN 已排好）

**第二批**：D-02/D-06/D-09/D-10/D-12；**第三批**：D-13/D-14/D-15/D-07 + 泳道 6 收口。
预估节奏：首批 ~1 周，全 Phase D ~4-6 周（不含泳道 5 研究型条目）。

---

## 4. 前沿与同类仓库参照（2026-07 调研）

| 方向 | 项目/工作 | License | 对本项目的适配 |
|---|---|---|---|
| 符号 spotting | [VecFormer](https://github.com/WesKwong/VecFormer)（NeurIPS 2025，FloorPlanCAD PQ 91.1） | Apache 2.0 | 权重未释放，维持 C-10 跟踪；释放即替换 CADTransformer |
| 文档解析 | PaddleOCR 3.x / PP-StructureV3、PaddleOCR-VL-1.5 | Apache 2.0 | D-16：OCR 基座直接升级路径 |
| 文档解析 | [MinerU 2.5](https://arxiv.org/pdf/2509.22186)（OpenDataLab，CJK 最强） | AGPL→商用需评估（MinerU 2.x 起 AGPL-3.0，**离线工具链使用不传染服务端**，仍需 license 审计过门禁） | D-17：规范 PDF 导入前段 |
| 文档解析 | docling（IBM，LangChain/LlamaIndex 原生集成） | MIT | D-17 备选，生态兼容我们的 LangChain 栈 |
| BIM 前端 | @thatopen/fragments（**已在用** 3.4.6）+ engine_components | MIT | 继续深化，不换栈 |
| BIM 前端 | [xeokit-sdk](https://github.com/xeokit/xeokit-bim-viewer) | **AGPL-3.0**（商用需付费授权） | ⛔ 不引入（license 门禁），仅作能力对标（BCF 视点、双精度坐标值得借鉴自研） |
| BIM 协同 | speckle-server | Apache 2.0 | 可选集成（已在技术栈预留），多源模型汇聚时再评估 |
| 合规审查 | [GraphCompliance](https://arxiv.org/pdf/2510.26309)、SIERA（Neo4j+ReAct）、[LLM-BIM 合规](https://arxiv.org/pdf/2506.20551) | 论文为主 | D-18 思路来源：条文图+向量双路召回、agent 式多步核查 |

---

## 5. 风险与守则

1. **合并不丢功能**：D-06/D-12/D-13 均为入口合并，旧能力逐项 checklist 迁移 + 旧路由重定向（D-21），禁止直接删页。
2. **自动化不越权**：所有自动触发默认「建议制」（通知+一键执行），项目级开关；三审/签字等硬约束一律不自动。
3. **诚实分级不动摇**：D-09/D-11 任何识别增强沿用「绝不虚高 LOD/置信」原则，回退兜底必须保留。
4. **license 门禁前置**：泳道 5 任何新依赖先过 `PHASE_C_LICENSE_AUDIT` 同款审计再进 requirements。
5. **手册同步是验收项**：每个 D-xx 合并前检查 MODEL_MANUAL / 帮助中心是否更新（D-22）。

---

## 附：本蓝图事实依据速查

- 串联断点核查：`routers/drawings.py:142`（审图自动）、`routers/project_models.py:118`（重建手动）、`core/model3d/ocr/consume.py`（三馈线）、`grep fusion services/` 空（未回灌）
- 前端复杂度：`pages/model/ProjectModel/index.tsx`（~1100 行 10+ 面板）、`DrawingList` 三模式上传
- LOD300 路线：`docs/MODEL_P2_PLAN.md`（阶段 A~E 完整方案，直接并入 D-11）
- VecFormer 跟踪：`docs/PHASE_C_VECFORMER_WATCH.md`
