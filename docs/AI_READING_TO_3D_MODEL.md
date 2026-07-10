# AI 读图 → 三维工程模型：深度调研与落地方案

> 版本 V1.0 | 2026-07-10 | 面向「超级工程建模」核心亮点的技术路线决策
>
> 结论先行：**「AI 读图出三维模型」靠谱，但唯一靠谱的形态是「AI + 确定性混合」——绝不是 VLM 端到端。** 你们现有系统已是这条路的雏形，本方案给出把它做深、做到算量级/BIM 级的完整路径、可商用的开源选型，以及诚实的能力边界。

---

## 一、核心结论：靠谱吗？

**分级回答（对齐你「多目标、分阶段演进」的定位）：**

| 目标精度 | AI 读图靠谱度 | 关键依据 |
|---|---|---|
| **展示/审查级**（体量+构件示意+贴图） | ✅ 非常靠谱，且能大幅升级 | 你现有的贴图堆叠+挤出已经能出，换成合规 IFC 后更专业 |
| **算量/成本创效级**（真实几何+构件量） | ✅ 靠谱，**但必须走混合路线** | 几何/计数交确定性引擎，IFC 自带工程量；VLM 只读表 |
| **施工级 BIM/碰撞** | ⚠️ 半自动，**必须人工审校闭环** | 行业无全自动出生产级 BIM 的方案，效率提升现实值 25–30% |

**一句话本质判断：**
- **「VLM 看图直接吐出结构化模型」= 今天不靠谱。** 有硬证据：专用基准 AECV-Bench 上，最强模型数门 39%、数窗 34%，符号计数没有一个模型 > 55%；空间/尺寸推理近乎随机。VLM 会「基于模式编一个看似合理的数」，而不是系统性枚举+校验。**凡涉及"数出来/量出来/对齐坐标"，VLM 都会幻觉。**
- **「AI 作为组件嵌入确定性流水线」= 非常靠谱，且是学术界与工业界共同的方向。** 代表范式 CAD-Assistant（ICCV 2025）：VLM 做规划/语义，几何工具做精确执行与校验，形成 agent 闭环。

> 你选的「AI+确定性混合」正是唯一正确答案。下面是把它落到生产的完整方案。

---

## 二、目标架构：AI+确定性混合的四层分工

核心原则：**每一层只做它可靠的事，不确定的显式标注置信度，交给下一层或人工。**

```
┌─────────────────────────────────────────────────────────────┐
│ L4 成果呈现层  Web 三维(That Open/Fragments) + QTO 算量 + 人工审校工作台 │
├─────────────────────────────────────────────────────────────┤
│ L3 结构装配层  构件→拓扑(规则) → IFC(IfcOpenShell) → 跨视图 z 恢复(自研)  │
├─────────────────────────────────────────────────────────────┤
│ L2 符号语义层  符号识别(CADTransformer→VecFormer) + VLM 读表/判专业(Qwen3-VL)│
├─────────────────────────────────────────────────────────────┤
│ L1 几何真值层  ezdxf / 矢量 PDF 确定性提取(精确坐标·尺寸·计数)             │
└─────────────────────────────────────────────────────────────┘
```

**与你现有代码的映射（哪些保留、哪些新增）：**

| 层 | 职责 | 现有资产（保留/增强） | 需新增 |
|---|---|---|---|
| L1 几何真值 | 从 DXF/矢量PDF 提精确图元 | `core/model3d/geometry_extractor.py` ✅ 已做得很好 | DXF→SVG/JSON 预处理器（喂给 L2 模型） |
| L2 符号语义 | 图元→构件类型/符号；文字→语义 | `element_recognizer.py`（确定性规则）✅ + `yolo_detector.py` + PaddleOCR | 符号 spotting 模型；VLM 读表/判专业微服务 |
| L3 结构装配 | 构件→拓扑→IFC；恢复真实 z | `model_story.py` / `model_semantics.py` / `model_annotations.py` / `cross_drawing.py` / IfcOpenShell（栈内已有） | IFC 程序化建模器；跨视图 z 恢复；构件拓扑规则 |
| L4 成果呈现 | 三维渲染+算量+审校 | `pages/model/ProjectModel/`（three.js）+ `rebar_calculator.py` + 三审文化 | That Open/Fragments 加载器；IFC-QTO；语义审校队列（已有雏形 `SemanticReviewQueue.tsx`） |

**关键洞察：你缺的从来不是「BIM 生成端」（IfcOpenShell 是现成轮子），而是「DXF→语义构件」这一段——而这段恰恰没有开箱开源，必须自建。** 好消息是你已经用确定性规则做出了雏形（`element_recognizer.py`），方向完全正确。

