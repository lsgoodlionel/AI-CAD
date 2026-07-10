# Phase B（算量级）详细任务分解

> 版本 V1.0 | 2026-07-10 | 面向排期开发的可执行任务清单
>
> 上游依据：`docs/AI_READING_TO_3D_MODEL.md` 第四章 Phase B、`docs/MODEL_PRECISION_BLUEPRINT.md`
> 跨视图 z 恢复详设：`docs/CROSS_VIEW_Z_RECOVERY_DESIGN.md`（**尚未生成**，本文档相关任务已就地展开总方案描述，并标注「详见 z 恢复详设文档」，待详设落地后回链）

---

## 0. 范围与总目标

**Phase B 目标（算量级，中期攻坚，3–5 月）**：从「贴图堆叠 + 硬编码常量挤出」升级为「跨视图恢复真实几何 + 确定性拓扑 + IFC 自带工程量」，让模型能出**算量可用**的混凝土量 / 模板量 / 钢筋量，并与创效激励系统闭环。

**四大工作块**：
1. 跨视图 z 向恢复（核心自研）：剖面标高 + 立面洞口 + 轴网配准 → 真实标高表 + 构件截面表，替换硬编码常量，点亮 `model_lod` 的 `cross_view_match` gate。
2. 构件拓扑规则（确定性）：门窗-墙从属、梁-柱支承、板-梁托承。
3. IFC-QTO 算量：`Qto_*BaseQuantities`（混凝土净体积 / 模板侧面积）+ 钢筋回填 IFC。
4. 图种判别（平面 / 剖面 / 立面），为 z 恢复供料。

### Scope Boundary（明确不做，留给 Phase C）

- ❌ **不做**符号识别学习模型（CADTransformer / VecFormer）——留 Phase C。
- ❌ **不做**自建标注数据集与冷启动标注引擎——留 Phase C。
- ❌ **不做**人工审校工作台深化（3D 重建结果接三审队列）——留 Phase C，Phase B 仅产出带置信度的机器初模。
- ❌ **不做**扫描件重建（CubiCasa / HEAT / RoomFormer）——独立技术栈，当前走矢量主线。
- ✅ Phase B 只用**确定性规则 + 已有 VLM 读表**，符号语义沿用现有 `element_recognizer.py`（Phase A 已强化）。

### Phase B 验收总标准

1. 一套「含规范剖面标注」的整套图 → 模型出**真实层高 / 板厚 / 梁高**（非默认常量），`model_lod` 判定达 LOD300 且 `cross_view_match` gate 通过。
2. 缺剖面 / 缺标注时**显式回落默认值并打 `estimated=true` + 置信度**，绝不把常量伪装成实测（provenance 可追溯到证据来源）。
3. IFC 模型写出 `Qto_*BaseQuantities`，混凝土净体积、模板侧面积可导出；钢筋量由 `rebar_calculator.py` 回填。
4. QTO 结果可一键生成创效测算草稿，与 `incentive_proposals` 打通。
5. 全链路测试覆盖率 ≥ 80%，z 恢复与拓扑规则核心算法 100% 分支覆盖。

---

## 1. 现状集成点（代码事实核对）

| 集成点 | 文件（精确路径） | Phase B 动作 |
|---|---|---|
| 跨视图分析 | `apps/api/core/ai_review/cross_drawing.py`（`analyze_batch`，当前仅平面间关联） | **扩展**：吃剖 / 立面，产出标高表 + 截面表 |
| 硬编码层高 | `apps/api/services/model_lod.py:7` `DEFAULT_STORY_HEIGHT_M = 4.5`；`services/model_story.py:12` 同值 | **替换**为标高表驱动 |
| LOD gate | `apps/api/services/model_lod.py` `has_cross_view_match` / `LOD300_GATES` 含 `cross_view_match` | **点亮**该 gate |
| 构件挤出 | `apps/api/services/model_builder.py`、`services/model_elements.py`（beams/slabs/pipes 截面参数） | **接入**截面表，替换默认截面 |
| 楼层解析 | `apps/api/services/floor_parser.py`、`services/model_story.py` | 消费标高表 |
| IFC 建模器 | `apps/api/services/model_ifc_builder.py`（**Phase A 产出，当前不存在**） | **在其上写量集** |
| 钢筋算量 | `apps/api/core/economic/rebar_calculator.py`（`calc_anchor_lengths` / `optimize_cutting`，GB50010） | 结果回填 IFC |
| 图种/专业 | `apps/api/services/drawing_filename_parser.py`（有 `discipline`，**无 view_type**） | 新增图种判别 |
| 创效 | `apps/api/routers/incentive.py`（`incentive_proposals` 表，`net_saving` 字段） | QTO → 测算草稿 |
| 迁移编号 | 现存至 `016_model_semantic_graph.sql` | Phase B 用 **017+**（与 Phase A 冲突则顺延） |

