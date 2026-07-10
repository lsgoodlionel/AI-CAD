# 跨视图 z 向尺寸恢复 — 详细设计

> 版本：V1.0 | 日期：2026-07-10 | 前置：Phase 6 模型基座、Phase 7 构件级重建（MODEL_PRECISION_BLUEPRINT）
> 定位：超级工程建模从「2.5D 挤出」迈向「真三维」的核心攻坚（对应 `docs/AI_READING_TO_3D_MODEL.md` 第四章 Phase B 第 1 项）
> 目标：用**平面 + 剖面 + 立面**三视图交叉配准，恢复真实层高 / 板厚 / 梁高 / 构件截面 / 洞口标高，
> **替换现有硬编码常量**，并点亮 `model_lod.py` 目前恒为空的 `cross_view_match` 证据门（LOD300 gate）。

---

## 0. 名词与单位约定

- **z 向**：竖直方向（标高方向）。平面图只承载 XY，z 信息只存在于剖面 / 立面 / 标高标注中。
- 所有对外契约字段单位一律 **米（m）**，与 `core/model3d/types.py:FloorElements`（米坐标）保持一致。
- 内部几何原语单位为**页面点（pt）**，经 `element_recognizer._Ctx`（见 `element_recognizer.py:141-157`）换算为米。
- **provenance（溯源）**：沿用 `model_lod.py:152-177 _build_provenance` 的风格——每个恢复出的 z 量都必须携带
  `source`（measured / estimated / default）、`confidence` 与证据链，**绝不把默认常量伪装成真实测量**。

---

## 1. 问题定义

### 1.1 现状：z 全部来自硬编码常量或层序推断

当前模型的所有竖直尺寸都是**猜测**，没有一处来自剖面/立面的真实测量：

| z 量 | 现状取值 | 代码位置 | 问题 |
|------|---------|---------|------|
| 每层真实标高 | 贪心单调选择「标高候选」，缺则 `None`（前端回退层序高度） | `model_builder.py:678 _apply_real_elevations` | 候选仅来自平面图内散落的标高文本（`element_recognizer.py:75 extract_elevations`），无剖面锚定，噪声大 |
| 默认层高 | `DEFAULT_STORY_HEIGHT_M = 4.5` | `model_story.py:12`、`model_lod.py:7` | 每层一律 4.5m |
| 地下层高 | `DEFAULT_BASEMENT_HEIGHT_M = 4.2` | `model_story.py:13` | 拍脑袋 |
| `StoryLevel.height_m` | `4.2 if order<0 else 4.5` | `model_story.py:416` | 从不由真实数据回填 |
| 梁高（截面 depth） | `depth=0.6` 硬编码 | `element_recognizer.py:132`、`model_builder.py` 蓝图 | 全楼所有梁一个高度 |
| 板厚 thickness | `thickness=0.12` 硬编码 | `element_recognizer.py:376,383` | 全楼所有板一个厚度 |
| 管径 dia | `dia=0.1` 硬编码 | `element_recognizer.py:429` | — |
| 设备高度 height | `height=1.5` 硬编码 | `element_recognizer.py:443,452` | — |
| 洞口顶/底标高 | **完全缺失**（当前不识别门窗洞口 z） | — | 立面信息未被消费 |

### 1.2 需恢复的 z 量清单（本设计的产出目标）

1. **每层真实标高** `elevation_m`：F1=±0.000、F2=+4.200……由剖面标高线锚定。
2. **层高** `height_m`：相邻标高之差（本层顶标高 − 本层底标高）。
3. **板厚** `slab_thickness_m`：剖面楼板双线间距，或结构说明表。
4. **梁高（截面）** `beam_depth_m`：剖面梁腹高 / 梁配筋图标注（如 300×600 的 600）。
5. **柱截面高** `column_height_m`：一般等于层高，特殊层（错层、夹层）由剖面修正。
6. **门窗洞口顶/底标高** `sill_m` / `head_m`：立面洞口 + 尺寸标注 → 窗台高、门顶高、檐口高。
7. **层间关系**：楼层 z 序列的连续性（是否错层 / 跃层 / 夹层），供拓扑校验。