---

## 三、关键技术选型（含许可证 / 开箱度对比）

### 3.1 ⚠️ 最重要的坑：符号识别的许可证陷阱

**精度最高的一批方法是「非商用」，不能进你的商业创效平台。** 这是最容易被忽视、代价最高的坑。

| 方法 | 年份 | FloorPlanCAD PQ | 许可证 | 可商用 | 权重 | 开箱(1-5) | 判断 |
|---|---|---|---|---|---|---|---|
| **CADTransformer** | CVPR 2022 | 68.5 | **MIT** | ✅ | ✅ | 3 | **短期首选：唯一 MIT+代码+权重齐全** |
| GAT-CADNet | CVPR 2022 | 73.7 | 无官方码 | ⚠️ | ❌ | 1 | 仅参考其拓扑思路 |
| SymPoint V1 | ICLR 2024 | 83.3 | **非商用** | ❌ | ✅ | 3 | ⛔ 仅作内部对标天花板 |
| SymPoint V2 | 2024 | 90.1 | 未声明 | ❌ | ✅ | 2 | ⛔ 法务出局 |
| CADSpotting | 2024 | 88.9 | 未释放 | ❌ | ❌ | 1 | ⛔ 无码 |
| **VecFormer** | NeurIPS 2025 | **91.1** | **Apache 2.0** | ✅ | ❌暂未释放 | 2 | **中期升级目标：精度+合规唯一交集** |
| ArchCAD-400K/DPSS | NeurIPS 2025 | 新SOTA | 待确认 | ? | 部分 | 1 | 关注其数据集（规模 26×，自动标注引擎） |

**选型结论：`CADTransformer(MIT)` 现在跑通管线 → `VecFormer(Apache 2.0)` 权重释放后升级；SymPoint 系只当内部评测对标，绝不写进产品代码。**

**三条硬约束（必须知道）：**
1. **所有模型只吃 SVG/JSON，没有一个直接吃 DXF/DWG** → 你必须自建 `DXF→SVG/图元JSON` 预处理（图层/块 INSERT 展开、坐标归一化），ezdxf 可胜任。
2. **所有 SOTA 都只在建筑平面图（FloorPlanCAD）刷分** → 结构（柱梁板）/机电管线/装修 + 中文施工图**几乎零现成权重，必须自建数据集**。借鉴 ArchCAD-400K「用 CAD 内在图层/块属性自动标注」冷启动。
3. **符号识别 ≠ OCR**，模型不处理中文文字，文字仍走 PaddleOCR。

### 3.2 VLM 选型（读表/判专业/跨图语义——它可靠的事）

| 用途 | 云端方案 | 本地私有化方案（图纸涉密推荐） |
|---|---|---|
| 图名/说明/专业判别/跨图语义 | Qwen2.5-VL-72B / GPT-4o / Claude | **Qwen3-VL-8B（Apache 2.0，单卡 16–24GB）** 或稳妥版 Qwen2.5-VL-7B |
| 标题栏/门窗表/钢筋表结构化抽取 | 叠加 PaddleOCR-VL | **PaddleOCR-VL-0.9B**（极轻、中文/表格 SOTA、Apache 生态） |
| 精确计数/坐标/尺寸 | ⛔ 禁止 | ⛔ 禁止 → 交 L1 确定性引擎 |

**VLM 能可靠做 / 不能可靠做（硬证据）：**

- ✅ **可靠**：读文字（OCR ~0.95）、表格字段抽取（TEDS ~90+）、专业判别（分类强项）、基于文字标注的对象识别（被标注房间 82–91%）、对比已标注数值（~0.80）、生成候选+置信度当"提示器"。
- ❌ **不可靠**：符号计数（门 39%/窗 34%，最强 <55%）、组合计数、精确坐标/尺寸（SIRI-Bench 最好仅 31% 落在合理误差）、端到端 QTO、无文字的纯图形符号、大幅面细节（闭源模型强制降采样：Claude 最长边 1568px、GPT-4o 短边 768px → A0/A1 图细节进模型前就丢了，必须切图+矢量解析）。

### 3.3 IFC 生成 / 几何 / Web（成熟轮子，直接复用）

| 环节 | 方案 | 许可 | 成熟度 | 与你的接入点 |
|---|---|---|---|---|
| 2D 构件→IFC | **IfcOpenShell `ifcopenshell.api`** | LGPL-3.0 | 生产级事实标准 | 栈内已有；你现有挤出逻辑几乎 1:1 迁移成 `IfcExtrudedAreaSolid` |
| 工程量 QTO | IFC `Qto_*BaseQuantities` + `qto_buccaneer`(MIT) | MIT | 成熟 | 建模顺带写量集，混凝土量/模板量近乎免费 |
| 钢筋算量 | **保留你的 `rebar_calculator.py`**（GB50010） | 自有 | 已实现 | 从图纸自动恢复配筋是另一难题，规则引擎路线正确，结果回填 IFC |
| Web 三维 | **That Open Engine / Fragments(.frag)** | MIT | web BIM 事实标准 | 替换/补充现有 three.js；2GB IFC→80MB frag，百万构件 60fps |