> ⚠️ **对 Phase A 的依赖**：`model_ifc_builder.py`（IFC 程序化建模器）由 Phase A 交付。QTO 相关任务（B-16~B-19）依赖它就位。若 Phase A 延期，B-16~B-19 可用 IfcOpenShell 最小自建 stub 并行开工，后期切换。

---

## 2. 任务清单

> 字段：ID / 标题 / 描述 / 交付物 / 涉及文件 / 依赖 / 开源件（许可）/ 工作量（S≤1、M=2–3、L=4–5 人天）/ 验收标准 / 风险

### 工作块一：图种判别（供料层）

#### B-01 图种判别器（平面 / 剖面 / 立面 / 详图）
- **描述**：从文件名 + 标题栏 OCR + 图内几何特征（水平标高线密度、轴网方向、剖切符号）判别单图为平面 / 剖面 / 立面 / 详图。产出 `view_type` + 置信度，供 z 恢复精确取料。规则优先，模糊时可选叠加现有 VLM（`ModelRouter` 的 `drawing_visual_analyzer` 引擎）读标题栏。
- **交付物**：`classify_view_type(drawing) -> ViewTypeResult{view_type, confidence, evidence}`。
- **涉及/新增文件**：新增 `apps/api/services/drawing_view_classifier.py`；扩展 `services/drawing_filename_parser.py`（增 view_type 关键词）；复用 `services/drawing_semantics.py`。
- **依赖**：无（可即刻开工）。
- **开源件**：ezdxf（LGPL-style/MIT 兼容）读几何、PaddleOCR（Apache 2.0）读标题栏；VLM 走现有 `ModelRouter`。
- **工作量**：M（3）
- **验收**：对样本集（≥30 张，覆盖四类）判别准确率 ≥ 90%；剖 / 立面召回 ≥ 95%（漏判剖面代价最高）；低置信度显式标 `uncertain`。
- **风险**：中文标题栏用词不统一（"剖面图/剖视/A-A"），需维护关键词词表；纯详图易误判，允许归入 `detail` 兜底。

---

### 工作块二：跨视图 z 恢复 — MVP（仅剖面标高）

> **渐进路线**：本块（B-02~B-05）只吃剖面，产出真实层高表并替换常量、点亮 gate 的一半证据。**这是 Phase B 最小可用增量**。

#### B-02 剖面标高线抽取
- **描述**：从剖面图识别标高符号（±0.000、层高标注、标高三角旗）与其数值，解析出有序「楼层 → 绝对标高（m）」序列。确定性方法：ezdxf 抽水平线 + 标高文字 → 数值配准；文字走 PaddleOCR。详见 z 恢复详设文档。
- **交付物**：`extract_section_levels(drawing) -> list[LevelMark{elevation_m, label, confidence, source_ref}]`。
- **涉及/新增文件**：新增 `apps/api/core/model3d/section_level_extractor.py`。
- **依赖**：B-01。
- **开源件**：ezdxf、PaddleOCR（Apache 2.0）、PyMuPDF（AGPL/商业双许可，栈内已用）。
- **工作量**：L（5）
- **验收**：规范标注剖面上，标高数值抽取准确率 ≥ 90%，层序正确率 ≥ 95%；每个标高带 source 引用（图元 handle / 坐标）；识别不到时返回空 + 明确 reason。
- **风险**：标高符号样式多样（旗标 / 圆圈 / 纯文字），需覆盖主流样式；负标高（地下室 -3.600）符号方向易错。

#### B-03 真实标高表数据模型 + 迁移
- **描述**：定义「每层真实标高表」持久化结构：`model_story_levels`（scope_key、story_key、elevation_bottom_m、story_height_m、source=section|elevation|estimated、confidence、evidence_ref）。为 z 恢复各来源提供统一落库。
- **交付物**：迁移脚本 + Repository 读写函数。
- **涉及/新增文件**：新增 `apps/api/migrations/017_model_story_levels.sql`（编号顺延 Phase A）；扩展 `services/model_story.py` 增读写。
- **依赖**：B-02。
- **开源件**：PostgreSQL 16。
- **工作量**：M（2）
- **验收**：迁移可正反向执行；表含 source / confidence / evidence_ref 三列，能区分实测与估算；读写函数有单测。
- **风险**：需与 Phase A / 现有 `model_story_annotations`（015）字段不重叠，避免概念混淆。