---

## 2. 核心方法——以轴网为锚点的跨视图配准

> **关键洞察（来自 AI_READING_TO_3D_MODEL.md:123）**：建筑图纸有一个 CV 图像匹配没有的天然对齐锚点——
> **轴网编号（1/2/3、A/B/C）与标注尺寸**。三视图共享同一套轴号，用轴号做配准键远比图像特征匹配鲁棒。
> 本项目已在**平面↔平面**方向实现了这一机制（`model_elements.py:117-131 register_offset`，按共有轴号位置差中位数平移），
> 本设计将其**推广到平面↔剖面↔立面**，把水平轴号配准扩展为「水平轴号配准 + 竖直标高配准」的二维对齐。

### 2.1 图种判别（Plan / Section / Elevation）

在消费一张图前，必须先判定它是平面、剖面还是立面。判别为**廉价确定性优先、VLM 兜底**三级：

**第一级：图名关键词（零成本，先跑）**——复用 `element_recognizer._is_beam_drawing`（`element_recognizer.py:363`）同款关键词思路：

```python
_SECTION_RE   = re.compile(r"剖面|剖视|\d+-\d+\s*剖|[A-Z]-[A-Z]\s*剖|section", re.I)
_ELEVATION_RE = re.compile(r"立面|正立面|背立面|侧立面|[东南西北]立面|elevation", re.I)
# 平面 = 既非剖面亦非立面，且命中 平面|plan|楼层|层
```

判别键取 `drawings.title` / `drawings.drawing_no`（与 `floor_parser.floor_of_drawing` 一致的字段口径，`floor_parser.py:120`）。

**第二级：几何特征佐证**——剖面/立面的几何签名与平面不同：
- 平面图：轴网为**双向长直线**（`_detect_axes` 同时产出 `axis_x` 与 `axis_y`，`element_recognizer.py:201`）。
- 剖面/立面图：只有一个方向的建筑轮廓 + **一列密集的水平标高线**（大量近水平长线，y 坐标离散），
  且伴随标高符号 `▽` 与三位小数标高文本（`extract_elevations` 命中密度高）。

**第三级：VLM 判别（兜底，仅关键词与几何都无法判定时）**——复用 `drawing_visual_analyzer` 引擎（CLAUDE.md 13 引擎之一）
出图种候选 + 置信度。**遵守硬边界：VLM 只判类别，绝不读取任何尺寸数字**（AI_READING_TO_3D_MODEL.md:145）。

判别结果写入 `drawings` 的一个新字段 `view_kind ∈ {plan, section, elevation, unknown}`（迁移见 §4.4）。

### 2.2 剖面图识别 → 楼层 z 序列（层高、板厚、梁高）

剖面图是 z 恢复的**主锚点**。算法在现有 `extract_elevations`（`element_recognizer.py:75-84`）之上扩展：

**输入**：剖面图的 `DrawingGeometry`（`geometry_extractor.extract_pdf_geometry` / `extract_dxf_geometry`）。

**步骤**：

1. **抽标高线**：遍历 `geom.lines`，取近水平长线（`abs(y0-y1) <= _LINE_STRAIGHT_TOL_PT`，复用 `element_recognizer.py:33` 的容差常量），
   得到候选标高线的 y(pt) 列表。
2. **绑定标高文本**：对每条标高线，在其端部（通常右侧带 `▽` 符号）容差窗口内查找三位小数标高文本，
   复用 `_ELEVATION_RE`（`element_recognizer.py:52`）与 `_find_axis_label`（`element_recognizer.py:182`）的「就近绑定」范式，
   得到 `(y_pt, elevation_m)` 对。**这一步把「图面 y 像素」标定到「真实标高米」**，即建立剖面的 z 标定尺。
