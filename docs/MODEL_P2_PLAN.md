# 工程 3D 模型 P2 落地方案 — 冲 LOD300(算量级)

> 版本 V1.0 ｜ 2026-07-12 ｜ 承接 `docs/MODEL_EVAL_SGOH.md`(P0/P1 + sprawl 已修复并提交)
>
> 目标:把模型从当前 **LOD200** 推进到 **LOD300(算量级)**,让 QTO 可作结算依据。
> 本方案基于对真实代码的通读(`model_lod.py` / `model_builder.py` / `section_z_recovery.py` /
> `element_recognizer.py` / `model_topology.py`),给出每个门槛的现状、鲁棒化策略、代码位置、
> 风险与验收,并排定实施顺序。

---

## 0. 背景:LOD300 需要什么

`model_lod.py` 定义:一个 scope 达 LOD300 需 **6 项证据门槛全部通过**(`LOD300_GATES`):

| 门槛 | 含义 | 证据来源(代码) | 当前状态 |
|---|---|---|---|
| `scale` | 可靠比例尺 | `element_recognizer._detect_scale` → 逐层 `_lod_evidence` | ❌ 缺失 |
| `registered_grid` | 跨图轴网配准 | `_detect_axes` + `model_elements.register_offset` | ❌ 缺失 |
| `dimensions` | 尺寸标注 | 逐层 `_lod_evidence`(尺寸提取) | ❌ 缺失 |
| `cross_view_match` | 平面↔剖面对齐 | `section_z_recovery` → `matched_units` | ❌ 缺失 |
| `stable_component_boundaries` | 构件边界一致 | `model_topology.build_topology_graph().lod_evidence()` | 部分 |
| `geometry_consistent` | 几何一致 | 同上(拓扑) | 部分 |

证据在 `build_scene` 里经 `_build_model_scopes(...)` 拼成 `ModelScopeEvidence`,再
`evaluate_lod_capability` 判级。**只要把这些证据在真实数据上真实置真,LOD 自动升到 300**——
关键是让现有(Phase B 在合成数据上验证的)检测在 2309 张真实脏图上**鲁棒生效**。

---

## 1. 现状根因(为什么每项都不过)

现有模块对**合成数据**够用,对真实竣工图太脆:

- **cross_view_match**:`section_z_recovery._marks_align_stories` 要求剖面标高数 ∈
  [楼层数, 楼层数+2] 且严格单调。真实歌剧院 13 层多分区,单张剖面抽出的标高集几乎不可能
  正好匹配 → 一律回落默认 4.5m(实测层高即为均匀 0/4.5/9…,证明从未匹配)。
- **scale**:机电图比例检测出错(P1 已见:管线冲到 2416m),说明 `_detect_scale` 在很多图上
  失败或误判 → scale 证据不可信 → 门槛不给过。
- **registered_grid**:`register_offset` 按**共有轴号标签**对齐;多分区(北区/南区/A-D区)用
  独立轴网、无共有标签 → 配准不成立。
- **dimensions**:真实图尺寸标注样式多样,当前提取覆盖不足。
- **stable_component_boundaries / geometry_consistent**:依赖拓扑闭合;构件识别不全时不稳。

---

## 2. 分门槛落地方案(按价值/可行性排序)

### 阶段 A — cross_view_match(剖面真实层高)【最高价值,先做】

**价值**:点亮 1 个 LOD300 门槛 + 给出**真实层高/标高**(替代默认 4.5m),直接提升体量与算量真实性。

**改造点**:`services/section_z_recovery.py`
1. **放宽对齐**:不再要求标高数≈楼层数的强绑定。改为**最近邻配准** —— 把剖面标高序与平面
   楼层序按标高就近配对(动态规划/贪心最小残差),允许剖面标高数 > 楼层数(取结构楼面标高子集)。
2. **鲁棒筛标高**:剖面抽出的标高先过滤(去重、去女儿墙/设备夹层噪声、单调化),用 RANSAC/
   中位数残差挑出"主楼面标高序"。
3. **置信度门槛**:配对残差 < 阈值(如 0.5m)且覆盖 ≥70% 楼层才置 `matched`,否则回落默认
   (保持"绝不虚高 LOD"原则)。
4. **单体级**:按 `detect_building_unit` 分单体,各单体独立匹配(多分区各自有剖面)。

**代码位置**:`_marks_align_stories`(放宽)、`_assign_overrides`(最近邻配对)、
`section_level_extractor`(标高抽取鲁棒化,去噪)。

**验收**:歌剧院模型层高非均匀(出现真实层高如 5.4/6.0m 等),`cross_view_match` 点亮,
且缺剖面单体仍回落默认(estimated=true)。

**风险**:标高误配 → 层高错乱。缓解:高置信门槛 + 残差校验 + 回落兜底。

---

### 阶段 B — scale(鲁棒比例尺)【sprawl 根治,高价值】

**价值**:比例尺对了,坐标才对,P1 的离群裁剪就从"补救"变"根治";点亮 scale 门槛。

**改造点**:`core/model3d/element_recognizer._detect_scale`
1. **多源比例尺**:图签比例文字(1:100)→ DXF 毫米空间 → 轴距反推(已知标准柱距/轴距)→
   尺寸标注值/像素比。多源投票,取一致者。
2. **合理性校验**:换算后构件尺寸落在工程合理区间(柱 0.3~2m、层高 3~6m、轴距 6~12m),
   否则判为比例错误 → 该图不参与几何(或标低置信)。
3. **图幅级一致**:同套图(同专业同区)比例应一致,用众数纠个别离群图。

**代码位置**:`element_recognizer._detect_scale`(第 ~174 行)、`_Ctx`(坐标换算)。