#### B-04 替换硬编码层高常量为标高表驱动
- **描述**：把 `model_lod.py` / `model_story.py` 的 `DEFAULT_STORY_HEIGHT_M = 4.5` 从「默认即用」改为「**兜底且显式标注 estimated**」。有标高表时用实测层高；无时回落 4.5 并写 `estimated=true` + 低置信度 + note。
- **交付物**：标高解析优先级链（section > elevation > default），provenance 贯穿。
- **涉及/新增文件**：`services/model_lod.py`（`_resolve_height`）、`services/model_story.py`、`services/model_builder.py`（消费层高处）。
- **依赖**：B-03。
- **开源件**：无。
- **工作量**：M（3）
- **验收**：有剖面时层高来自实测且 note 为空；无剖面时 `notes` 明确含"默认层高 4.5m 估算"、confidence ≤ 0.55；现有 `test_model_lod.py` / `test_model_story.py` 全绿并新增估算路径断言。
- **风险**：多处引用常量，须全量 grep 替换（`grep -rn "4.5\|DEFAULT_STORY_HEIGHT_M"`）不遗漏；不可回归破坏 Phase A 展示级默认行为。

#### B-05 点亮 cross_view_match gate（剖面证据部分）
- **描述**：当标高表来源含 `section` 且层序与平面楼层数一致时，置 `ModelScopeEvidence.has_cross_view_match = True`（剖面单证据即可满足该 gate 的 MVP 判定；立面配准在 B-09 增强）。
- **交付物**：证据装配逻辑：由 z 恢复结果驱动 `has_cross_view_match` / `has_dimensions`。
- **涉及/新增文件**：`services/model_lod.py`（证据装配入口，通常在 `model_builder.py` 组装 `ModelScopeEvidence` 处）、`services/model_builder.py`。
- **依赖**：B-04。
- **开源件**：无。
- **工作量**：M（2）
- **验收**：含剖面样本经全链路后 `cross_view_match` gate 通过、LOD 升至 300（其余 gate 满足前提下）；无剖面样本该 gate 保持 False；`test_model_lod.py` 增用例。
- **风险**：gate 判定过松会虚高 LOD，须要求「层序一致」而非仅「有剖面」。

---

### 工作块三：跨视图 z 恢复 — 立面洞口

#### B-06 立面洞口识别 + 尺寸
- **描述**：从立面图识别门窗洞口矩形与其尺寸 / 标高标注 → 窗台高、门顶高、洞口宽高、檐口 / 女儿墙高。确定性：ezdxf 抽闭合矩形 + 尺寸线关联；洞口类型借现有 `element_recognizer` 门窗规则。详见 z 恢复详设文档。
- **交付物**：`extract_elevation_openings(drawing) -> list[Opening{kind, sill_h_m, head_h_m, width_m, height_m, axis_ref, confidence}]`。
- **涉及/新增文件**：新增 `apps/api/core/model3d/elevation_opening_extractor.py`；复用 `core/model3d/element_recognizer.py`。
- **依赖**：B-01。
- **开源件**：ezdxf、PaddleOCR。
- **工作量**：L（5）
- **验收**：规范立面上洞口计数召回 ≥ 90%，窗台 / 门顶高数值误差 ≤ 标注精度；无尺寸标注的洞口返回几何但标 `dimension_missing`。
- **风险**：立面重复构件（成排窗）需去重并与轴网对位；装饰线脚易误判为洞口。

#### B-07 构件截面表
- **描述**：汇集剖面 / 立面 / 详图给出的构件真实截面：梁高 × 宽、板厚、墙厚、柱截面、管径 → 「构件类型 → 截面参数」表，替换 `model_elements.py` / `model_builder.py` 的默认截面（梁 0.6 / 板 0.12 / 管 0.1）。缺证据回落默认并标估算。
- **交付物**：`build_component_sections(...) -> dict[component_type, Section{h,w,thickness,diameter,source,confidence}]` + 持久化。
- **涉及/新增文件**：新增 `apps/api/services/model_component_sections.py`；迁移 `migrations/018_model_component_sections.sql`；改 `services/model_elements.py`、`services/model_builder.py` 消费截面表。
- **依赖**：B-02、B-06。
- **开源件**：PostgreSQL。
- **工作量**：L（4）
- **验收**：有剖面 / 详图时梁高 / 板厚来自实测；无时回落且 `estimated=true`；截面进入挤出后模型几何随之变化（可视验证）。
- **风险**：同类构件多种截面（不同跨梁高不同），MVP 允许「按楼层 / 按类型取代表值」，差异化留 Phase C。