3. **拟合 z 标定线性映射**：≥2 个 `(y_pt, elevation_m)` 对 → 最小二乘拟合 `elevation_m = a·y_pt + b`。
   一致性校验：`a` 的符号必须为负（页面 y 向下、标高向上），残差超阈值的离群点剔除后重拟合。
   `a` 的绝对值即剖面的竖直比例尺（米/点），可与平面 `_detect_scale`（`element_recognizer.py:159`）互校。
4. **导出楼层标高表**：把标定后的标高值按升序聚类到楼层（相邻标高差 < 楼板厚级别的并为「同层顶底」），
   逐层 `height_m = 上层底标高 − 本层底标高`。
5. **板厚**：楼层标高附近成对的近水平线（楼板上下皮），双线 y 间距经 z 标定尺换算 → `slab_thickness_m`
   （复用 `_find_parallel_pairs` 的配对思想，`element_recognizer.py:314`，但作用在竖直方向）。
6. **梁高**：楼层顶标高下方下挂的矩形/双线块 → `beam_depth_m`；或从梁配筋图标注文本 `\d{3}[×xX]\d{3}` 正则抽截面
   （宽×高，取高）。

**输出**：`ElevationTable` + `SectionProfileTable`（§3）。

### 2.3 立面图识别 → 洞口标高（窗台/门顶/檐口）

立面图承载**洞口 z**。算法：

1. 用 §2.1 的水平轴号（立面图横向仍是 1/2/3 轴号）+ §2.2 的竖直标高标定尺，建立立面的 (x_pt→轴号, y_pt→标高) 双向标定。
2. **识别洞口**：立面墙面内的闭合矩形（`geom.rects` filled=False 的框）即门窗洞口轮廓
   （复用 `element_recognizer._find_equipment` 的矩形筛选范式，`element_recognizer.py:436`，改判据为「墙面内、竖直细长或近方」）。
3. **按偏移映射到墙面**：洞口矩形的下边→`sill_m`（窗台/门槛标高），上边→`head_m`（窗顶/门顶标高），
   x 范围→轴号跨度，绑定到平面里对应轴跨的墙构件。
4. **檐口/女儿墙**：立面最高的水平轮廓线标高 → 建筑总高校验（与剖面顶标高互校）。

立面精度低于剖面，**定位为洞口 z 的补充源**，缺失时门窗按默认高度并标 `estimated`。

### 2.4 三视图配准（共享轴号 + 标注尺寸对齐）

这是把三张图「缝」到同一坐标系的关键，分**水平**与**竖直**两个方向：

**水平方向（XY 对齐）——直接复用现有能力**：
- 平面、剖面、立面共享同一套轴号。`element_recognizer._detect_axes`（`element_recognizer.py:201`）已能从三种图各自识别带标注的轴线，
  产出 `axes = {"x":[(label,pos_m)], "y":[...]}`。
- `model_elements.register_offset`（`model_elements.py:123`）已实现「以共有轴号位置差中位数平移对齐」。
  剖面/立面只有单方向轴号（横向），配准退化为一维——直接调用同一函数，用其返回的 `dx` 对齐横向轴跨。
- **匹配键 = 归一化轴号**（`_normalize_axis_label`，`element_recognizer.py:56`：③→'3'、'B'→'B'），三视图天然一致。

**竖直方向（z 对齐）——本设计新增**：
- 剖面 §2.2 拟合出的 z 标定线性映射，把每张剖面/立面的图面 y 标定到**统一的真实标高基准**（±0.000 为 0.0m）。
- 由于所有图共享 ±0.000 基准，跨图 z 直接可加可比，无需额外平移量——**标高本身就是绝对锚点**，比 XY 配准更简单。

**配准产物**：一个 `(building_unit, story_key) → 真实标高/层高/板厚/梁高` 的对照表，
按单体分组（沿用 `model_story` 的 `building_unit_key` 归一化，`model_story.py:160 detect_building_unit`）。