**验收**:管线等不再冲到千米(比例错误图被识别并纠正/剔除),scale 证据置真。

**风险**:误纠正真实大跨结构。缓解:合理性区间放宽 + 保留原值兜底。

---

### 阶段 C — registered_grid(跨图/跨区轴网配准)【最硬,核心】

**价值**:多分区对齐到共同轴网 → 真实空间关系,彻底解决"挤在一起";点亮 registered_grid。

**改造点**:`core/model3d/grid_anchor_extractor` + `services/cross_view_registration` +
`model_elements.register_offset`
1. **轴网锚点提取**:每图抽轴线 + 轴号(①②③ / A/B/C),形成 (轴号→坐标) 锚点集。
2. **跨图配准**:
   - 同区图:按共有轴号最小二乘配准(已有 `register_offset`,鲁棒化:RANSAC 抗错标)。
   - 跨区图:分区各自成独立坐标系;**需站点级总图/总平面**提供分区相对位置锚点。
     无总图时:退化为"各区独立摆放 + 人工/半自动指定相对位置"(不硬造)。
3. **全局原点**:选锚点最全的图为项目参考系,其余配准到它。

**代码位置**:`grid_anchor_extractor`、`cross_view_registration.register_views`、
`model_elements.register_offset`(RANSAC 鲁棒化)。

**验收**:同区各层轴网对齐(共有轴号坐标一致);registered_grid 置真。跨区在有总图时对齐,
无总图时诚实标注"分区未配准"。

**风险**:最高。多分区无总图时无法真配准 —— 须如实标注,不虚高。

---

### 阶段 D — dimensions【中等】

**改造点**:尺寸标注提取(`element_recognizer` 尺寸链/标注文字)。抽取梁跨、板厚、柱截面等
标注值,与几何量交叉校验。**价值**:点亮 dimensions,并给 QTO 提供标注级真值。

---

### 阶段 E — stable_component_boundaries / geometry_consistent【随 A~D 自然改善】

依赖构件识别完整度与拓扑闭合。阶段 A~D 提升识别质量后,`model_topology.lod_evidence()` 的
这两项会自然改善。补充:板/梁识别增强(P1 已起头)、拓扑闭合率提升。

---

## 3. 实施顺序与里程碑

| 阶段 | 门槛 | 价值 | 难度 | 里程碑 |
|---|---|---|---|---|
| A | cross_view_match | 高(真实层高) | 中 | 层高非均匀 + gate 点亮 |
| B | scale | 高(根治sprawl) | 中 | 比例错误图被纠/剔,坐标真实 |
| C | registered_grid | 高(多分区对齐) | **高** | 同区轴网对齐;跨区如实标注 |
| D | dimensions | 中 | 中 | 标注级真值入 QTO |
| E | boundaries/consistent | 中 | 低(随附) | 拓扑闭合率提升 |

**达 LOD300 判据**:某单体 6 门槛全通过 → 该 scope 升 300;整模型按 scope 分别评级(现有机制
已支持,`_build_model_scopes` per-scope)。**歌剧院预期**:结构主体较易达 A/B/E;C(多分区)
是能否整体 300 的关键,可能需总图或部分保持 200(诚实分级)。

---

## 4. 验证策略(应对本机 docker build 失效)

现状:本机 `docker compose build` 空操作、只能 `docker cp`。P2 迭代频繁,建议:

1. **单元/纯函数优先**:阶段 A/B 的核心(section_z 配准、scale 检测)都是纯函数,可脱离整栈
   用 pytest + 真实图导出的几何做**离线验证**(像本轮诊断那样,`docker exec ... python3` 跑
   函数),无需 10 分钟整模型重建。
2. **小样验证**:抽 1 个分区、几张图先验证配准/层高,再全量重建。
3. **部署环境迭代**:在 docker build 正常的环境做 P2(提交代码自动打包),避免 docker cp 漂移。
4. **每阶段回归**:LOD 证据是"绝不虚高"设计,回落兜底完善 → 改坏了会降级而非虚高,便于发现。

---

## 5. 工作量与建议

- **阶段 A(剖面层高)**:~0.5–1 天,单点见效,建议先做。
- **阶段 B(比例尺)**:~1–2 天,根治 sprawl。
- **阶段 C(多分区配准)**:**最大不确定性**,有总图 ~2–3 天,无总图则需产品决策(半自动摆放
  或保持分区独立);这是 P2 能否整体达 300 的关键瓶颈。
- **阶段 D/E**:~1–2 天,随附改善。

**总体**:P2 是一个 **~1 周级的专项工程**(不含 C 的总图不确定性),应作为独立任务、在
docker 正常的环境推进,按 A→B→C→D→E 顺序,每阶段离线验证纯函数 + 小样重建。

**建议先做阶段 A**(剖面真实层高):价值高、边界清、可离线验证、风险可控,是 P2 的最佳切入点。

---

## 附:关键代码位置速查

- LOD 判级:`services/model_lod.py`(`LOD300_GATES` / `evaluate_lod_capability`)
- 证据拼装:`services/model_builder.py`(`_build_model_scopes` / `build_scene` 1078–1134)
- 剖面 z:`services/section_z_recovery.py`(`_marks_align_stories` / `_assign_overrides`)、
  `core/model3d/section_level_extractor.py`(标高抽取)
- 比例尺/轴网:`core/model3d/element_recognizer.py`(`_detect_scale` / `_detect_axes`)、
  `core/model3d/grid_anchor_extractor.py`
- 跨视图配准:`services/cross_view_registration.py`、`model_elements.register_offset`
- 拓扑证据:`core/model3d/topology_rules.py`、`model_topology.build_topology_graph().lod_evidence()`