---

### 工作块四：跨视图 z 恢复 — 全三视图配准

#### B-08 轴网锚点提取
- **描述**：从各视图提取轴网编号（1/2/3、A/B/C）与轴线坐标，作为平面↔剖面↔立面对齐锚点（比纯 CV 图像匹配鲁棒）。确定性：ezdxf 抽轴线 + 轴号圆圈文字。详见 z 恢复详设文档。
- **交付物**：`extract_grid_anchors(drawing) -> GridSystem{axes_x:[{label,coord}], axes_y:[...], confidence}`。
- **涉及/新增文件**：新增 `apps/api/core/model3d/grid_anchor_extractor.py`。
- **依赖**：B-01。
- **开源件**：ezdxf、PaddleOCR。
- **工作量**：L（4）
- **验收**：规范图轴网编号识别准确率 ≥ 95%，横 / 纵轴分辨正确；轴号缺失时返回可用轴线几何 + 标 `unlabeled`。
- **风险**：附加轴（1/A 分轴）、圆弧轴网需兼容；中文轴号变体。

#### B-09 三视图配准与 z 装配
- **描述**：以轴网锚点 + 标注尺寸把平面（xy）、剖面（z + 一向）、立面（z + 另一向）配准到统一坐标系，合成完整 z 信息，增强 `cross_view_match` 为「多视图一致」强证据。冲突时按置信度择优并记录分歧。详见 z 恢复详设文档。
- **交付物**：`register_views(plan, sections, elevations) -> ZRegistration{levels, sections, axis_map, consistency_score, conflicts}`。
- **涉及/新增文件**：新增 `apps/api/services/cross_view_registration.py`。
- **依赖**：B-05、B-07、B-08。
- **开源件**：numpy（BSD）做坐标变换；无重型 CV 依赖。
- **工作量**：L（5）
- **验收**：三视图齐备样本配准一致性 ≥ 0.9；轴网对齐后剖面标高与立面洞口标高互校误差在阈内；`conflicts` 非空时不静默取一，显式记录。
- **风险**：视图比例不一致 / 局部剖需归一化；配准失败必须优雅降级到 B-05 剖面单证据结果，不可整链崩。

#### B-10 cross_drawing.py 扩展整合
- **描述**：把 `analyze_batch` 从「仅平面间关联」扩展为「吃全套图（平面 + 剖 + 立）→ 调 z 恢复流水线 → 产出标高表 + 截面表 + 配准结果」，作为套图审图 / 建模的统一入口。
- **交付物**：扩展后的 `analyze_batch`（新增 z 恢复分支，向后兼容纯平面批次）。
- **涉及/新增文件**：`apps/api/core/ai_review/cross_drawing.py`；串联 B-02/06/08/09 各提取器。
- **依赖**：B-09。
- **开源件**：无。
- **工作量**：M（3）
- **验收**：整套图批次经 `analyze_batch` 一次跑出标高表 + 截面表 + LOD 判定；纯平面批次行为不回归（现有 `test_cross_drawing.py` 全绿）。
- **风险**：`analyze_batch` 已有多职责，需保持函数聚焦，抽子函数避免超 800 行 / 函数超 50 行。

#### B-11 置信度与降级框架（贯穿性）
- **描述**：统一 z 恢复各来源的置信度模型与降级策略：来源优先级（section > elevation+plan 配准 > elevation only > default），每个几何量携带 `source` / `confidence` / `estimated` / `evidence_ref`，模型输出与前端标签一致标注「实测 / 估算」。**贯穿 B-02~B-10，独立成任务确保不遗漏。**
- **交付物**：`Provenance` 值对象 + 降级决策函数 + 统一 note 文案。
- **涉及/新增文件**：新增 `apps/api/core/model3d/provenance.py`；各提取器 / builder 接入。
- **依赖**：B-04（与之并行细化）。
- **开源件**：无。
- **工作量**：M（3）
- **验收**：任一几何量都能回答"从哪张图哪个图元来的、实测还是估算、置信几何"；缺证据路径 100% 标 `estimated=true`；前端 LOD 卡片文案与 provenance 一致（验收总标准 2 的落点）。
- **风险**：置信度阈值需与 `model_lod.py` 现有 gate 语义协调，避免双套标准。

---

### 工作块五：构件拓扑规则（确定性）