### 2.5 与既有平面配准链路的关系

现状 `model_elements.build_floor_elements`（`model_elements.py:188`）只在**平面之间**做轴号配准并合并构件。
本设计新增一条**正交的 z 通道**：剖面/立面不进入 `elements` 合并（它们不是平面构件源），
而是产出 `ElevationTable`/`SectionProfileTable`，在 §4 的集成点回填到楼层与构件的 z 尺寸。

---

## 3. 数据结构设计（输出契约）

新增模块 `apps/api/core/model3d/z_recovery/types.py`，全部 `@dataclass(frozen=True)`、类型注解、米单位。

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

ZSource = Literal["measured", "estimated", "default"]

@dataclass(frozen=True)
class ElevationEntry:
    """单层真实标高恢复结果。"""
    building_unit_key: str          # 单体（沿用 model_story 归一化）
    story_key: str                  # 楼层 key（F1/B2/RF，沿用 floor_parser）
    story_order: int
    elevation_m: float              # 本层底标高（±0.000 基准，米）
    height_m: float                 # 本层层高（上层底 − 本层底，米）
    source: ZSource                 # measured=剖面锚定 / estimated=部分推断 / default=常量兜底
    confidence: float               # 0~1
    evidence: dict = field(default_factory=dict)  # {section_drawing_id, y_fit_residual, tie_points}

@dataclass(frozen=True)
class ElevationTable:
    """一个单体的楼层标高表（跨视图恢复的主产物）。"""
    building_unit_key: str
    entries: tuple[ElevationEntry, ...] = ()
    datum_elevation_m: float = 0.0  # ±0.000 对应的真实标高（默认 0）
    provenance: dict = field(default_factory=dict)

@dataclass(frozen=True)
class SectionProfile:
    """单类构件的截面/厚度恢复结果（替换硬编码常量）。"""
    element_kind: Literal["beam", "slab", "column", "pipe", "equipment"]
    story_key: str | None           # None=全楼通用
    dimension_key: Literal["depth", "thickness", "height", "dia"]
    value_m: float
    source: ZSource
    confidence: float
    evidence: dict = field(default_factory=dict)  # {section_drawing_id, label_text}

@dataclass(frozen=True)
class SectionProfileTable:
    """构件截面表（按单体聚合，供 model_builder 回填）。"""
    building_unit_key: str
    profiles: tuple[SectionProfile, ...] = ()
    provenance: dict = field(default_factory=dict)

@dataclass(frozen=True)
class ZRecoveryResult:
    """跨视图 z 恢复总输出（一个项目）。"""
    elevation_tables: tuple[ElevationTable, ...] = ()
    section_tables: tuple[SectionProfileTable, ...] = ()
    view_classification: dict = field(default_factory=dict)  # drawing_id → view_kind
    cross_view_matched: bool = False   # 是否达成有效跨视图配准（点亮 LOD gate 的依据）
    issues: tuple = ()                 # 复用 model_story.ModelQualityIssue 风格
