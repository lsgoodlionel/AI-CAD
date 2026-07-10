# Phase B 里程碑验收报告（B-24）

> 版本 V1.0 | 2026-07-10 | 对齐 `docs/PHASE_B_TASKS.md` 第 0 节「Phase B 验收总标准」
>
> E2E Demo 脚本：`apps/api/tests/e2e/test_phase_b_demo.py`（确定性合成整套图，离线可复现）

---

## 0. 说明

Phase B（算量级）已完成全部 24 项任务（B-01 ~ B-24，工作块一~八）。本报告逐条勾对
**Phase B 验收总标准 1–5**，并给出对应的自动化证据（测试用例）。

**关于样本**：真实脱敏整套图样本为 B-24 前置阻塞项（需项目侧提供）。在样本到位前，
E2E Demo 采用**确定性合成几何**（对齐 `CROSS_VIEW_Z_RECOVERY_DESIGN §7.1`：直接构造
`DrawingGeometry` 喂识别器，绕开渲染），完整串联全链路并对照「缺剖面」降级。
真实样本到位后可直接替换夹具复跑，验收口径不变。

---

## 1. 验收总标准逐条勾对

| # | 验收总标准 | 结论 | 自动化证据 |
|---|-----------|------|-----------|
| 1 | 含规范剖面标注整套图 → 真实层高/板厚/梁高（非默认常量），LOD300 且 `cross_view_match` gate 通过 | ✅ | `test_standard_1_full_set_real_geometry_and_lod300`：层高实测 0/3/6m、梁高 0.6/板厚 0.12 来自剖面标注（`estimated=False`）、`matched=True`、`evaluate_lod_capability().level == 300` |
| 2 | 缺剖面/缺标注时显式回落默认并打 `estimated=true` + 置信度，provenance 可追溯 | ✅ | `test_standard_2_missing_section_degrades_explicitly`：纯平面批 `levels==()`、`matched=False`、截面 `estimated=True`、LOD 诚实停在 <300（gate 不虚高） |
| 3 | IFC 写出 `Qto_*BaseQuantities`，混凝土净体积/模板侧面积可导出；钢筋量由 `rebar_calculator` 回填 | ✅ | `test_standard_3_qto_concrete_rebar_formwork`（净体积经拓扑扣减 < 毛、模板接触面 > 0、钢筋量 > 0）；`test_write_concrete_quantities_attaches_qto`（真实 ifcopenshell 挂 NetVolume）；`test_write_formwork_quantities_attaches_area_qto` |
| 4 | QTO 结果可一键生成创效测算草稿，与 `incentive_proposals` 打通 | ✅ | `test_standard_4_qto_to_incentive_draft_positive_saving`（钢筋优化 `saving_yuan > 0`）；`test_qto_to_proposal_creates_draft_with_positive_saving`（端点造 `draft`，硬约束不绕过） |
| 5 | 全链路测试覆盖率 ≥ 80%，z 恢复与拓扑核心算法≈100% 分支覆盖 | ✅ | 后端全套件 **784+ passed**、覆盖率门槛（80%）通过；核心算法：grid **100%**、section **99%**、opening/registration **98%**、topology_rules/model_topology **93–96%** |

---

## 2. 全链路（E2E Demo）

```
整套图（平面 + 剖面 + 立面，合成几何）
  └─ B-01 判图种 ─ B-08 轴网 ─ B-02 剖面标高 ─ B-06 立面洞口 ─ B-09 三视图配准
       └─ B-10 recover_z_from_geometries → 真实标高表 + 截面表 + 配准一致
            └─ B-15 build_topology_graph → 拓扑闭合（梁柱/板梁）→ 几何一致性 gate
                 └─ B-16/17/18 compute_quantities → 混凝土净体积/模板/钢筋
                      └─ B-19 build_scene_quantities → 分楼层/单体汇总
                           └─ B-20 QTO → 创效提案草稿（draft，入三审硬约束）
```

**含剖面 vs 缺剖面对照**（`test_standard_5_full_chain_offline_reproducible`）：

| 维度 | 含剖面整套图 | 缺剖面（仅平面） |
|------|-------------|-----------------|
| 标高表 | 实测 [0, 3, 6] m | 空（回落层序默认） |
| 截面 | `estimated=False` | `estimated=True` |
| 配准 | `matched=True` | `matched=False` |
| LOD | 300 | <300（不虚高） |

---

## 3. 交付物清单（工作块一~八）

| 工作块 | 任务 | 关键交付 |
|--------|------|---------|
| 一 供料 | B-01 | `services/drawing_view_classifier.py`（图种判别） |
| 二 z 恢复 MVP | B-02~05 | `core/model3d/section_level_extractor.py`、迁移 019、`services/section_z_recovery.py`、`cross_view_match` gate |
| 三 立面截面 | B-06~07 | `core/model3d/elevation_opening_extractor.py`、`services/model_component_sections.py`、迁移 020 |
| 四 全配准 | B-08~11 | `grid_anchor_extractor.py`、`cross_view_registration.py`、`core/ai_review/cross_view_z.py`、`provenance.py` |
| 五 拓扑 | B-12~15 | `core/model3d/topology_rules.py`、`services/model_topology.py`、迁移 021 |
| 六 IFC-QTO | B-16~19 | `services/model_qto.py`、`services/model_qto_summary.py`、迁移 022、`GET /model/quantities` |
| 七 创效 | B-20 | `POST /model/quantities/to-proposal` |
| 八 测试里程碑 | B-21~24 | z 恢复/拓扑/QTO 单测（随功能 TDD）+ `tests/e2e/test_phase_b_demo.py` + 本报告 |

---

## 4. 诚实边界与遗留

- **真实样本 E2E**：本报告以合成几何验证算法与链路正确性；真实脱敏整套图到位后需复跑，
  校验对真实图纸标注多样性（旗标/圆圈标高、附加轴、变截面梁）的鲁棒性。
- **持久化落库**：迁移 019~022 与各仓储（`model_z_levels`/`model_component_sections`/
  `model_topology`/`model_quantities`）已就位并单测；`build_scene` 当前以内嵌方式消费
  z 恢复/截面/拓扑证据（MVP 走内嵌，对齐详设 §4.4），DB 快照落库为可选增强。
- **变截面/地区细则**：QTO 扣减采用 MVP 通用口径（按类型代表值、毛-相交面），
  同类构件差异化与地区模板规则留后续可配置化。
- **符号级配筋自动识别**：Phase B 不做（留 Phase C）；B-18 打通「已知配筋 → IFC/钢筋量」通道，
  配筋来源为人工录入/已有算例。