#### B-12 门窗-墙从属规则
- **描述**：开洞图元（门窗）中心落在墙线段内 → 建立「洞口 host = 墙」从属关系，供 IFC `IfcRelVoidsElement` / `IfcRelFillsElement` 与算量扣减。
- **交付物**：`resolve_opening_host(openings, walls) -> list[HostRel{opening_id, wall_id, confidence}]`。
- **涉及/新增文件**：新增 `apps/api/core/model3d/topology_rules.py`（本块共用）。
- **依赖**：B-06。
- **开源件**：shapely（BSD）做点 / 线 / 面几何判定。
- **工作量**：M（3）
- **验收**：样本上门窗归属正确率 ≥ 95%；跨墙 / 悬空洞口标 `orphan` 而非硬塞；有单测覆盖边界（洞口在墙端 / 转角）。
- **风险**：墙线为双线时需先合成墙中心线（可复用现有几何提取）。

#### B-13 梁-柱支承规则
- **描述**：梁端点落在柱截面内（或近邻阈值内）→ 建立「梁支承于柱」关系，供拓扑闭合与荷载语义。
- **交付物**：`resolve_beam_support(beams, columns) -> list[SupportRel{beam_id, column_id, end, confidence}]`。
- **涉及/新增文件**：`apps/api/core/model3d/topology_rules.py`。
- **依赖**：B-07（需柱 / 梁截面与位置）。
- **开源件**：shapely。
- **工作量**：M（3）
- **验收**：梁两端支承识别率 ≥ 90%；仅一端搭柱 / 悬挑梁正确标注；单测覆盖近邻阈值边界。
- **风险**：阈值过大误连，过小漏连；需按柱截面尺寸自适应阈值。

#### B-14 板-梁托承规则
- **描述**：板边界与梁轴线对齐（在带宽阈值内）→ 建立「板托承于梁」关系，供板净体积扣梁占位、模板面积计算。
- **交付物**：`resolve_slab_support(slabs, beams) -> list[SupportRel{slab_id, beam_ids, confidence}]`。
- **涉及/新增文件**：`apps/api/core/model3d/topology_rules.py`。
- **依赖**：B-07。
- **开源件**：shapely。
- **工作量**：M（3）
- **验收**：板边对齐梁识别率 ≥ 90%；悬挑板 / 无梁楼盖正确降级标注；单测覆盖对齐带宽边界。
- **风险**：无梁楼盖 / 板柱结构无梁可托，规则须允许"板支于墙 / 柱"扩展位（YAGNI，先支持有梁情形）。

#### B-15 拓扑图装配 + 集成 model_builder
- **描述**：把 B-12~B-14 关系汇成构件拓扑图（节点=构件，边=从属 / 支承），落库并注入 `model_builder`，为 QTO 提供扣减依据与几何一致性检查（点亮 `stable_component_boundaries` / `geometry_consistent` gate 的确定性证据）。
- **交付物**：`build_topology_graph(...) -> TopologyGraph` + 迁移 + builder 接入。
- **涉及/新增文件**：新增 `apps/api/services/model_topology.py`；迁移 `migrations/019_model_topology.sql`；改 `services/model_builder.py`。
- **依赖**：B-12、B-13、B-14。
- **开源件**：networkx（BSD）可选，做图一致性检查。
- **工作量**：M（3）
- **验收**：拓扑图能查「某梁支承哪些板 / 落在哪些柱」；孤立构件率上报；相关 gate 由拓扑证据驱动，`test_model_lod.py` 增用例。
- **风险**：图规模随构件数增长，需限制查询深度（对齐 `graph_query_depth_max`）。

---

### 工作块六：IFC-QTO 算量

> 依赖 Phase A 的 `model_ifc_builder.py`。若未就位，可先建最小 IfcOpenShell stub 并行。

#### B-16 混凝土净体积量集
- **描述**：建模顺带为墙 / 柱 / 梁 / 板写 `Qto_WallBaseQuantities` / `Qto_ColumnBaseQuantities` / `Qto_BeamBaseQuantities` / `Qto_SlabBaseQuantities` 的 `NetVolume`，用拓扑关系做交叠扣减（梁 - 柱重叠、板 - 梁重叠）得净体积。
- **交付物**：`write_concrete_quantities(ifc_model, elements, topology)`。
- **涉及/新增文件**：新增 `apps/api/services/model_qto.py`；扩展 `services/model_ifc_builder.py`（Phase A）。
- **依赖**：B-15、**A-xx（IFC 建模器交付）**。
- **开源件**：IfcOpenShell（LGPL-3.0，`ifcopenshell.api`）、qto_buccaneer（MIT）参考量集写法。
- **工作量**：L（4）
- **验收**：导出 IFC 用 BlenderBIM / That Open 打开可见量集；净体积经手算校验误差 ≤ 2%；未扣减的毛体积同时保留供对比。
- **风险**：扣减重复计算 / 遗漏；变截面构件体积近似需标注。