```

**设计要点**：
- 每个 z 量都带 `source` + `confidence` + `evidence`，满足「绝不把常量伪装成测量」的硬约束（§5）。
- `ElevationEntry.height_m` 与 `SectionProfile.value_m` 直接对应要替换的 `model_story.StoryLevel.height_m`
  与 `element_recognizer` 里的 `depth/thickness/dia/height` 常量。
- `cross_view_matched` 是点亮 `model_lod.cross_view_match` gate 的唯一真值来源（§4.3）。

---

## 4. 与现有代码的集成点

### 4.1 扩展 `cross_drawing.py`

现状 `core/ai_review/cross_drawing.py:169 analyze_batch` 是**纯 SQL + Python 聚合**、只做平面间关联（重复图号/接口缺图/问题聚类）。
按 AI_READING_TO_3D_MODEL.md:124「复用扩展 cross_drawing.py」的指示，**不污染现有 SQL 聚合**，
而是在同目录新增 `core/ai_review/cross_view_z.py`，暴露：

```python
async def recover_z(db, project_id: str, file_getter) -> ZRecoveryResult: ...
```

它做的事：拉取项目图纸 → §2.1 判图种 → 对 section/elevation 图跑 §2.2/§2.3 识别 →
§2.4 配准 → 产出 `ZRecoveryResult`。`analyze_batch` 保持不变，二者是「平面聚合」与「z 恢复」两条并列分析线。
（命名与目录延续 cross_drawing 家族，满足「扩展 cross_drawing」的意图而不破坏其单一职责。）

### 4.2 替换 `model_builder` / `element_recognizer` 硬编码常量

回填在 `model_builder.build_scene`（`model_builder.py:826`）的组装阶段进行，**在 `_apply_real_elevations` 之后接一个 z 回填步骤**：

1. **层高/标高**：`ZRecoveryResult.elevation_tables` → 覆盖 `floor["elevation_m"]` 与 `floor["height_m"]`，
   并回填 `model_story.StoryLevel.height_m`（见 §4.5）。当前 `_apply_real_elevations`（`model_builder.py:678`）
   仅从平面散落标高贪心选值——改为**优先采用剖面锚定的 `elevation_table`，平面候选降为二级来源**。
2. **梁高/板厚**：新增 `services/model_builder` 回填函数，把 `SectionProfileTable` 的 `beam_depth_m`/`slab_thickness_m`
   注入 `floor["elements"]["beams"][*]["depth"]` 与 `["slabs"][*]["thickness"]`，覆盖 `element_recognizer.py:132/376/383`
   写入的默认 `0.6/0.12`。
3. **常量降级为「兜底默认」而非「硬编码真值」**：`element_recognizer` 里的 `depth=0.6` 等保留，但输出时附带
   `z_source="default"`；一旦 `SectionProfileTable` 有 measured 值即被覆盖为 `z_source="measured"`。
   前端据 `z_source` 用不同视觉（measured 实色 / estimated 半透明 / default 虚线描边）表达置信度。

> **不做破坏性删除**：常量仍是缺剖面时的合法兜底（§5），只是从「唯一真值」降级为「显式标注的估算」。

### 4.3 点亮 `model_lod` 的 `cross_view_match` gate

现状：`model_lod.py:11-18` 的 `LOD300_GATES` 含 `"cross_view_match"`，gate 判定在 `model_lod.py:137-138`
读 `scope.has_cross_view_match`；但 `model_builder._collect_scope_lod_evidence`（`model_builder.py:778-809`）
里 `cross_view_match` 恒初始化为 `False` 且**没有任何代码把它置 True**——所以这个 gate 目前是**死的**，
LOD300 永远差这一票。

**点亮方式**：在 `_collect_scope_lod_evidence` 增加一路证据源——把 `ZRecoveryResult.cross_view_matched`
（以及该 scope 下是否存在 measured 的 elevation/section）并入：

```python
# model_builder.py _collect_scope_lod_evidence 内新增
if z_result_for_scope.cross_view_matched:
    evidence["cross_view_match"] = True