---

## 四、三阶段落地路线（对齐「多目标、分阶段演进」）

### Phase A — 展示级增强（近期，低风险，1–2 个月）

**目标：把现有 2.5D 挤出升级为合规 IFC，拿到标准数据结构和百万构件级 Web 性能。**

1. **IfcOpenShell 替换私有挤出** —— 在 `model_builder.py` 里，把识别出的构件参数（`element_recognizer` 已产出轮廓+位置+类型）经 `ifcopenshell.api` 生成 `IfcWall/Column/Beam/Slab` 挂到 `IfcBuildingStorey`。几乎与现有挤出同构，零外部服务依赖，**风险最低、确定性最高**。
2. **Web 端接 That Open/Fragments** —— IFC 离线转 `.frag`，前端 `ProjectModel` 换 Fragments 加载器；glTF 路线保留作轻量预览。
3. **VLM 语义微服务** —— 新增一个 VLM 引擎（复用你的 `ModelRouter` 多提供商架构！），本地 Qwen3-VL 或云端，负责：读图名/标题栏、判专业、跨图关联提示。**接入你现有的 13 引擎路由体系，天然契合。**
4. **图层规则强化构件识别** —— 中国施工图图层高度规范化（A-WALL/S-COLU/M-/E-），用 ezdxf 图层+块名约定补强 `element_recognizer` 的召回（尤其修当前"柱必须 filled 才识别"的漏检）。

**验收标准**：能对矢量图纸产出合规 IFC，Web 端流畅加载；VLM 读表/判专业准确率实测（用你们自己的图纸样本）。

### Phase B — 算量级（中期，核心攻坚，3–5 个月）

**目标：从模型出真实工程量，支撑创效激励。核心是攻克「跨视图 z 恢复」和「构件拓扑」。**

1. **跨视图 z 向恢复（关键，需自研，无现成库）** —— 这是从"2.5D 挤出"迈向"真三维"的核心，方法明确：
   - 剖面图抽标高线（±0.000/层高标注）→ 每层真实标高表
   - 立面图识别门窗洞口+尺寸标注 → 窗台/门顶/檐口高度
   - **以轴网编号（1/2/3、A/B/C）和标注尺寸为锚点**做平面↔剖面↔立面配准（比纯 CV 图像匹配鲁棒得多——这是建筑图纸独有的对齐锚点）
   - **复用扩展 `cross_drawing.py`**：让它除了平面间关联，再吃剖/立面，产出"每层真实标高表 + 构件截面表"，**替换 `model_lod.py`/`model_builder.py` 里的硬编码常量**（梁 depth=0.6、板 thickness=0.12 等）
2. **构件拓扑规则（确定性，开源界空白，自研最稳）** —— 门窗：开洞图元在墙线内→从属；梁-柱：梁端点落柱截面内→支承；板-梁：板边界与梁轴对齐→托承。契合你 Phase 7 确定性识别路线。
3. **IFC-QTO 算量** —— 建模写 `Qto_*BaseQuantities`，混凝土量（净体积）、模板量（侧面积）近乎免费；钢筋沿用 `rebar_calculator.py` 回填 IFC。与创效激励系统打通，**创效故事闭环**。

**验收标准**：剖面标注规范的图纸，层高/板厚/梁高达到算量可用精度；缺剖面时**显式回落默认值并标注"估算"**，绝不把常量伪装成真实测量。

### Phase C — BIM 级（长期，能力上限，6+ 个月）

**目标：符号识别学习模型上线，覆盖规则触达不到的模糊情形；深化人工审校工作台。**

1. **符号 spotting 模型** —— `CADTransformer(MIT)` 先跑通 DXF→SVG→spotting 全链路验证；关注 `VecFormer` 权重释放后迁移。
2. **自建数据集** —— 用 ArchCAD-400K 的「CAD 图层/块属性自动标注」思路低成本冷启动，覆盖结构/机电/装修 + 中文域（这是必做，无捷径）。
3. **人工审校工作台深化** —— 你已有 `SemanticReviewQueue.tsx` / `DrawingAnnotationQueue.tsx` 雏形，把 3D 重建结果作为**待审成果**接入三审文化：机器出初模+置信度，人审拓扑闭合/命名/规范符合性。