#### B-17 模板量（侧面积）
- **描述**：为梁 / 板 / 墙 / 柱写 `GrossSideArea` / 模板接触面积，扣除构件相交面与楼板顶底不支模面，得可施工模板量。
- **交付物**：`write_formwork_quantities(ifc_model, elements, topology)`。
- **涉及/新增文件**：`apps/api/services/model_qto.py`。
- **依赖**：B-16。
- **开源件**：IfcOpenShell、shapely。
- **工作量**：L（4）
- **验收**：模板侧面积经典型构件手算校验误差 ≤ 3%；相交扣除逻辑有单测；输出区分"接触模板面 / 自由面"。
- **风险**：模板计算规则地区差异大，MVP 采用「毛侧面积 - 相交面」通用口径，细则留后续可配置。

#### B-18 钢筋量回填 IFC
- **描述**：调用现有 `rebar_calculator.py`（`calc_anchor_lengths` / `optimize_cutting`，GB50010）得钢筋量 → 回填 IFC（`IfcReinforcingBar` 或量集属性），与混凝土构件关联。抗震系数 / 搭接系数取现有引擎业务参数。
- **交付物**：`write_rebar_quantities(ifc_model, elements, rebar_params)`。
- **涉及/新增文件**：`apps/api/services/model_qto.py`；只读复用 `core/economic/rebar_calculator.py`（不改其算法）。
- **依赖**：B-16。
- **开源件**：IfcOpenShell；rebar_calculator 为自有。
- **工作量**：M（3）
- **验收**：给定配筋输入，IFC 中钢筋量与 `rebar_calculator` 直算一致；抗震 / 搭接系数取自引擎参数（非硬编码）；无配筋数据时构件标 `rebar_missing` 不臆造。
- **风险**：从图纸自动恢复配筋本身是难题（Phase B 不做自动识配筋），本任务只打通「已知配筋 → IFC」通道，配筋来源可为人工录入 / 已有算例。

#### B-19 QTO 汇总 API + 数据模型
- **描述**：从 IFC 量集聚合项目 / 单体 / 楼层级工程量汇总（混凝土 m³ / 模板 m² / 钢筋 t），落库并出 REST 接口，供前端展示与创效引用。
- **交付物**：`GET /api/v1/project-models/{id}/quantities` + 汇总表。
- **涉及/新增文件**：扩展 `apps/api/routers/project_models.py`；新增 `services/model_qto_summary.py`；迁移 `migrations/020_model_quantities.sql`。
- **依赖**：B-16、B-17、B-18。
- **开源件**：IfcOpenShell、FastAPI。
- **工作量**：M（3）
- **验收**：接口返回统一信封（success/data/error/meta），含各构件量 + 置信度 + 实测/估算标记；分楼层 / 分单体可下钻；有集成测试。
- **风险**：量集缺失构件需在汇总中显式计入"未覆盖"，不可静默漏量。

---

### 工作块七：创效联调

#### B-20 QTO → 创效激励打通
- **描述**：由 QTO 汇总（优化前后混凝土 / 钢筋 / 模板量差）自动生成创效提案草稿：净节约额 = 原方案量价 - 优化方案量价 - 成本，预填 `incentive_proposals`，用户确认后进入现有三方签字流程。
- **交付物**：`POST /api/v1/project-models/{id}/quantities/to-proposal`（生成草稿）。
- **涉及/新增文件**：扩展 `routers/project_models.py` 或 `routers/incentive.py`；复用 `services/bonus_calculator.py`。
- **依赖**：B-19。
- **开源件**：无（复用现有创效栈）。
- **工作量**：M（3）
- **验收**：QTO 差值可一键生成 `net_saving > 0` 的提案草稿；铁三角比例 / 签字顺序等硬约束不被绕过（沿用现有校验）；生成动作写 `audit_logs`。
- **风险**：不得让自动草稿绕过「多方案 ≥ 2」「经济师签字」等硬约束；草稿须人工确认方可提交。

---

### 工作块八：测试与里程碑