```

判定 `cross_view_matched=True` 的条件（在 `cross_view_z.recover_z` 内计算）：
该单体**至少有 1 张剖面图成功拟合 z 标定线（≥2 tie points、残差达标）**，
**且**其标高表与平面楼层序（`model_story` 的 `story_order`）能对上（层数一致、单调）。
只有真发生了跨视图对齐才置真——不满足则保持 False，LOD 诚实停在 200。

### 4.4 数据库迁移

新增迁移 `migrations/014_cross_view_z.sql`：
- `drawings` 增 `view_kind text`（plan/section/elevation/unknown，§2.1 判别结果）。
- 新增 `model_z_recovery`（可选缓存表）：`project_id, building_unit_key, elevation_table jsonb, section_table jsonb,
  cross_view_matched bool, generated_at`——避免每次 `build_scene` 重跑重识别；或直接内嵌 scene JSON 不落表（MVP 走内嵌）。

### 4.5 回填 `model_story` 的 `height_m`

`model_story.normalize_story_table`（`model_story.py:278`）目前对 `StoryLevel.height_m` 一律填默认
（`model_story.py:416`：`DEFAULT_BASEMENT_HEIGHT_M if order<0 else DEFAULT_STORY_HEIGHT_M`）。
集成方案：给 `normalize_story_table` 增加可选入参 `z_overrides: dict[(unit_key, story_key)] -> height_m`，
存在测量值时用之，否则维持默认——**保持函数纯度与向后兼容**（不改签名默认行为）。
`build_scene` 在拿到 `ZRecoveryResult` 后构造 `z_overrides` 传入。

---

## 5. 置信度与降级（诚实性硬约束）

> 对应 AI_READING_TO_3D_MODEL.md:128「缺剖面时**显式回落默认值并标注估算**，绝不把常量伪装成真实测量」
> 与 AI_READING_TO_3D_MODEL.md:148「z 恢复没有免费午餐……标注不全时必须显式降级为估算并标置信度」。

### 5.1 三级来源标记（贯穿所有 z 量）

| `source` | 含义 | 触发条件 | 前端表达 |
|----------|------|---------|---------|
| `measured` | 剖面/立面标注锚定的真实测量 | z 标定线拟合成功 + tie points ≥2 + 残差达标 | 实色，标「实测」 |
| `estimated` | 部分证据推断（如只有个别标高、由层高外推） | 标注不全、单 tie point、跨视图部分对齐 | 半透明，标「估算」 |
| `default` | 常量兜底 | 无剖面 / 拟合失败 / 图种判不出 | 虚线描边，标「默认 4.5m」等 |

### 5.2 置信度计算

沿用 `model_lod._confidence_for`（`model_lod.py:146-149`）「baseline + 覆盖率取大」的思路：

```
elevation confidence = base(source) × fit_quality × story_match
  base:  measured 0.9 / estimated 0.6 / default 0.25
  fit_quality: 1 − 归一化拟合残差（measured 时生效）
  story_match: 标高表层数与 model_story 层序一致度（0~1）