**验收标准**：符号识别在你的专业域数据上超过纯规则；审校工作台把人工返工点收敛。

---

## 五、诚实的能力边界与风险（务必向上管理预期）

1. **全自动出生产级 BIM today 不存在。** 学术峰值 ~94% 实例精度听着高，但 BIM 要求拓扑闭合（墙首尾相接）、语义正确、可结算，单点 6% 错误率在整栋楼累积成大量返工。**定位必须是「AI 出初模 + 平台内人工审改」，而非「一键出 BIM」。** 与你「三审+人工确认」文化天然契合。
2. **VLM 永远不碰精确数字。** 计数/坐标/尺寸/QTO 一律确定性引擎，VLM 只做语义+候选提示。这是有多个 2025-2026 专用基准背书的硬边界。
3. **许可证陷阱。** SymPoint 系（非商用/未声明）法务出局，只能用 CADTransformer(MIT)/VecFormer(Apache)。写进代码前务必核许可。
4. **数据冷启动是最大隐形成本。** 所有开源 SOTA 只覆盖建筑平面图，结构/机电/中文域必须自建数据。低估这点会让"上学习模型"这步无限延期。
5. **z 恢复没有免费午餐。** 无现成库，依赖图纸剖面/立面的标注质量。标注不全时必须显式降级为估算并标置信度。
6. **扫描件是另一个战场（当前不需要）。** 你确认图纸是 DWG+矢量 PDF，所以走矢量主线；若未来有大量扫描件，需另引入 floorplan 重建网络（CubiCasa/HEAT/RoomFormer），那是独立技术栈。

---

## 六、马上可做的第一步（建议）

**最小风险、最快验证价值的切入点 = Phase A 第 1 步：IfcOpenShell 程序化建模 PoC。**

- 拿一张已能识别构件的矢量图，把 `element_recognizer` 的输出喂给一个 `ifcopenshell.api` 建模脚本，产出一个含 `IfcWall/Column/Slab` 的合规 `.ifc`，用 That Open 或 BlenderBIM 打开验证。
- 这一步打通后，你就有了「标准数据底座」，后续算量、Web、审校全部挂在 IFC 上，不再依赖私有网格格式。

**并行可做的低成本实测**：用你们自己的 5–10 张真实图纸，跑一轮本地 Qwen3-VL + PaddleOCR-VL，实测中文标题栏/门窗表抽取准确率，为 VLM 版本定版提供依据。

---

## 附录：关键来源

**矢量 CAD 符号识别**
- FloorPlanCAD (CVPR 2021): https://arxiv.org/abs/2105.07147
- CADTransformer (MIT): https://github.com/VITA-Group/CADTransformer
- VecFormer (Apache 2.0, PQ 91.1): https://github.com/WesKwong/VecFormer / https://arxiv.org/abs/2505.23395
- SymPoint 非商用许可原文: https://github.com/nicehuster/SymPoint/blob/main/LICENSE.txt
- ArchCAD-400K (NeurIPS 2025): https://arxiv.org/abs/2503.22346
- DL+规则混合重建: https://doi.org/10.3390/buildings16051043

**VLM 读图边界**
- AECV-Bench（门/窗计数 <55%）: https://arxiv.org/abs/2601.04819 / https://www.aecfoundry.com/blog/can-ai-really-read-your-building-plans-aecv-bench-gets-a-major-upgrade
- 组合计数失败 (Your VLM Can't Even Count to 20): https://arxiv.org/pdf/2510.04401
- 空间智能 SIRI-Bench: https://arxiv.org/pdf/2506.14512
- CAD-Assistant (ICCV 2025, 混合范式): https://arxiv.org/abs/2412.13810
- Qwen3-VL (Apache 2.0): https://github.com/QwenLM/Qwen3-VL
- PaddleOCR-VL: https://arxiv.org/html/2510.14528v1
- Claude Vision 分辨率上限: https://platform.claude.com/docs/en/build-with-claude/vision

**2D→3D / IFC / Web**
- IfcOpenShell API: https://docs.ifcopenshell.org/ifcopenshell-python/hello_world.html
- 2D→3D 重建综述 (MDPI 2024): https://www.mdpi.com/2673-4117/5/2/42
- Cloud2BIM: https://arxiv.org/html/2503.11498v2
- That Open / Fragments: https://docs.thatopen.com/fragments/getting-started
- qto_buccaneer (MIT QTO): https://github.com/simondilhas/qto_buccaneer
- Scan-to-BIM 是否可全自动: https://www.foundamental.com/perspectives/scan-to-bim-will-it-ever-be-fully-automated-some-thoughts