#### B-21 z 恢复算法单元测试
- **描述**：为 B-02/06/08/09/11 各提取器与配准器建单测 + 合成 DXF 夹具（含 / 不含剖面 / 立面 / 轴网多组）。
- **交付物**：`tests/test_section_level_extractor.py`、`test_elevation_opening_extractor.py`、`test_grid_anchor_extractor.py`、`test_cross_view_registration.py`、`test_provenance.py` + 夹具。
- **涉及/新增文件**：`apps/api/tests/`；夹具 `apps/api/tests/fixtures/`。
- **依赖**：B-11。
- **开源件**：pytest、ezdxf（造夹具）。
- **工作量**：L（4）
- **验收**：z 恢复核心分支 100% 覆盖，含降级 / 冲突 / 缺证据路径；夹具可复现验收样本。
- **风险**：合成 DXF 夹具工作量易低估，尽早备真实脱敏样本。

#### B-22 拓扑规则测试
- **描述**：为 B-12~B-15 建单测，覆盖正常 / 悬挑 / 孤立 / 无梁等边界。
- **交付物**：`tests/test_topology_rules.py`、`test_model_topology.py`。
- **涉及/新增文件**：`apps/api/tests/`。
- **依赖**：B-15。
- **开源件**：pytest、shapely。
- **工作量**：M（2）
- **验收**：拓扑规则 100% 分支覆盖；孤立 / 降级路径有断言。
- **风险**：无。

#### B-23 QTO 与创效联调测试
- **描述**：为 B-16~B-20 建单测 + 集成测试，含 IFC 量集写入校验、汇总接口、创效草稿生成，用离线 mock（无外部服务）。
- **交付物**：`tests/test_model_qto.py`、`test_project_models_quantities.py`、`test_qto_to_proposal.py`。
- **涉及/新增文件**：`apps/api/tests/`。
- **依赖**：B-20。
- **开源件**：pytest、IfcOpenShell。
- **工作量**：M（3）
- **验收**：量集手算校验、接口信封、创效草稿硬约束全覆盖；CI 可离线跑。
- **风险**：IfcOpenShell 在 CI 镜像需就位（与 Phase A 共用依赖）。

#### B-24 里程碑 Demo：整套图 → 真实几何 → 算量 → 创效（E2E）
- **描述**：端到端串联全 Phase B 能力的验收 Demo：上传一套含剖面标注的整套图 → z 恢复出真实层高 / 板厚 / 梁高 → 拓扑闭合 → IFC 算出混凝土量 + 钢筋量 + 模板量 → 生成创效测算草稿。对照「缺剖面」样本验证显式估算降级。
- **交付物**：E2E 脚本 + Demo 样本集 + 验收报告（对齐 Phase B 验收总标准 1–5）。
- **涉及/新增文件**：`apps/api/tests/e2e/test_phase_b_demo.py` 或 `apps/web/tests/e2e/`；Demo 样本 `docs/source/` 或 fixtures。
- **依赖**：B-20、B-21、B-22、B-23。
- **开源件**：Playwright（Apache 2.0）、pytest。
- **工作量**：L（4）
- **验收**：含剖面套图跑出 LOD300 + 真实几何 + 完整量 + 创效草稿；缺剖面套图跑出带 `estimated` 标记的降级结果且 gate 不虚高；报告逐条勾对验收总标准。
- **风险**：真实脱敏整套图样本获取是前置阻塞项，需项目侧尽早提供。

---

## 3. 依赖图（DAG）

```
B-01 图种判别 ──┬─> B-02 剖面标高 ──> B-03 标高表模型 ──> B-04 替换常量 ──> B-05 点亮gate(剖面)
                │                                            │                    │
                │                                            └──> B-11 置信降级 <──┘
                │                                                    (贯穿 02~10)
                ├─> B-06 立面洞口 ──┬─> B-07 截面表 ────────────────┐
                │                   │                               │
                └─> B-08 轴网锚点 ──┴─> B-09 三视图配准 <── B-05, B-07, B-08
                                            │
                                            └─> B-10 cross_drawing 整合

B-06 ──> B-12 门窗-墙 ──┐
B-07 ──> B-13 梁-柱 ────┼─> B-15 拓扑装配 ──┐
B-07 ──> B-14 板-梁 ────┘                   │
                                            v
Phase A(model_ifc_builder) ──> B-16 混凝土量 ──> B-17 模板量
                                    │              │
                                    ├─> B-18 钢筋回填
                                    │
                                    └──> B-19 QTO汇总API ──> B-20 创效打通

测试：B-11→B-21 | B-15→B-22 | B-20→B-23 | (B-20,21,22,23)→B-24 里程碑Demo
```