section confidence = base(source) × label_clarity（标注文本清晰度）
```

标高冲突（同层多值、非单调）时降 confidence 并发 `ModelQualityIssue`
（复用 `model_story.ModelQualityIssue`，`model_story.py:66`，issue_type 如 `z_elevation_conflict`），
校正逻辑对齐现有 `story_spacing_too_small`（`model_story.py:389-406`）的「按默认层高校正 + 记 issue」范式。

### 5.3 Provenance 记录

每个 `ElevationTable`/`SectionProfileTable` 的 `provenance` 按 `model_lod._build_provenance`（`model_lod.py:152`）风格记：
`{source_drawings:[...], view_kinds_used:[...], tie_point_count, fit_residual, datum_note:"±0.000 assumed as 0.0m"}`。
使前端与人工审校工作台（`SemanticReviewQueue.tsx` / `DrawingAnnotationQueue.tsx`）可追溯每个 z 量的由来。

---

## 6. 分步实现计划

### MVP —— 仅剖面标高（点亮 z 主通道）

- **输入**：项目图纸中被判为 section 的图。
- **产出**：`ElevationTable`（每层 `elevation_m` + `height_m`，measured/default 标记）；`cross_view_matched` 计算；
  回填 `floor.height_m` 与 `model_story.height_m`；点亮 `cross_view_match` gate。
- **不做**：立面洞口、梁高/板厚截面（仍走常量但标 `default`）。
- **验收标准**：一张标注规范的剖面图，能正确产出楼层标高序列（±0.000 锚定、层高误差 <5%）；
  无剖面项目行为与现状完全一致（`height_m` 默认、gate 仍 False，无回归）。

### 步骤 2 —— 剖面截面（梁高/板厚）

- 扩展 §2.2 步骤 5/6，产出 `SectionProfileTable`；回填 `beams[*].depth` / `slabs[*].thickness`。
- **验收标准**：剖面/配筋图有截面标注时，梁高/板厚为实测值并标 measured；无标注回落 0.6/0.12 并标 default。

### 步骤 3 —— 立面洞口

- §2.3：识别门窗洞口 → `sill_m`/`head_m`，绑定到平面墙构件。
- **验收标准**：立面标注规范时洞口标高可用；缺立面时门窗按默认并标 estimated。

### 步骤 4 —— 全三视图配准闭环

- §2.4 完整二维对齐 + 一致性互校（剖面顶标高 vs 立面檐口 vs 平面层数三方交叉验证）。
- **验收标准**：三视图齐备的单体，标高/层高/截面达「算量可用精度」（对齐 MODEL_PRECISION_BLUEPRINT 验收口径）；
  冲突显式发 `ModelQualityIssue` 并降级，进入人工审校队列。

---

## 7. 测试策略

遵循项目 TDD 与 80% 覆盖要求（`~/.claude/rules/common/testing.md`）。测试放 `apps/api/tests/`。

### 7.1 合成图纸夹具（确定性、可复现）

无需真实 PDF——直接构造 `DrawingGeometry`（`core/model3d/types.py:12`）喂给识别器，绕开渲染：

```python
def make_section_geom(tie_points: list[tuple[float, float]]) -> DrawingGeometry:
    """构造带 N 条标高线 + 标高文本的剖面几何。
    tie_points: [(y_pt, elevation_m)] —— 标高线 y 坐标与其应绑定的标高值。
    """
    lines = [(50, y, 400, y) for y, _ in tie_points]        # 水平标高线
    texts = [(410, y, f"{ev:+.3f}") for y, ev in tie_points]  # ▽ 标高文本
    return DrawingGeometry(page_w=500, page_h=800, lines=lines, texts=texts)
```

单测覆盖：z 标定线拟合正确性、标高→楼层聚类、层高计算、板厚双线换算、梁截面正则抽取、
三视图轴号配准（复用 `model_elements.register_offset` 已有测试模式）。

### 7.2 边界用例（必测）

| 用例 | 期望行为 |
|------|---------|
| **无剖面**（项目全是平面） | `elevation_tables` 空、`cross_view_matched=False`、height_m 全 default、无回归、无异常 |
| **标高冲突**（同层两个不同标高值 / 非单调序列） | 降 confidence、发 `z_elevation_conflict` issue、按默认层高校正（对齐 `model_story.py:389`） |
| **轴号缺失**（剖面无可识别轴号） | 横向配准退化，仅用 z 标定；若连标高文本都无 → source=default |
| **单 tie point**（只有 ±0.000 一个标高） | 无法拟合线性映射 → source=estimated（用默认层高外推）+ 低 confidence |
| **图种判不出** | view_kind=unknown，不参与 z 恢复，不污染结果 |
| **超大图/识别超时** | 沿用 `model_elements._RECOGNIZE_TIMEOUT_SEC`（`model_elements.py`）降级返回空，绝不中断 build_scene |
| **DXF 毫米模型空间** | 竖直 z 标定尺与 `_detect_scale` 的 DXF 分支（`element_recognizer.py:166`）互校一致 |

### 7.3 集成测试

- `build_scene` 端到端：注入含剖面的合成项目，断言 scene JSON 里 `floors[*].height_m` 为实测、
  `lod_capabilities` 中 `cross_view_match` gate 通过、`stats` 出现 z 恢复统计。
- 回归测试：现有无剖面项目的 scene 输出逐字段不变（保护 schema_version=2 契约）。

---

## 8. 风险与开放问题（诚实清单）

1. **剖面标注质量强依赖（最大风险）**：z 恢复的天花板由图纸剖面/立面的标注规范度决定。
   标高符号 `▽` 与标高文本的绑定容差、标高线是否画满、配筋图截面标注格式差异——任一不规范即退化为 estimated/default。
   **缓解**：三级来源标记 + 人工审校工作台兜底，机器出初模、人审闭合（对齐三审文化）。
2. **斜屋面 / 坡道**：标高非水平线，线性 z 标定假设失效。MVP 不处理，识别为「非水平标高线」时该区域标 estimated。
3. **错层 / 夹层 / 跃层**：同一「楼层」内存在多个标高，破坏「一层一标高」假设。
   `ElevationTable` 用 `story_order` 排序可容纳多 entry，但与 `model_story` 单层单标高模型的映射需专门处理（开放问题）。
4. **z 标定尺与平面比例尺不一致**：剖面竖直比例可能与平面水平比例不同（如剖面纵向放大）。
   必须独立拟合剖面自身的 z 标定线，不能复用平面 `_detect_scale`——已在 §2.2 步骤 3 明确，但需实测验证互校阈值。
5. **±0.000 基准假设**：默认 ±0.000 = 绝对 0.0m。若项目用绝对高程（如黄海高程 +45.300 为 F1），
   datum 需从总平面/说明另取。MVP 假设相对基准并在 provenance 显式标注 `datum_note`。
6. **剖面与平面的单体归属对齐**：一张剖面可能跨多个单体（南区+北区通剖），
   `building_unit_key` 归属靠 `model_story.detect_building_unit`（`model_story.py:160`）的图名匹配，多单体通剖是开放难点。
7. **门窗洞口→墙构件绑定**：立面洞口的 x 轴跨与平面墙的 axis span 对齐是几何匹配，
   墙识别本身是近似（`_find_parallel_pairs`，`element_recognizer.py:314`），绑定误差会累积。
8. **VLM 判图种的成本与延迟**：关键词+几何应覆盖绝大多数；VLM 兜底需控调用量（仅 unknown 才触发），
   经 `ModelRouter`（CLAUDE.md 模型路由层）走 `drawing_visual_analyzer` 引擎，受断路器保护。

---

## 附：关键代码锚点速查

| 关注点 | 文件:行 |
|--------|---------|
| 现有平面间跨图聚合（待并列扩展） | `core/ai_review/cross_drawing.py:169` |
| 轴网+轴号识别 | `core/model3d/element_recognizer.py:201 _detect_axes` |
| 轴号归一化/排序 | `element_recognizer.py:56 _normalize_axis_label` |
| 标高正则抽取（待扩展为 z 标定） | `element_recognizer.py:75 extract_elevations` |
| 坐标换算上下文 | `element_recognizer.py:141 _Ctx` |
| 比例尺检测 | `element_recognizer.py:159 _detect_scale` |
| 硬编码梁高 depth=0.6 | `element_recognizer.py:132` |
| 硬编码板厚 thickness=0.12 | `element_recognizer.py:376,383` |
| 共有轴号配准（推广到剖面） | `services/model_elements.py:123 register_offset` |
| 平面构件配准合并 | `model_elements.py:188 build_floor_elements` |
| 楼层标高贪心选择（待被剖面锚定替换） | `services/model_builder.py:678 _apply_real_elevations` |
| LOD 证据收集（cross_view_match 恒 False） | `model_builder.py:778 _collect_scope_lod_evidence` |
| LOD gate 定义 | `services/model_lod.py:11-18, 137-138` |
| provenance 记录范式 | `model_lod.py:152 _build_provenance` |
| 默认层高常量 | `services/model_story.py:12-13` |
| StoryLevel.height_m 默认填充 | `model_story.py:416` |
| 楼层解析 | `services/floor_parser.py:89 parse_floor` |
| 质量 issue 范式 | `model_story.py:66 ModelQualityIssue` |