### 依赖表（前置）

| 任务 | 前置 | 任务 | 前置 |
|---|---|---|---|
| B-01 | — | B-13 | B-07 |
| B-02 | B-01 | B-14 | B-07 |
| B-03 | B-02 | B-15 | B-12,B-13,B-14 |
| B-04 | B-03 | B-16 | B-15, A-xx(IFC建模器) |
| B-05 | B-04 | B-17 | B-16 |
| B-06 | B-01 | B-18 | B-16 |
| B-07 | B-02,B-06 | B-19 | B-16,B-17,B-18 |
| B-08 | B-01 | B-20 | B-19 |
| B-09 | B-05,B-07,B-08 | B-21 | B-11 |
| B-10 | B-09 | B-22 | B-15 |
| B-11 | B-04（并行细化） | B-23 | B-20 |
| B-12 | B-06 | B-24 | B-20,B-21,B-22,B-23 |

---

## 4. 并行分组建议（按里程碑迭代）

**Sprint 1（供料 + MVP，~2 周）**：B-01 → B-02 → B-03 → B-04 → B-05。目标：**剖面驱动真实层高、点亮 gate 一半证据**（首个可演示增量）。B-11 同步启动骨架。

**Sprint 2（立面 + 配准，~3 周，两路并行）**：
- 路 A：B-06 → B-07（立面洞口 + 截面表）
- 路 B：B-08 → B-09 → B-10（轴网 + 三视图配准 + 整合）
- 收口：B-11 完成。里程碑：**全三视图 z 恢复可用**。

**Sprint 3（拓扑，~2 周，三路并行）**：B-12 / B-13 / B-14 并行 → B-15 收口。

**Sprint 4（算量，~2.5 周）**：B-16 → (B-17 ∥ B-18) → B-19 → B-20。前置：Phase A `model_ifc_builder.py` 就位（否则先 stub）。

**Sprint 5（测试 + Demo，~2 周）**：B-21 / B-22 / B-23 并行（各随对应功能滚动进行更佳）→ B-24 里程碑 Demo 验收。

> 测试任务（B-21~B-23）建议**随功能同步 TDD**，不集中到最后；此处独立列出仅为工作量与验收归集。

---

## 5. 关键里程碑

| 里程碑 | 完成任务 | 可演示成果 |
|---|---|---|
| **M1 剖面驱动真实层高** | B-01~B-05 | 含剖面图 → 模型层高非默认常量，`cross_view_match` gate 点亮 |
| **M2 全三视图 z 恢复** | B-06~B-11 | 平面 + 剖 + 立配准出真实标高表 + 构件截面表，估算路径显式降级 |
| **M3 构件拓扑闭合** | B-12~B-15 | 门窗从属 / 梁柱 / 板梁关系图，几何一致性 gate 由拓扑驱动 |
| **M4 IFC 算量出量** | B-16~B-19 | IFC 出混凝土净体积 + 模板量 + 钢筋量，QTO 汇总接口可下钻 |
| **M5 创效闭环 Demo** | B-20~B-24 | 整套图 → 真实几何 → 算量 → 创效测算草稿，E2E 通过验收总标准 |

---

## 6. 汇总

- **任务总数**：24（B-01 ~ B-24）
- **工作量合计**：约 **80 人天**（S≈1 / M≈2.5 / L≈4.5 折算；含测试与联调）。按 2 人并行约 8–10 周主开发，留缓冲对齐 3–5 月窗口。
- **开源件与许可**：IfcOpenShell（LGPL-3.0）、qto_buccaneer（MIT）、ezdxf、PaddleOCR（Apache 2.0）、shapely / numpy / networkx（BSD）、pytest / Playwright（Apache 2.0）。**均可商用**，无 Phase C 的符号识别非商用许可陷阱。
- **最大技术风险**：**跨视图 z 恢复无现成开源库、强依赖图纸剖 / 立面标注质量**（B-02/06/08/09）。缓解：严格执行「置信度 + 显式降级」（B-11），缺证据标 `estimated` 绝不伪装实测；尽早取真实脱敏整套图样本（B-24 前置阻塞项）。次级风险：Phase A `model_ifc_builder.py` 交付时点影响 B-16~B-19，缓解为最小 stub 并行。

> 本文档中「详见 z 恢复详设文档」处，待 `docs/CROSS_VIEW_Z_RECOVERY_DESIGN.md` 落地后回链细化算法参数（标高符号样式表、配准阈值、置信度公式）。
